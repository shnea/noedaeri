import logging
import os
import signal
import time
from uuid import UUID, uuid4

import httpx

from .config import Settings
from .media import JobCancelled, MediaError, thumbnail
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
                    json={"worker_id": str(worker_id), "kinds": ["video.thumbnail"]},
                )
                response.raise_for_status()
                job = response.json()
                if not job:
                    time.sleep(1)
                    continue
                lease = {"token": job["lease_token"]}
                last_beat, valid, cancelled = 0.0, True, False

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

                status, code = "succeeded", None
                try:
                    job_id = UUID(job["id"])
                    thumbnail(
                        storage.path("uploads", job_id, "input"),
                        storage.path("results", job_id, "thumbnail.jpg"),
                        job["options"]["seconds"],
                        settings.job_timeout,
                        alive,
                    )
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
                        json={**lease, "status": status, "error_code": code},
                    ).raise_for_status()
            except httpx.HTTPError:
                logging.getLogger("noedaeri").error("worker_api_unavailable")
                time.sleep(2)


if __name__ == "__main__":
    run()
