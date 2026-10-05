import asyncio
import json
import secrets
import shutil
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlencode, urlsplit
from uuid import UUID, uuid4

import httpx
import jwt
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .auth import COOKIE, Auth, digest
from .config import Settings
from .db import Database
from .queue import Queue
from .services import SERVICES
from .storage import Storage


class NewJob(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str
    title: str = Field(min_length=1, max_length=120)
    idempotency_key: UUID
    input: dict = Field(default_factory=dict)
    options: dict = Field(default_factory=dict)


class Claim(BaseModel):
    worker_id: UUID
    kinds: list[str] = Field(min_length=1, max_length=20)


class Lease(BaseModel):
    token: UUID
    stage: str | None = Field(default=None, max_length=40, pattern=r"^[a-z0-9_]+$")


class Finish(Lease):
    result: dict | None = None
    status: Literal["succeeded", "failed", "cancelled"]
    error_code: (
        Literal[
            "processing_timeout",
            "invalid_media_or_conversion_failed",
            "unsupported_media",
            "result_missing",
            "worker_failed",
            "storage_capacity_exceeded",
        ]
        | None
    ) = None


class Approval(BaseModel):
    status: Literal["approved", "rejected", "revoked"]


PUBLIC_JOB_FIELDS = (
    "id",
    "title",
    "kind",
    "service",
    "status",
    "stage",
    "owner_id",
    "worker_id",
    "created_at",
    "updated_at",
    "finished_at",
    "expires_at",
    "cancel_requested",
    "error_code",
    "cleanup_state",
    "result_state",
    "result",
)


def public_job(job):
    return {key: job[key] for key in PUBLIC_JOB_FIELDS}


def create_app(settings: Settings | None = None):
    settings = settings or Settings.from_env()
    db, storage = Database(settings.database_url), Storage(settings)
    queue = Queue(db, settings.lease_seconds, settings.result_ttl)
    auth = Auth(settings, db)

    async def maintenance():
        while True:
            try:
                await asyncio.to_thread(storage.cleanup, db)
            except Exception:
                # Never emit connection strings, stored payloads or credentials to logs.
                import logging

                logging.getLogger("noedaeri").error("maintenance_failed")
            await asyncio.sleep(30)

    @asynccontextmanager
    async def lifespan(app):
        db.migrate()
        task = asyncio.create_task(maintenance())
        yield
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    app = FastAPI(
        title="뇌대리 API", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None
    )
    app.state.db, app.state.queue, app.state.storage = db, queue, storage

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    def worker(request):
        if not secrets.compare_digest(
            request.headers.get("authorization", ""), "Bearer " + settings.worker_key
        ):
            raise HTTPException(401, "worker_key_required")

    @app.get("/health/live")
    def health():
        return {"status": "up"}

    @app.get("/auth/login")
    def login():
        try:
            return auth.login()
        except (httpx.HTTPError, ValueError, KeyError):
            raise HTTPException(503, "identity_provider_unavailable") from None

    @app.get("/auth/callback")
    def callback(request: Request, state: str = "", code: str = ""):
        try:
            return auth.callback(request, state, code)
        except (httpx.HTTPError, jwt.PyJWTError, ValueError, KeyError, StopIteration):
            raise HTTPException(400, "login_validation_failed") from None

    @app.post("/auth/logout")
    def logout(request: Request):
        auth.user(request, approved=False)
        with db.connect() as conn:
            conn.execute(
                "DELETE FROM sessions WHERE digest=%s", (digest(request.cookies.get(COOKIE, "")),)
            )
        logout_url = None
        try:
            endpoint = auth.metadata().get("end_session_endpoint", "")
            if urlsplit(endpoint).scheme == "https" and urlsplit(endpoint).netloc:
                logout_url = (
                    endpoint
                    + "?"
                    + urlencode(
                        {
                            "client_id": settings.client_id,
                            "post_logout_redirect_uri": settings.public_origin + "/",
                        }
                    )
                )
        except (httpx.HTTPError, ValueError, KeyError):
            pass  # Local logout must succeed even if the identity provider is unavailable.
        response = JSONResponse({"logout_url": logout_url})
        response.delete_cookie(COOKIE, secure=True, httponly=True, samesite="lax")
        return response

    @app.get("/api/me")
    def me(request: Request):
        user = auth.user(request, approved=False)
        return {key: user[key] for key in ("id", "role", "status", "csrf")}

    @app.get("/api/services")
    def services(request: Request):
        auth.user(request)
        return [
            {
                "kind": item.kind,
                "service": item.service,
                "label": item.label,
                "input_type": item.input_type,
                "options_schema": item.options.model_json_schema(),
            }
            for item in SERVICES.values()
        ]

    @app.get("/api/integrations/guide")
    def integration_guide(request: Request):
        auth.user(request)
        path = Path(__file__).resolve().parents[2] / "docs" / "SERVICE_INTEGRATION.md"
        return FileResponse(path, media_type="text/markdown", filename="SERVICE_INTEGRATION.md")

    @app.get("/api/jobs")
    def jobs(request: Request, limit: int = 50):
        user = auth.user(request)
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE owner_id=%s ORDER BY created_at DESC LIMIT %s",
                (user["id"], max(1, min(limit, 100))),
            ).fetchall()
        return [public_job(row) for row in rows]

    @app.post("/api/jobs", status_code=201)
    def create_job(request: Request, data: NewJob):
        user = auth.user(request)
        service = SERVICES.get(data.kind)
        if not service:
            raise HTTPException(422, "unsupported_job_kind")
        try:
            options = service.options.model_validate(data.options).model_dump()
        except ValidationError:
            raise HTTPException(422, "invalid_job_options") from None
        try:
            service.input_model.model_validate(data.input)
        except ValidationError:
            raise HTTPException(422, "invalid_job_input") from None
        with db.connect() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(756903)")
            existing = conn.execute(
                "SELECT * FROM jobs WHERE owner_id=%s AND idempotency_key=%s",
                (user["id"], data.idempotency_key),
            ).fetchone()
            if existing:
                if (
                    existing["kind"] != data.kind
                    or existing["options"] != options
                    or existing["input"] != data.input
                    or existing["title"] != data.title
                ):
                    raise HTTPException(409, "idempotency_conflict")
                return public_job(existing)
            reserved = conn.execute(
                "SELECT COALESCE(sum(CASE WHEN status='uploading' THEN %s ELSE input_bytes END),0) "
                "AS bytes FROM jobs WHERE cleanup_state<>'done'",
                (settings.upload_limit,),
            ).fetchone()["bytes"]
            if not storage.available(reserved + settings.upload_limit):
                raise HTTPException(507, "storage_capacity_exceeded")
            job = conn.execute(
                "INSERT INTO jobs(id, owner_id, idempotency_key, kind, service, title, input, "
                "options, status) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
                (
                    uuid4(),
                    user["id"],
                    data.idempotency_key,
                    data.kind,
                    service.service,
                    data.title,
                    Jsonb(data.input),
                    Jsonb(options),
                    "uploading" if service.input_type == "upload" else "queued",
                ),
            ).fetchone()
        return public_job(job)

    @app.put("/api/jobs/{job_id}/input")
    async def upload(request: Request, job_id: UUID):
        user = auth.user(request)
        if request.headers.get("content-type") != "application/octet-stream":
            raise HTTPException(415, "binary_body_required")
        # Stream raw bytes directly into owned storage; no multipart spool outside tmp/.
        with db.connect() as conn:
            job = conn.execute(
                "SELECT * FROM jobs WHERE id=%s AND owner_id=%s FOR UPDATE NOWAIT",
                (job_id, user["id"]),
            ).fetchone()
            if not job:
                raise HTTPException(404, "job_not_found")
            if job["status"] != "uploading":
                raise HTTPException(409, "input_already_received")
            path = storage.path("uploads", job_id, "input")
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            size = 0
            try:
                with path.open("wb") as target:
                    stream = request.stream().__aiter__()
                    while True:
                        try:
                            chunk = await asyncio.wait_for(anext(stream), timeout=30)
                        except StopAsyncIteration:
                            break
                        except TimeoutError:
                            raise HTTPException(408, "upload_timeout") from None
                        if shutil.disk_usage(storage.root).free < settings.free_floor + len(chunk):
                            raise HTTPException(507, "storage_capacity_exceeded")
                        size += len(chunk)
                        if size > min(
                            settings.upload_limit,
                            32_000_000 if job["kind"] == "image.package" else settings.upload_limit,
                        ):
                            raise HTTPException(413, "upload_too_large")
                        target.write(chunk)
                if not size:
                    raise HTTPException(422, "empty_upload")
                conn.execute(
                    "UPDATE jobs SET status='queued',input_bytes=%s,updated_at=now() WHERE id=%s",
                    (size, job_id),
                )
            except BaseException:
                path.unlink(missing_ok=True)
                raise
        return {"status": "queued"}

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel(request: Request, job_id: UUID):
        user = auth.user(request)
        if not queue.cancel(job_id, user["id"]):
            raise HTTPException(409, "job_not_cancellable")
        return {"status": "cancellation_requested"}

    @app.get("/api/jobs/{job_id}/result")
    @app.get("/api/jobs/{job_id}/files/{filename}")
    def result(request: Request, job_id: UUID, filename: str | None = None):
        user = auth.user(request)
        with db.connect() as conn:
            job = conn.execute(
                "SELECT * FROM jobs WHERE id=%s AND owner_id=%s", (job_id, user["id"])
            ).fetchone()
        if not job:
            raise HTTPException(404, "job_not_found")
        if (
            job["result_state"] != "available"
            or not job["expires_at"]
            or job["expires_at"] <= datetime.now(UTC)
        ):
            raise HTTPException(410, "result_unavailable")
        service = SERVICES[job["kind"]]
        if job["kind"] in {"video.package", "image.package"}:
            manifest = job["result"] or {}
            name = filename or ("image.zip" if job["kind"] == "image.package" else "video.zip")
            if name not in manifest.get("files", []):
                raise HTTPException(404, "result_missing")
            try:
                path = storage.path("results", job_id, name)
            except ValueError:
                raise HTTPException(404, "result_missing") from None
            if not path.is_file():
                raise HTTPException(410, "result_missing")
            media_type = {
                ".m3u8": "application/vnd.apple.mpegurl",
                ".ts": "video/mp2t",
                ".jpg": "image/jpeg",
                ".webp": "image/webp",
                ".json": "application/json",
                ".zip": "application/zip",
            }.get(path.suffix)
            return FileResponse(
                path, media_type=media_type, filename=name if name.endswith(".zip") else None
            )
        if filename is not None:
            raise HTTPException(404, "result_missing")
        if not service.result_filename:
            return job["result"]
        path = storage.path("results", job_id, service.result_filename)
        if not path.is_file():
            raise HTTPException(410, "result_missing")
        return FileResponse(
            path, media_type=service.result_media_type, filename=service.result_filename
        )

    @app.get("/api/admin/users")
    def users(request: Request):
        auth.user(request, admin=True)
        with db.connect() as conn:
            return conn.execute(
                "SELECT id,issuer,subject,status,role,created_at FROM users "
                "ORDER BY created_at DESC LIMIT 100"
            ).fetchall()

    @app.patch("/api/admin/users/{user_id}")
    def approve(request: Request, user_id: UUID, data: Approval):
        auth.user(request, admin=True)
        with db.connect() as conn:
            changed = conn.execute(
                "UPDATE users SET status=%s WHERE id=%s AND role<>'admin' RETURNING id",
                (data.status, user_id),
            ).fetchone()
        if not changed:
            raise HTTPException(409, "user_not_editable")
        return {"status": data.status}

    @app.get("/api/admin/workers")
    def workers(request: Request):
        auth.user(request, admin=True)
        with db.connect() as conn:
            return conn.execute(
                "SELECT id,last_seen,last_seen>now()-make_interval(secs=>%s) "
                "AS online FROM workers",
                (settings.lease_seconds,),
            ).fetchall()

    @app.post("/internal/claim")
    def claim(request: Request, data: Claim):
        worker(request)
        job = queue.claim(data.worker_id, data.kinds)
        if not job:
            return JSONResponse(None)
        return {key: job[key] for key in ("id", "kind", "input", "options", "lease_token")}

    @app.post("/internal/jobs/{job_id}/heartbeat")
    def heartbeat(request: Request, job_id: UUID, data: Lease):
        worker(request)
        job = queue.heartbeat(job_id, data.token, data.stage)
        if not job:
            raise HTTPException(409, "lease_lost")
        return {"cancel_requested": job["cancel_requested"]}

    @app.post("/internal/jobs/{job_id}/finish")
    def finish(request: Request, job_id: UUID, data: Finish):
        worker(request)
        result_data = data.result
        if len(json.dumps(result_data).encode()) > 65536:
            raise HTTPException(413, "result_too_large")
        if data.status == "succeeded":
            with db.connect() as conn:
                job = conn.execute("SELECT kind FROM jobs WHERE id=%s", (job_id,)).fetchone()
            if not job:
                raise HTTPException(404, "job_not_found")
            service = SERVICES[job["kind"]]
            if job["kind"] in {"video.package", "image.package"}:
                if not isinstance(result_data, dict) or result_data.get("type") != job[
                    "kind"
                ].replace(".", "_"):
                    raise HTTPException(409, "result_missing")
                files = result_data.get("files", [])
                required = (
                    {"thumbnail.jpg", "preview.webp", "metadata.json", "image.zip"}
                    if job["kind"] == "image.package"
                    else {"master.m3u8", "thumbnail.jpg", "video.zip"}
                )
                if (
                    not isinstance(files, list)
                    or not all(isinstance(name, str) for name in files)
                    or not required.issubset(files)
                ):
                    raise HTTPException(409, "result_missing")
                try:
                    if not all(storage.path("results", job_id, name).is_file() for name in files):
                        raise ValueError()
                except (ValueError, TypeError):
                    raise HTTPException(409, "result_missing") from None
            if service.result_filename:
                path = storage.path("results", job_id, service.result_filename)
                if not path.is_file():
                    raise HTTPException(409, "result_missing")
                result_data = {
                    "type": "artifact",
                    "name": service.result_filename,
                    "media_type": service.result_media_type,
                }
        if not queue.finish(job_id, data.token, data.status, data.error_code, result_data):
            raise HTTPException(409, "lease_lost")
        return {"accepted": True}

    web_root = Path(__file__).resolve().parents[2] / "web" / "dist"
    if (web_root / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=web_root / "assets"), name="assets")

    @app.get("/")
    def index():
        if not (web_root / "index.html").is_file():
            raise HTTPException(503, "web_build_required")
        return FileResponse(web_root / "index.html")

    return app
