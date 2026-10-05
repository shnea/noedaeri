"""Server-side integration helpers. Load credentials from the host's secret environment."""

import hashlib
import hmac
import json
import time
from pathlib import Path
from uuid import UUID

import httpx


def verify_webhook(body: bytes, headers, secret: str, now=None):
    """Verify raw bytes BEFORE parsing; persist event_id uniquely before responding 2xx."""
    headers = {key.lower(): value for key, value in headers.items()}
    if len(body) > 128 * 1024:
        raise ValueError("Webhook too large")
    timestamp = headers.get("x-noedaeri-timestamp", "")
    try:
        age = abs((time.time() if now is None else now) - int(timestamp))
    except ValueError:
        raise ValueError("Invalid webhook timestamp") from None
    if age > 300:
        raise ValueError("Stale webhook")
    expected = (
        "sha256="
        + hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    )
    if not hmac.compare_digest(headers.get("x-noedaeri-signature", ""), expected):
        raise ValueError("Invalid webhook signature")
    event = json.loads(body)
    if event.get("version") != 1 or event.get("type") not in {
        "job.succeeded",
        "job.failed",
        "job.cancelled",
    }:
        raise ValueError("Unsupported webhook")
    if str(UUID(event["event_id"])) != headers.get("x-noedaeri-event-id"):
        raise ValueError("Event ID mismatch")
    UUID(event["job_id"])
    return event


class PlatformClient:
    def __init__(self, origin, api_key):
        if not origin.startswith("https://"):
            raise ValueError("HTTPS origin required")
        self.client = httpx.Client(
            base_url=origin.rstrip("/"),
            headers={"X-Noedaeri-API-Key": api_key},
            timeout=120,
            follow_redirects=False,
            trust_env=False,
        )

    def close(self):
        self.client.close()

    def create(self, *, kind, title, request_id, extension=None, seconds=0, retry_of=None):
        # Save request_id with the platform file/generation before this request.
        payload = {
            "kind": kind,
            "title": title,
            "idempotency_key": str(UUID(str(request_id))),
            "input": {"type": "upload", **({"extension": extension} if extension else {})},
            "options": {} if kind == "image.package" else {"seconds": seconds},
            "retry_of": str(UUID(str(retry_of))) if retry_of else None,
        }
        response = self.client.post("/api/v1/jobs", json=payload)
        response.raise_for_status()
        return response.json()

    def job(self, job_id):
        response = self.client.get(f"/api/v1/jobs/{UUID(str(job_id))}")
        response.raise_for_status()
        return response.json()

    def upload(self, job_id, source: Path):
        # After an uncertain response inspect job() once; don't overwrite queued inputs.
        with source.open("rb") as stream:
            response = self.client.put(
                f"/api/v1/jobs/{UUID(str(job_id))}/input",
                content=iter(lambda: stream.read(1024 * 1024), b""),
                headers={"Content-Type": "application/octet-stream"},
            )
        response.raise_for_status()

    def download(self, job_id, destination: Path):
        # Caller owns destination and disk limits. No archive extraction is performed here.
        # Exclusive creation avoids overwriting existing platform files.
        try:
            with destination.open("xb") as target:
                try:
                    with self.client.stream("GET", f"/api/v1/jobs/{UUID(str(job_id))}/result") as r:
                        r.raise_for_status()
                        for chunk in r.iter_bytes():
                            target.write(chunk)
                except BaseException:
                    destination.unlink(missing_ok=True)
                    raise
        except FileExistsError:
            raise ValueError("Destination already exists") from None

    def receipt(self, job_id, event_id):
        # ONLY after durable storage and derivative registration commit successfully.
        response = self.client.post(
            f"/api/v1/jobs/{UUID(str(job_id))}/receipt",
            json={"event_id": str(UUID(str(event_id)))},
        )
        response.raise_for_status()
        return response.json()
