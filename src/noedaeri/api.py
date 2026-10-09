import asyncio
import json
import secrets
import shutil
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlencode, urlsplit
from uuid import UUID, uuid4

import httpx
import jwt
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .ai_indexing import cleanup_indexing_results, install_indexing_routes, process_indexing_queue
from .ai_jobs import cleanup_translation_results, install_ai_job_routes, process_ai_queue
from .auth import COOKIE, Auth, digest
from .compute import Compute
from .config import Settings
from .db import Database
from .embeddings import install_embedding_routes
from .execution import execution_lock
from .integration import PLATFORM_OWNER, Webhooks
from .ocr import runtime_ready
from .queue import Queue
from .raya import Raya, install_raya_routes
from .services import SERVICES, service_catalog
from .storage import Storage
from .tasks import cleanup_operation_results, list_tasks
from .voices import SAMPLE_LIMIT, Voices, default_voice, install_voice_routes


class NewJob(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str
    title: str = Field(min_length=1, max_length=120)
    idempotency_key: UUID
    input: dict = Field(default_factory=dict)
    options: dict = Field(default_factory=dict)
    retry_of: UUID | None = None


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
            "tts_not_configured",
            "tts_generation_failed",
            "tts_memory_unavailable",
            "voice_sample_missing",
            "voice_storage_unavailable",
            "compute_wait_timeout",
            "ocr_not_configured",
            "ocr_recognition_failed",
            "ocr_result_too_large",
            "pdf_encrypted",
            "pdf_page_limit_exceeded",
            "stt_not_configured",
            "stt_transcription_failed",
            "stt_duration_exceeded",
            "stt_result_too_large",
        ]
        | None
    ) = None


class Reservation(Lease):
    bytes: int = Field(gt=0, le=100_000_000_000)


class Receipt(BaseModel):
    event_id: UUID


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
    "retry_of",
    "output_reserved",
    "options",
    "origin",
    "terminal_event_id",
    "received_at",
)


def public_job(job):
    return {key: job[key] for key in PUBLIC_JOB_FIELDS}


