import json
import logging
import os
import signal
import sys
import time
from pathlib import Path
from uuid import UUID, uuid4

import httpx

from .config import Settings
from .execution import execution_lock
from .media import JobCancelled, MediaError, run_process, thumbnail, video_package
from .storage import Storage


def run():
    settings = Settings.from_env()
    storage = Storage(settings)
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
                        "kinds": ["video.thumbnail", "video.package", "image.package"],
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
                                capacity_alive,
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
                                capacity_alive,
                                stage,
                                reserve,
                            )
                        else:
                            reserve(2_097_152)
                            thumbnail(
                                source,
                                output,
                                job["options"]["seconds"],
                                settings.job_timeout,
                                capacity_alive,
                            )
                        if not capacity_alive():
                            raise JobCancelled()
                    except JobCancelled:
                        status = "cancelled"
                    except MediaError as error:
                        status, code = "failed", str(error)
                    except (OSError, ValueError):
                        status, code = "failed", "worker_failed"
                    # A lost lease never reports a result. The process has already been reaped.
                    if valid:
                        client.post(
                            f"/internal/jobs/{job['id']}/finish",
                            json={**lease, "status": status, "error_code": code, "result": result},
                        ).raise_for_status()
            except BlockingIOError:
                time.sleep(1)
            except httpx.HTTPError:
                logging.getLogger("noedaeri").error("worker_api_unavailable")
                time.sleep(2)


if __name__ == "__main__":
    run()
