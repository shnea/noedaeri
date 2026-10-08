"""Persistent voice profiles, scoped to a trusted caller and requester."""

import hashlib
import shutil
import wave
from typing import Literal
from uuid import UUID, uuid4

from fastapi import HTTPException, Query, Request
from fastapi.responses import FileResponse
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .execution import execution_lock
from .integration import PLATFORM_OWNER
from .voice_storage import voice_storage_online

SPEAKERS = ("Sohee", "Vivian", "Serena", "Uncle_Fu", "Dylan", "Eric", "Ryan", "Aiden", "Ono_Anna")
SAMPLE_LIMIT = 64 * 1024**2
PROFILE_RESERVATION = 3_000_000


class NewVoice(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    idempotency_key: UUID
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["preset", "clone"]
    requester_id: str | None = Field(default=None, min_length=1, max_length=128)
    project: str = Field(default="default", min_length=1, max_length=128)
    environment: str = Field(default="production", min_length=1, max_length=128)
    speaker: str | None = None
    reference_text: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def valid_voice(self):
        if self.kind == "preset":
            if self.speaker not in SPEAKERS or self.reference_text:
                raise ValueError("Preset voice requires a supported speaker")
        elif not self.reference_text or self.speaker is not None:
            raise ValueError("Clone voice requires the reference transcript")
        return self


class VoiceName(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=120)


class Voices:
    def __init__(self, db, settings, storage):
        self.db, self.settings, self.storage = db, settings, storage

    def sample(self, voice_id):
        if not voice_storage_online(self.settings):
            raise HTTPException(503, "voice_storage_unavailable")
        root = self.settings.voice_root.resolve()
        path = root / str(UUID(str(voice_id))) / "reference.wav"
        if path.is_symlink() or path.parent.is_symlink() or not path.resolve().is_relative_to(root):
            raise HTTPException(409, "unsafe_voice_storage")
        return path

    def owned(self, conn, user, voice_id, scope=None):
        row = conn.execute(
            "SELECT * FROM voices WHERE id=%s AND (owner_id=%s OR (%s AND owner_id=%s))",
            (voice_id, user["id"], user["role"] == "admin", PLATFORM_OWNER),
        ).fetchone()
        if not row or row["status"] == "deleted":
            raise HTTPException(404, "voice_not_found")
        if scope and any(row[key] != value for key, value in scope.items()):
            raise HTTPException(404, "voice_not_found")
        return row

    def public(self, conn, row):
        data = {
            key: row[key]
            for key in (
                "id",
                "owner_id",
                "requester_id",
                "project",
                "environment",
                "name",
                "kind",
                "speaker",
                "reference_text",
                "status",
                "registration_job_id",
                "sample_bytes",
                "created_at",
            )
        }
        data["error_code"] = None
        data["sample_available"] = row["kind"] == "preset"
        if row["kind"] == "clone" and row["status"] == "ready":
            if not voice_storage_online(self.settings):
                data["error_code"] = "voice_storage_unavailable"
            else:
                data["sample_available"] = self.sample(row["id"]).is_file()
                if not data["sample_available"]:
                    data["error_code"] = "voice_sample_missing"
        if row["status"] == "pending" and row["registration_job_id"]:
            job = conn.execute(
                "SELECT status,error_code FROM jobs WHERE id=%s", (row["registration_job_id"],)
            ).fetchone()
            if job:
                data["status"], data["error_code"] = job["status"], job["error_code"]
        return data

    def resolve(self, conn, user, voice_id, payload, instruct):
        scope = {key: payload[key] for key in ("requester_id", "project", "environment")}
        row = self.owned(conn, user, voice_id, scope)
        if row["status"] != "ready":
            raise HTTPException(409, "voice_not_ready")
        if row["kind"] == "clone" and instruct:
            raise HTTPException(422, "clone_style_not_supported")
        if row["kind"] == "clone" and not self.sample(voice_id).is_file():
            raise HTTPException(409, "voice_sample_missing")
        return row

    def activate(self, conn, job, result):
        voice_id = UUID(job["options"]["voice_id"])
        row = conn.execute(
            "SELECT * FROM voices WHERE id=%s AND registration_job_id=%s FOR UPDATE",
            (voice_id, job["id"]),
        ).fetchone()
        if not row or row["status"] != "pending":
            raise HTTPException(409, "voice_registration_conflict")
        source = self.storage.path("results", job["id"], "reference.wav")
        if not source.is_file() or not 0 < source.stat().st_size <= PROFILE_RESERVATION:
            raise HTTPException(409, "result_missing")
        try:
            with wave.open(str(source)) as audio:
                if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (
                    1,
                    2,
                    24000,
                ):
                    raise ValueError()
                if not 3 <= audio.getnframes() / 24000 <= 30:
                    raise ValueError()
        except (wave.Error, EOFError, ValueError):
            raise HTTPException(409, "result_missing") from None
        target = self.sample(voice_id)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        pending = target.with_suffix(".pending")
        try:
            shutil.copyfile(source, pending)
            pending.chmod(0o600)
            pending.replace(target)
        finally:
            pending.unlink(missing_ok=True)
        checksum = hashlib.sha256(target.read_bytes()).hexdigest()
        conn.execute(
            "UPDATE voices SET status='ready',sample_bytes=%s,sample_sha256=%s WHERE id=%s",
            (target.stat().st_size, checksum, voice_id),
        )
        return {
            **(result or {}),
            "type": "voice_profile",
            "voice_id": str(voice_id),
            "name": "reference.wav",
            "media_type": "audio/wav",
        }


def install_voice_routes(app, voices, principal):
    db, settings, storage = voices.db, voices.settings, voices.storage

    def scope_for(user, requester_id, project, environment):
        if user["role"] == "service" and not requester_id:
            raise HTTPException(422, "requester_id_required")
        if user["role"] == "user":
            if requester_id and requester_id != str(user["id"]):
                raise HTTPException(403, "requester_id_mismatch")
            requester_id = str(user["id"])
        return (
            {"requester_id": requester_id, "project": project, "environment": environment}
            if requester_id
            else None
        )

    @app.get("/api/v1/voices")
    @app.get("/api/voices")
    def list_voices(
        request: Request,
        requester_id: str | None = Query(default=None, max_length=128),
        project: str = "default",
        environment: str = "production",
    ):
        user = principal(request)
        scope = scope_for(user, requester_id, project, environment)
        requester_id = scope["requester_id"] if scope else None
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM voices WHERE status<>'deleted' AND "
                "(owner_id=%s OR (%s AND owner_id=%s)) AND "
                "(%s::text IS NULL OR (requester_id=%s AND project=%s AND environment=%s)) "
                "ORDER BY created_at DESC LIMIT 500",
                (
                    user["id"],
                    user["role"] == "admin",
                    PLATFORM_OWNER,
                    requester_id,
                    requester_id,
                    project,
                    environment,
                ),
            ).fetchall()
            return [voices.public(conn, row) for row in rows]

    @app.post("/api/v1/voices", status_code=201)
    @app.post("/api/voices", status_code=201)
    def create_voice(request: Request, data: NewVoice):
        user = principal(request)
        if not getattr(request.app.state, "settings", settings).tts_enabled:
            raise HTTPException(503, "tts_not_configured")
        if data.kind == "clone" and not voice_storage_online(voices.settings):
            raise HTTPException(503, "voice_storage_unavailable")
        if user["role"] == "service":
            if data.kind == "clone" and not settings.webhook_url:
                raise HTTPException(503, "platform_delivery_not_configured")
        if user["role"] == "service":
            if not data.requester_id:
                raise HTTPException(422, "requester_id_required")
        else:
            if data.requester_id and data.requester_id != str(user["id"]):
                raise HTTPException(403, "requester_id_mismatch")
            data.requester_id = str(user["id"])
        payload = data.model_dump(mode="json")
        with db.connect() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(756903)")
            existing = conn.execute(
                "SELECT * FROM voices WHERE owner_id=%s AND idempotency_key=%s",
                (user["id"], data.idempotency_key),
            ).fetchone()
            if existing:
                if any(str(existing[key]) != str(value) for key, value in payload.items()):
                    raise HTTPException(409, "idempotency_conflict")
                return voices.public(conn, existing)
            reserved = conn.execute(
                "SELECT COALESCE(sum(CASE WHEN kind='clone' THEN GREATEST(sample_bytes,%s) "
                "ELSE 0 END),0) AS bytes FROM voices "
                "WHERE status<>'deleted'",
                (PROFILE_RESERVATION,),
            ).fetchone()["bytes"]
            if (
                reserved + (PROFILE_RESERVATION if data.kind == "clone" else 0)
                > settings.voice_storage_limit
            ):
                raise HTTPException(507, "voice_storage_capacity_exceeded")
            voice_id, job_id = uuid4(), uuid4() if data.kind == "clone" else None
            if job_id:
                used = conn.execute(
                    "SELECT COALESCE(sum(CASE WHEN status='uploading' THEN %s ELSE input_bytes END"
                    "+output_reserved),0) AS bytes FROM jobs WHERE cleanup_state<>'done'",
                    (settings.upload_limit,),
                ).fetchone()["bytes"]
                if not storage.available(used + min(settings.upload_limit, SAMPLE_LIMIT)):
                    raise HTTPException(507, "storage_capacity_exceeded")
                conn.execute(
                    "INSERT INTO jobs(id,owner_id,idempotency_key,kind,service,title,input,options,"
                    "status,origin) VALUES(%s,%s,%s,'tts.voice.register','tts',"
                    "%s,%s,%s,'uploading',%s)",
                    (
                        job_id,
                        user["id"],
                        voice_id,
                        "목소리 등록 · " + data.name,
                        Jsonb({"type": "upload"}),
                        Jsonb({"voice_id": str(voice_id)}),
                        "platform" if user["role"] == "service" else "web",
                    ),
                )
            row = conn.execute(
                "INSERT INTO voices(id,owner_id,idempotency_key,requester_id,project,environment,"
                "name,kind,speaker,reference_text,registration_job_id,status) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
                (
                    voice_id,
                    user["id"],
                    data.idempotency_key,
                    data.requester_id,
                    data.project,
                    data.environment,
                    data.name,
                    data.kind,
                    data.speaker,
                    data.reference_text,
                    job_id,
                    "pending" if job_id else "ready",
                ),
            ).fetchone()
            return voices.public(conn, row)

    @app.get("/api/v1/voices/{voice_id}")
    @app.get("/api/voices/{voice_id}")
    def get_voice(
        request: Request,
        voice_id: UUID,
        requester_id: str | None = None,
        project: str = "default",
        environment: str = "production",
    ):
        user = principal(request)
        with db.connect() as conn:
            row = voices.owned(
                conn, user, voice_id, scope_for(user, requester_id, project, environment)
            )
            return voices.public(conn, row)

    @app.get("/api/v1/voices/{voice_id}/sample")
    @app.get("/api/voices/{voice_id}/sample")
    def voice_sample(
        request: Request,
        voice_id: UUID,
        requester_id: str | None = None,
        project: str = "default",
        environment: str = "production",
    ):
        user = principal(request)
        with db.connect() as conn:
            row = voices.owned(
                conn, user, voice_id, scope_for(user, requester_id, project, environment)
            )
        path = voices.sample(voice_id)
        if row["kind"] != "clone" or row["status"] != "ready" or not path.is_file():
            raise HTTPException(404, "voice_sample_missing")
        return FileResponse(path, media_type="audio/wav")

    @app.patch("/api/v1/voices/{voice_id}")
    @app.patch("/api/voices/{voice_id}")
    def rename_voice(
        request: Request,
        voice_id: UUID,
        data: VoiceName,
        requester_id: str | None = None,
        project: str = "default",
        environment: str = "production",
    ):
        user = principal(request)
        with db.connect() as conn:
            voices.owned(conn, user, voice_id, scope_for(user, requester_id, project, environment))
            row = conn.execute(
                "UPDATE voices SET name=%s WHERE id=%s RETURNING *", (data.name, voice_id)
            ).fetchone()
            return voices.public(conn, row)

    @app.delete("/api/v1/voices/{voice_id}")
    @app.delete("/api/voices/{voice_id}")
    def delete_voice(
        request: Request,
        voice_id: UUID,
        requester_id: str | None = None,
        project: str = "default",
        environment: str = "production",
    ):
        user = principal(request)
        with db.connect() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(756903)")
            row = voices.owned(
                conn, user, voice_id, scope_for(user, requester_id, project, environment)
            )
            if conn.execute(
                "SELECT 1 FROM jobs WHERE options->>'voice_id'=%s AND "
                "status IN ('uploading','queued','running','interrupted')",
                (str(voice_id),),
            ).fetchone():
                raise HTTPException(409, "voice_in_use")
            if row["registration_job_id"]:
                try:
                    with execution_lock(storage.root, row["registration_job_id"]):
                        pass
                except BlockingIOError:
                    raise HTTPException(409, "voice_in_use") from None
            try:
                if row["kind"] == "clone":
                    path = voices.sample(voice_id)
                    if path.parent.exists():
                        shutil.rmtree(path.parent)
            except OSError:
                conn.execute("UPDATE voices SET status='cleanup_failed' WHERE id=%s", (voice_id,))
                return {"deleted": False, "status": "cleanup_failed"}
            conn.execute(
                "UPDATE voices SET status='deleted',sample_bytes=0 WHERE id=%s", (voice_id,)
            )
        return {"deleted": True, "status": "deleted"}