def create_app(settings: Settings | None = None):
    settings = settings or Settings.from_env()
    db, storage = Database(settings.database_url), Storage(settings)
    queue = Queue(db, settings.lease_seconds, settings.result_ttl, settings.platform_result_ttl)
    auth = Auth(settings, db)
    webhooks = Webhooks(db, settings)
    raya = Raya(settings)

    async def release_raya():
        while True:
            await asyncio.sleep(5)
            await asyncio.to_thread(raya.reap_idle)

    async def deliver():
        while True:
            await asyncio.sleep(5)
            try:
                await asyncio.to_thread(webhooks.collect)
                for _ in range(20):
                    if not await asyncio.to_thread(webhooks.dispatch_one):
                        break
            except Exception:
                import logging

                logging.getLogger("noedaeri").error("webhook_delivery_failed")

    async def index_documents():
        while True:
            try:
                await process_indexing_queue(db, getattr(app.state, "settings", settings))
            except Exception:
                import logging

                logging.getLogger("noedaeri").error("indexing_dispatch_failed")
            await asyncio.sleep(1)

    async def ai_workflows():
        while True:
            try:
                await process_ai_queue(db, getattr(app.state, "settings", settings))
            except Exception:
                import logging

                logging.getLogger("noedaeri").error("ai_dispatch_failed")
            await asyncio.sleep(1)

    async def maintenance():
        while True:
            try:
                await asyncio.to_thread(webhooks.collect)
                await asyncio.to_thread(storage.cleanup, db)
                await asyncio.to_thread(cleanup_indexing_results, db)
                await asyncio.to_thread(cleanup_operation_results, db)
                await asyncio.to_thread(cleanup_translation_results, db)
            except Exception:
                # Never emit connection strings, stored payloads or credentials to logs.
                import logging

                logging.getLogger("noedaeri").error("maintenance_failed")
            await asyncio.sleep(30)

    @asynccontextmanager
    async def lifespan(app):
        db.migrate()
        with db.connect() as conn:
            policy = conn.execute("SELECT * FROM raya_policy WHERE singleton=true").fetchone()
        if policy:
            raya.settings = replace(
                raya.settings,
                raya_minimum_keep=policy["minimum_keep_seconds"],
                raya_idle=policy["idle_seconds"],
            )
        with db.connect() as conn:
            conn.execute(
                "INSERT INTO users(id,issuer,subject,status,role) "
                "VALUES(%s,'urn:noedaeri:service','platform','approved','user') "
                "ON CONFLICT(id) DO NOTHING",
                (PLATFORM_OWNER,),
            )
        task = asyncio.create_task(maintenance())
        delivery_task = asyncio.create_task(deliver())
        raya_task = asyncio.create_task(release_raya())
        indexing_task = asyncio.create_task(index_documents())
        ai_task = asyncio.create_task(ai_workflows())
        yield
        task.cancel()
        delivery_task.cancel()
        raya_task.cancel()
        indexing_task.cancel()
        ai_task.cancel()
        try:
            await ai_task
        except asyncio.CancelledError:
            pass
        try:
            await indexing_task
        except asyncio.CancelledError:
            pass
        try:
            await raya_task
        except asyncio.CancelledError:
            pass
        await asyncio.to_thread(raya.close)
        try:
            await task
        except asyncio.CancelledError:
            pass
        try:
            await delivery_task
        except asyncio.CancelledError:
            pass

    app = FastAPI(
        title="뇌대리 API", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None
    )
    app.state.db, app.state.queue, app.state.storage = db, queue, storage
    app.state.webhooks = webhooks
    app.state.raya = raya
    app.state.compute = Compute(db, settings.storage_root)
    voices = Voices(db, settings, storage)
    app.state.voices = voices

    def principal(request):
        if request.url.path.startswith("/api/v1/"):
            if not settings.integration_key or not secrets.compare_digest(
                request.headers.get("X-Noedaeri-API-Key", "").encode(),
                settings.integration_key.encode(),
            ):
                raise HTTPException(401, "platform_key_required")
            return {"id": PLATFORM_OWNER, "role": "service"}
        return auth.user(request)

    install_raya_routes(app, settings, auth, raya, db, principal)
    install_embedding_routes(app, settings, auth, principal, db)
    install_ai_job_routes(app, db, auth, settings)
    install_indexing_routes(app, db, auth, settings)
    install_voice_routes(app, voices, principal)

    def present(job):
        data = public_job(job)
        with db.connect() as conn:
            data["delivery"] = conn.execute(
                "SELECT id,state,attempts,last_http_status,last_attempt_at,next_attempt_at "
                "FROM deliveries WHERE job_id=%s",
                (job["id"],),
            ).fetchone()
        return data

    def job_owner(request, job_id):
        user = principal(request)
        if user["role"] == "admin":
            with db.connect() as conn:
                row = conn.execute(
                    "SELECT owner_id FROM jobs WHERE id=%s AND origin='platform'", (job_id,)
                ).fetchone()
            if row:
                return row["owner_id"]
        return user["id"]

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

    @app.get("/api/v1/services")
    @app.get("/api/services")
    def services(request: Request):
        principal(request)
        return service_catalog(getattr(request.app.state, "settings", settings))

    @app.get("/api/v1/tasks")
    @app.get("/api/tasks")
    def tasks(
        request: Request,
        limit: int = Query(default=100, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=1000000),
        status: str | None = None,
        service: str | None = None,
    ):
        return list_tasks(db, principal(request), present, limit, offset, status, service)

    @app.get("/api/compute")
    def compute_status(request: Request):
        user = auth.user(request)
        return {
            "concurrency": 1,
            "requests": app.state.compute.snapshot(None if user["role"] == "admin" else user["id"]),
        }

    @app.post("/api/admin/compute/{reservation_id}/acknowledge-stopped")
    def acknowledge_compute(request: Request, reservation_id: UUID, body: dict):
        auth.user(request, admin=True)
        if set(body) != {"execution_stopped"} or body["execution_stopped"] is not True:
            raise HTTPException(422, "compute_stop_confirmation_required")
        app.state.compute.acknowledge_stopped(reservation_id)
        return {"released": True}

    @app.get("/integrations/SERVICE_INTEGRATION.md")
    @app.get("/api/integrations/guide")
    def integration_guide(request: Request):
        if request.url.path.startswith("/api/"):
            auth.user(request)
        path = Path(__file__).resolve().parents[2] / "docs" / "SERVICE_INTEGRATION.md"
        return FileResponse(path, media_type="text/markdown", filename="SERVICE_INTEGRATION.md")

    @app.get("/integrations/openapi.json")
    def integration_schema():
        schema = get_openapi(
            title="Noedaeri platform integration",
            version="1.0.0",
            routes=[
                route for route in app.routes if getattr(route, "path", "").startswith("/api/v1/")
            ],
        )
        schema.setdefault("components", {})["securitySchemes"] = {
            "PlatformKey": {"type": "apiKey", "in": "header", "name": "X-Noedaeri-API-Key"}
        }
        schema["security"] = [{"PlatformKey": []}]
        return schema

    @app.get("/api/v1/jobs")
    @app.get("/api/jobs")
    def jobs(request: Request, limit: int = 50):
        user = principal(request)
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE owner_id=%s OR (%s AND origin='platform') "
                "ORDER BY created_at DESC LIMIT %s",
                (user["id"], user["role"] == "admin", max(1, min(limit, 100))),
            ).fetchall()
        return [present(row) for row in rows]

    @app.get("/api/v1/jobs/{job_id}")
    @app.get("/api/jobs/{job_id}")
    def get_job(request: Request, job_id: UUID):
        owner = job_owner(request, job_id)
        with db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE id=%s AND owner_id=%s", (job_id, owner)
            ).fetchone()
        if not row:
            raise HTTPException(404, "job_not_found")
        return present(row)

    @app.post("/api/v1/jobs", status_code=201)
    @app.post("/api/jobs", status_code=201)
    def create_job(request: Request, data: NewJob):
        user = principal(request)
        if user["role"] == "service" and not settings.webhook_url:
            raise HTTPException(503, "platform_delivery_not_configured")
        service = SERVICES.get(data.kind)
        if not service:
            raise HTTPException(422, "unsupported_job_kind")
        if data.kind == "tts.voice.register":
            raise HTTPException(422, "voice_registration_endpoint_required")
        if service.service == "tts" and not getattr(app.state, "settings", settings).tts_enabled:
            raise HTTPException(503, "tts_not_configured")
        if service.service in {"ocr", "pdf"} and (
            not getattr(app.state, "settings", settings).ocr_enabled or not runtime_ready()
        ):
            raise HTTPException(503, "ocr_not_configured")
        if (
            service.service in {"stt", "subtitles"}
            and not getattr(app.state, "settings", settings).stt_enabled
        ):
            raise HTTPException(503, "stt_not_configured")
        try:
            options = service.options.model_validate(data.options).model_dump(mode="json")
        except ValidationError:
            raise HTTPException(422, "invalid_job_options") from None
        try:
            data.input = service.input_model.model_validate(data.input).model_dump(mode="json")
        except ValidationError:
            raise HTTPException(422, "invalid_job_input") from None
        if data.kind == "tts.synthesize":
            requester_id = data.input["requester_id"]
            if user["role"] == "service" and not requester_id:
                raise HTTPException(422, "requester_id_required")
            if user["role"] == "user" and requester_id and requester_id != str(user["id"]):
                raise HTTPException(403, "requester_id_mismatch")
            data.input["requester_id"] = requester_id or str(user["id"])
        with db.connect() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(756903)")
            existing = conn.execute(
                "SELECT * FROM jobs WHERE owner_id=%s AND idempotency_key=%s",
                (user["id"], data.idempotency_key),
            ).fetchone()
            if existing:
                existing_options = existing["options"]
                if data.kind == "tts.synthesize":
                    existing_options = {key: existing_options.get(key) for key in options}
                if (
                    existing["kind"] != data.kind
                    or existing_options != options
                    or existing["input"] != data.input
                    or existing["title"] != data.title
                    or existing["retry_of"] != data.retry_of
                ):
                    raise HTTPException(409, "idempotency_conflict")
                return public_job(existing)
            if data.retry_of:
                original = conn.execute(
                    "SELECT * FROM jobs WHERE id=%s AND owner_id=%s FOR UPDATE",
                    (data.retry_of, user["id"]),
                ).fetchone()
                if not original:
                    raise HTTPException(404, "job_not_found")
                if original["status"] not in {"failed", "cancelled"}:
                    raise HTTPException(409, "retry_not_safe")
                try:
                    with execution_lock(storage.root, original["id"]):
                        pass
                except BlockingIOError:
                    raise HTTPException(409, "retry_not_safe") from None
                if original["kind"] != data.kind:
                    raise HTTPException(422, "invalid_job_kind")
            if data.kind == "tts.synthesize":
                voice = voices.resolve(
                    conn, user, options["voice_id"], data.input, options["instruct"]
                )
                # Pin the selection at admission; idempotent retries keep the original choice.
                options["resolved_voice_id"] = str(voice["id"]) if voice else None
            reserved = conn.execute(
                "SELECT COALESCE(sum(CASE WHEN status='uploading' THEN %s ELSE input_bytes END "
                "+output_reserved),0) "
                "AS bytes FROM jobs WHERE cleanup_state<>'done'",
                (settings.upload_limit,),
            ).fetchone()["bytes"]
            incoming = settings.upload_limit
            if service.service == "tts":
                incoming = min(settings.upload_limit, 32_000_000)
            if service.service in {"image", "ocr"}:
                incoming = min(settings.upload_limit, settings.image_input_limit)
            if service.service == "pdf":
                incoming = min(settings.upload_limit, settings.pdf_input_limit)
            if not storage.available(reserved + incoming):
                raise HTTPException(507, "storage_capacity_exceeded")
            job = conn.execute(
                "INSERT INTO jobs(id, owner_id, idempotency_key, kind, service, title, input, "
                "options, status, retry_of, origin) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
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
                    data.retry_of,
                    "platform" if user["role"] == "service" else "web",
                ),
            ).fetchone()
        return public_job(job)

    @app.put("/api/v1/jobs/{job_id}/input")
    @app.put("/api/jobs/{job_id}/input")
    async def upload(request: Request, job_id: UUID):
        user = principal(request)
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
                        limit = (
                            min(settings.upload_limit, SAMPLE_LIMIT)
                            if job["kind"] == "tts.voice.register"
                            else min(settings.upload_limit, settings.image_input_limit)
                            if job["kind"] in {"ocr.recognize", "image.package"}
                            else min(settings.upload_limit, settings.pdf_input_limit)
                            if job["kind"] == "pdf.extract"
                            else settings.upload_limit
                        )
                        if size > limit:
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

    @app.post("/api/v1/jobs/{job_id}/cancel")
    @app.post("/api/jobs/{job_id}/cancel")
    def cancel(request: Request, job_id: UUID):
        owner = job_owner(request, job_id)
        if not queue.cancel(job_id, owner):
            raise HTTPException(409, "job_not_cancellable")
        return {"status": "cancellation_requested"}

    @app.get("/api/v1/jobs/{job_id}/result")
    @app.get("/api/v1/jobs/{job_id}/files/{filename}")
    @app.get("/api/jobs/{job_id}/result")
    @app.get("/api/jobs/{job_id}/files/{filename}")
    def result(request: Request, job_id: UUID, filename: str | None = None):
        owner = job_owner(request, job_id)
        with db.connect() as conn:
            job = conn.execute(
                "SELECT * FROM jobs WHERE id=%s AND owner_id=%s", (job_id, owner)
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
        if job["kind"] in {
            "video.package",
            "image.package",
            "stt.transcribe",
            "video.subtitles",
            "ocr.recognize",
            "pdf.extract",
        }:
            manifest = job["result"] or {}
            name = (
                filename
                or {
                    "image.package": "image.zip",
                    "video.package": "video.zip",
                    "stt.transcribe": "transcript.zip",
                    "video.subtitles": "subtitles.zip",
                    "ocr.recognize": "text.zip",
                    "pdf.extract": "document.zip",
                }[job["kind"]]
            )
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
                ".txt": "text/plain; charset=utf-8",
                ".srt": "application/x-subrip; charset=utf-8",
                ".vtt": "text/vtt; charset=utf-8",
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

    @app.post("/api/v1/jobs/{job_id}/receipt")
    def receipt(request: Request, job_id: UUID, data: Receipt):
        user = principal(request)
        webhooks.collect(job_id)
        with db.connect() as conn:
            job = conn.execute(
                "SELECT * FROM jobs WHERE id=%s AND owner_id=%s FOR UPDATE",
                (job_id, user["id"]),
            ).fetchone()
            if not job:
                raise HTTPException(404, "job_not_found")
            if job["status"] != "succeeded" or job["terminal_event_id"] != data.event_id:
                raise HTTPException(409, "receipt_mismatch")
            if job["received_at"]:
                return {"accepted": True, "cleanup": "scheduled"}
            if job["expires_at"] <= datetime.now(UTC):
                raise HTTPException(410, "result_unavailable")
            conn.execute(
                "UPDATE jobs SET received_at=now(),expires_at=now() WHERE id=%s", (job_id,)
            )
            conn.execute("UPDATE deliveries SET state='acknowledged' WHERE job_id=%s", (job_id,))
        return {"accepted": True, "cleanup": "scheduled"}

    @app.post("/api/admin/jobs/{job_id}/webhook-retry")
    def retry_webhook(request: Request, job_id: UUID):
        auth.user(request, admin=True)
        with db.connect() as conn:
            row = conn.execute(
                "UPDATE deliveries SET state='pending',attempts=0,next_attempt_at=now() "
                "WHERE job_id=%s AND state='failed' RETURNING id",
                (job_id,),
            ).fetchone()
        if not row:
            raise HTTPException(409, "delivery_not_failed")
        return {"accepted": True}

    @app.get("/api/admin/users")
    def users(request: Request):
        auth.user(request, admin=True)
        with db.connect() as conn:
            return conn.execute(
                "SELECT id,issuer,subject,status,role,created_at FROM users "
                "WHERE issuer<>'urn:noedaeri:service' ORDER BY created_at DESC LIMIT 100"
            ).fetchall()

    @app.patch("/api/admin/users/{user_id}")
    def approve(request: Request, user_id: UUID, data: Approval):
        auth.user(request, admin=True)
        with db.connect() as conn:
            changed = conn.execute(
                "UPDATE users SET status=%s WHERE id=%s AND role<>'admin' "
                "AND issuer<>'urn:noedaeri:service' RETURNING id",
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
        return {
            key: job[key] for key in ("id", "kind", "input", "options", "lease_token", "owner_id")
        }

    @app.post("/internal/jobs/{job_id}/heartbeat")
    def heartbeat(request: Request, job_id: UUID, data: Lease):
        worker(request)
        job = queue.heartbeat(job_id, data.token, data.stage)
        if not job:
            raise HTTPException(409, "lease_lost")
        return {"cancel_requested": job["cancel_requested"]}

    @app.post("/internal/jobs/{job_id}/reserve")
    def reserve_output(request: Request, job_id: UUID, data: Reservation):
        worker(request)
        error = storage.reserve(db, job_id, data.token, data.bytes)
        if error:
            raise HTTPException(507 if error == "storage_capacity_exceeded" else 409, error)
        return {"reserved_bytes": data.bytes}

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
            if job["kind"] in {
                "video.package",
                "image.package",
                "stt.transcribe",
                "video.subtitles",
                "ocr.recognize",
                "pdf.extract",
            }:
                if not isinstance(result_data, dict) or result_data.get("type") != job[
                    "kind"
                ].replace(".", "_"):
                    raise HTTPException(409, "result_missing")
                files = result_data.get("files", [])
                required = (
                    {"thumbnail.jpg", "preview.webp", "metadata.json", "image.zip"}
                    if job["kind"] == "image.package"
                    else {"transcript.json", "transcript.txt", "transcript.zip"}
                    if job["kind"] == "stt.transcribe"
                    else {
                        "transcript.json",
                        "transcript.txt",
                        "subtitles.srt",
                        "subtitles.vtt",
                        "subtitles.zip",
                    }
                    if job["kind"] == "video.subtitles"
                    else {"document.json", "document.txt", "document.zip"}
                    if job["kind"] == "pdf.extract"
                    else {"text.json", "text.txt", "text.zip"}
                    if job["kind"] == "ocr.recognize"
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
            if service.result_filename and job["kind"] != "tts.voice.register":
                path = storage.path("results", job_id, service.result_filename)
                if not path.is_file():
                    raise HTTPException(409, "result_missing")
                result_data = {
                    **((result_data or {}) if job["kind"] == "tts.synthesize" else {}),
                    "type": "artifact",
                    "name": service.result_filename,
                    "media_type": service.result_media_type,
                }
        with db.connect() as conn:
            job = conn.execute("SELECT kind FROM jobs WHERE id=%s", (job_id,)).fetchone()
        on_success = voices.activate if job and job["kind"] == "tts.voice.register" else None
        if not queue.finish(
            job_id, data.token, data.status, data.error_code, result_data, on_success
        ):
            raise HTTPException(409, "lease_lost")
        return {"accepted": True}

    @app.get("/internal/jobs/{job_id}/voice")
    def worker_voice(request: Request, job_id: UUID, token: UUID):
        worker(request)
        with db.connect() as conn:
            job = conn.execute(
                "SELECT * FROM jobs WHERE id=%s AND lease_token=%s AND status='running' "
                "AND lease_until>now() AND NOT cancel_requested",
                (job_id, token),
            ).fetchone()
            if not job or job["kind"] not in {"tts.synthesize", "tts.voice.register"}:
                raise HTTPException(409, "lease_lost")
            voice_id = job["options"].get("resolved_voice_id", job["options"].get("voice_id"))
            if job["kind"] == "tts.synthesize" and voice_id is None:
                return default_voice()
            row = conn.execute("SELECT * FROM voices WHERE id=%s", (voice_id,)).fetchone()
            if not row or row["status"] in {"deleted", "cleanup_failed"}:
                raise HTTPException(409, "voice_sample_missing")
            if job["kind"] == "tts.voice.register":
                if row["registration_job_id"] != job_id:
                    raise HTTPException(409, "voice_sample_missing")
            elif row["status"] != "ready":
                raise HTTPException(409, "voice_sample_missing")
            return {
                key: row[key]
                for key in ("id", "kind", "speaker", "reference_text", "sample_sha256")
            }

    web_root = Path(__file__).resolve().parents[2] / "web" / "dist"
    if (web_root / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=web_root / "assets"), name="assets")

    @app.get("/")
    def index():
        if not (web_root / "index.html").is_file():
            raise HTTPException(503, "web_build_required")
        return FileResponse(web_root / "index.html")

    return app
