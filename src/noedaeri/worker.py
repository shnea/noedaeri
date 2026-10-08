import json
import logging
import os
import signal
import sys
import time
from pathlib import Path
from uuid import UUID, uuid4

import httpx

from .compute import Compute
from .config import Settings
from .db import Database
from .execution import execution_lock
from .media import (
    JobCancelled,
    MediaError,
    native_compute_slot,
    run_process,
    thumbnail,
    video_package,
)
from .storage import Storage
from .stt import transcribe
from .tts import normalize_reference, synthesize


def run():
    settings = Settings.from_env()
    storage = Storage(settings)
    compute = Compute(Database(settings.database_url), storage.root)
    worker_id = uuid4()
    stopping = False

    def stop(signum, frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with httpx.Client(
        base_url=os.environ["WORKER_API_ORIGIN"],
        timeout=5,
        headers={"Authorization": "Bearer " + settings.worker_key},
    ) as client:
        while not stopping:
            try:
                response = client.post(
                    "/internal/claim",
                    json={
                        "worker_id": str(worker_id),
                        "kinds": ["video.thumbnail", "video.package", "image.package"]
                        + (["tts.synthesize", "tts.voice.register"] if settings.tts_enabled else [])
                        + (["stt.transcribe"] if settings.stt_enabled else []),
                    },
                )
                response.raise_for_status()
                job = response.json()
                if not job:
                    time.sleep(1)
                    continue
                lease = {"token": job["lease_token"]}
                last_beat, valid, cancelled = 0.0, True, False
                budget = {"bytes": 0}

                def alive(active_job=job, active_lease=lease):
                    nonlocal last_beat, valid, cancelled
                    if stopping:
                        return False
                    if time.monotonic() - last_beat > 2:
                        try:
                            beat = client.post(
                                f"/internal/jobs/{active_job['id']}/heartbeat", json=active_lease
                            )
                            valid = beat.status_code == 200
                            cancelled = valid and beat.json()["cancel_requested"]
                        except httpx.HTTPError:
                            valid = False
                        last_beat = time.monotonic()
                    return valid and not cancelled

                def stage(value, active_lease=lease):
                    nonlocal last_beat
                    active_lease["stage"] = value
                    last_beat = 0
                    if not alive():
                        raise JobCancelled()

                def reserve(amount, active_job=job, active_lease=lease, active_budget=budget):
                    nonlocal valid
                    try:
                        response = client.post(
                            f"/internal/jobs/{active_job['id']}/reserve",
                            json={**active_lease, "bytes": amount},
                        )
                    except httpx.HTTPError:
                        valid = False
                        raise JobCancelled() from None
                    if response.status_code == 507:
                        raise MediaError("storage_capacity_exceeded")
                    if response.status_code != 200:
                        valid = False
                        raise JobCancelled()
                    active_budget["bytes"] = amount

                def capacity_alive(active_job=job, active_budget=budget):
                    if not alive():
                        return False
                    if not storage.available(None):
                        raise MediaError("storage_capacity_exceeded")
                    if active_budget["bytes"]:
                        folder = storage.path(
                            "results", UUID(active_job["id"]), "thumbnail.jpg"
                        ).parent
                        if (
                            sum(p.stat().st_size for p in folder.glob("*") if p.is_file())
                            > active_budget["bytes"]
                        ):
                            raise MediaError("storage_capacity_exceeded")
                    return True

                with execution_lock(storage.root, UUID(job["id"])):
                    status, code, result = "succeeded", None, None
                    try:
                        if not alive():
                            raise JobCancelled()
                        job_id = UUID(job["id"])
                        source = storage.path("uploads", job_id, "input")
                        output = storage.path("results", job_id, "thumbnail.jpg")
                        with compute.ticket(
                            "media", UUID(job["id"]), job["kind"], UUID(job["owner_id"])
                        ) as compute_lease:
                            deadline = time.monotonic() + settings.native_wait
                            stage("waiting_compute")
                            while not compute_lease.try_start():
                                if not alive():
                                    raise JobCancelled()
                                if time.monotonic() >= deadline:
                                    raise MediaError("compute_wait_timeout")
                                time.sleep(0.2)

                            def compute_alive():
                                return compute_lease.alive() and capacity_alive()

                            with native_compute_slot(
                                storage.root,
                                compute_alive,
                                stage,
                                max(0, deadline - time.monotonic()),
                            ):
                                result = execute_job(
                                    settings,
                                    storage,
                                    job,
                                    client,
                                    lease,
                                    source,
                                    output,
                                    compute_alive,
                                    stage,
                                    reserve,
                                )
                        if not capacity_alive():
                            raise JobCancelled()
                    except JobCancelled:
                        status = "cancelled"
                    except MediaError as error:
                        status, code = "failed", str(error)
                    except (OSError, ValueError):
                        status, code = "failed", "worker_failed"
                    if valid:
                        response = client.post(
                            f"/internal/jobs/{job['id']}/finish",
                            json={**lease, "status": status, "error_code": code, "result": result},
                        )
                        response.raise_for_status()
            except BlockingIOError:
                time.sleep(1)
            except httpx.HTTPError:
                logging.warning("Worker connection failed; retrying")
                time.sleep(2)


def execute_job(settings, storage, job, client, lease, source, output, alive, stage, reserve):
    result = None
    if job["kind"] == "image.package":
        reserve(8_000_000)
        stage("image_processing")
        raw = run_process(
            [
                sys.executable,
                str(Path(__file__).with_name("images.py")),
                str(source),
                str(output.parent),
                job["input"]["extension"],
            ],
            60,
            alive,
            capture=True,
        )
        result = json.loads(raw)
        if "error" in result:
            raise MediaError(result["error"])
    elif job["kind"] == "video.package":
        result = video_package(
            source,
            output.parent,
            job["options"]["seconds"],
            settings.video_timeout,
            alive,
            stage,
            reserve,
            encoder=settings.video_encoder,
            resource_root=storage.root,
        )
    elif job["kind"] == "video.thumbnail":
        reserve(2_097_152)
        thumbnail(
            source,
            output,
            job["options"]["seconds"],
            settings.job_timeout,
            alive,
        )
    elif job["kind"] == "stt.transcribe":
        result = transcribe(settings, storage, job, alive, stage, reserve)
    else:
        profile = client.get(f"/internal/jobs/{job['id']}/voice", params={"token": lease["token"]})
        if profile.status_code != 200:
            raise JobCancelled()
        if job["kind"] == "tts.voice.register":
            reserve(3_000_000)
            stage("voice_reference_validation")
            result = normalize_reference(source, output.parent / "reference.wav", alive)
        else:
            reserve(32_000_000)
            result = synthesize(settings, storage, job, profile.json(), alive, stage)
    return result


if __name__ == "__main__":
    run()
