import asyncio
import json
import secrets
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .ai_delivery import ai_result_available, collect_ai_events, present_ai_delivery
from .auth import Auth
from .compute import Compute, compute_context, guard_key
from .config import Settings
from .db import Database
from .integration import PLATFORM_OWNER
from .translation import TranslationOptions, TranslationRequest, translation_result


class AiJobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=128)
    task_type: str = Field(min_length=1, max_length=64)
    prompt: str = Field(min_length=1, max_length=200000)
    project: str = Field(default="default", min_length=1, max_length=128)
    environment: str = Field(default="production", min_length=1, max_length=128)
    input: dict[str, Any] = Field(default_factory=dict)
    sync: bool = True
    notify: bool = False

    @model_validator(mode="after")
    def validate_translation(self):
        if self.task_type == "text.translate":
            self.prompt = self.prompt.strip()
            options = TranslationOptions.model_validate(self.input)
            TranslationRequest(request_id=self.request_id, text=self.prompt, **options.model_dump())
            self.input = options.model_dump()
        return self


class AiUsageReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=128)
    task_type: str = Field(min_length=1, max_length=64)
    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=128)
    project: str = Field(default="default", min_length=1, max_length=128)
    environment: str = Field(default="production", min_length=1, max_length=128)
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    model_tier: str | None = Field(default=None, max_length=32)


PUBLIC_AI_JOB_FIELDS = (
    "id",
    "request_id",
    "task_type",
    "project",
    "environment",
    "status",
    "stage",
    "cancel_requested",
    "result",
    "error_code",
    "error_message",
    "created_at",
    "updated_at",
    "finished_at",
    "expires_at",
    "notify",
    "terminal_event_id",
    "received_at",
)


def _serialize_job(job: dict) -> dict:
    serialized = {}
    for key in PUBLIC_AI_JOB_FIELDS:
        val = job.get(key)
        if key == "result" and not ai_result_available(job):
            val = None
        if isinstance(val, UUID):
            serialized[key] = str(val)
        elif isinstance(val, datetime):
            serialized[key] = val.isoformat()
        else:
            serialized[key] = val
    return serialized


def present_ai_job(db, settings, job):
    with db.connect() as conn:
        delivery = present_ai_delivery(conn, job, settings)
    return dict(_serialize_job(job), delivery=delivery)


async def run_n8n_workflow(settings: Settings, data: AiJobCreate) -> dict:
    if not settings.n8n_ai_webhook_url:
        raise HTTPException(503, "n8n_ai_webhook_not_configured")
    if not settings.n8n_compute_context_ready:
        raise HTTPException(503, "n8n_compute_context_not_ready")

    payload = {
        "request_id": data.request_id,
        "task_type": data.task_type,
        "prompt": data.prompt,
        "project": data.project,
        "environment": data.environment,
        "input": data.input,
    }
    lease = compute_context.get()
    if lease:
        # Workflow HTTP nodes forward this short-lived token only to internal compute APIs.
        # It is not a model input, permanent API key, or workflow return value.
        payload["compute_context"] = {
            "token": lease.token,
            "header": "X-Noedaeri-Compute-Token",
        }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(settings.n8n_ai_webhook_url, json=payload)
    except httpx.TimeoutException:
        raise HTTPException(504, "ai_execution_timeout") from None
    except httpx.RequestError:
        raise HTTPException(502, "n8n_network_error") from None

    if response.status_code != 200:
        raise HTTPException(502, f"n8n_execution_failed: status {response.status_code}")

    try:
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError()
        if lease and lease.token in json.dumps(result):
            raise ValueError()
        return result
    except Exception:
        raise HTTPException(502, "n8n_invalid_json_response") from None


def record_usage(
    conn,
    owner_id: UUID,
    project: str,
    environment: str,
    request_id: str,
    task_type: str,
    provider: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    model_tier: str | None = None,
    job_id: UUID | None = None,
):
    conn.execute(
        """
        INSERT INTO ai_usage (
            job_id, owner_id, project, environment, request_id, task_type,
            provider, model, prompt_tokens, completion_tokens, total_tokens, model_tier
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (owner_id, project, environment, request_id, provider, model) DO NOTHING
        """,
        (
            job_id,
            owner_id,
            project,
            environment,
            request_id,
            task_type,
            provider,
            model,
            prompt_tokens,
            completion_tokens,
            total_tokens,
            model_tier,
        ),
    )


async def execute_or_reuse_ai_job(
    db: Database, settings: Settings, owner_id: UUID, data: AiJobCreate
) -> dict:
    if data.notify:
        if owner_id != PLATFORM_OWNER:
            raise HTTPException(422, "ai_notifications_platform_only")
        if not settings.ai_webhook_url or not settings.webhook_secret:
            raise HTTPException(503, "ai_delivery_not_configured")
    if data.task_type == "text.translate" and (
        not settings.n8n_ai_webhook_url or not settings.n8n_compute_context_ready
    ):
        raise HTTPException(503, "translation_not_configured")
    with db.connect() as conn:
        job = conn.execute(
            "INSERT INTO ai_jobs(id,owner_id,project,environment,request_id,task_type,prompt,input,"
            "status,notify) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,'pending',%s) "
            "ON CONFLICT(owner_id,project,environment,request_id) DO NOTHING RETURNING *",
            (
                uuid4(),
                owner_id,
                data.project,
                data.environment,
                data.request_id,
                data.task_type,
                data.prompt,
                Jsonb(data.input),
                data.notify,
            ),
        ).fetchone()
        reused = job is None
        if reused:
            job = conn.execute(
                "SELECT * FROM ai_jobs WHERE owner_id=%s AND project=%s AND environment=%s "
                "AND request_id=%s",
                (owner_id, data.project, data.environment, data.request_id),
            ).fetchone()
            if (job["task_type"], job["prompt"], job["input"], job["notify"]) != (
                data.task_type,
                data.prompt,
                data.input,
                data.notify,
            ):
                raise HTTPException(409, "ai_request_id_conflict")
    if not data.sync or job["status"] not in {"pending", "running"}:
        return dict(_serialize_job(job), reused=reused)
    if job["status"] == "pending":
        result = await _run_and_save(db, settings, owner_id, job["id"], data)
        if result:
            return dict(result, reused=reused)
    # Another dispatcher owns execution. Waiting here never submits a second workflow.
    deadline = asyncio.get_running_loop().time() + settings.native_wait + 65
    while asyncio.get_running_loop().time() < deadline:
        with db.connect() as conn:
            job = conn.execute("SELECT * FROM ai_jobs WHERE id=%s", (job["id"],)).fetchone()
        if job["status"] not in {"pending", "running"}:
            return dict(_serialize_job(job), reused=True)
        await asyncio.sleep(0.1)
    raise HTTPException(504, "ai_execution_timeout")


async def _run_and_save(
    db: Database, settings: Settings, owner_id: UUID, job_id: UUID, data: AiJobCreate
) -> dict:
    with db.connect() as ownership:
        claimed = ownership.execute(
            "SELECT pg_try_advisory_lock(%s) AS ok", (guard_key(f"ai:{job_id}"),)
        ).fetchone()["ok"]
        ownership.commit()
        if not claimed:
            return None
        orphan = ownership.execute(
            "UPDATE ai_jobs SET status='failed',error_code='execution_unconfirmed',"
            "error_message='execution_unconfirmed',finished_at=now(),updated_at=now() "
            "WHERE id=%s AND status='running' RETURNING *",
            (job_id,),
        ).fetchone()
        ownership.commit()
        if orphan:
            # Reconcile history after restart, but never repeat an unknown external request.
            return _serialize_job(orphan)
        return await _execute_ai_job(db, settings, owner_id, job_id, data)


async def _execute_ai_job(db, settings, owner_id, job_id, data):
    with db.connect() as conn:
        claimed = conn.execute(
            "UPDATE ai_jobs SET status='running',stage='waiting_compute',updated_at=now() "
            "WHERE id=%s AND status='pending' RETURNING id",
            (job_id,),
        ).fetchone()
    if not claimed:
        return None

    def runnable():
        with db.connect() as conn:
            return bool(
                conn.execute(
                    "SELECT 1 FROM ai_jobs WHERE id=%s AND status='running' "
                    "AND NOT cancel_requested AND EXISTS (SELECT 1 FROM users "
                    "WHERE id=ai_jobs.owner_id AND status='approved')",
                    (job_id,),
                ).fetchone()
            )

    try:
        async with Compute(db, settings.storage_root).slot(
            "ai",
            job_id,
            "text.translate" if data.task_type == "text.translate" else "ai.workflow",
            owner_id,
            settings.native_wait,
            alive=runnable,
        ):
            with db.connect() as conn:
                conn.execute("UPDATE ai_jobs SET stage='executing' WHERE id=%s", (job_id,))
            n8n_result = await run_n8n_workflow(settings, data)
            if n8n_result.get("status") in {"failed", "error", "cancelled"}:
                # A finished workflow can report provider failure with HTTP 200.
                # This is a known terminal failure, not an unconfirmed remote execution.
                raise HTTPException(424, "n8n_workflow_failed")
            if data.task_type == "text.translate":
                translated = translation_result(
                    n8n_result, TranslationOptions.model_validate(data.input)
                )
                if translated is None:
                    raise HTTPException(502, "translation_invalid_result")
                n8n_result = translated
            with db.connect() as conn:
                conn.execute(
                    """
                    UPDATE ai_jobs
                    SET status=CASE WHEN cancel_requested THEN 'cancelled' ELSE 'succeeded' END,
                        result=CASE WHEN cancel_requested THEN NULL ELSE %s END,
                        stage='finished', finished_at=now(), updated_at=now(),
                        expires_at=now()+make_interval(secs=>%s)
                    WHERE id=%s AND status='running'
                    """,
                    (
                        Jsonb(n8n_result),
                        settings.platform_result_ttl
                        if owner_id == PLATFORM_OWNER
                        else settings.result_ttl,
                        job_id,
                    ),
                )

                usage = n8n_result.get("usage") or {}
                prompt_tokens = usage.get("prompt_tokens") or 0
                completion_tokens = usage.get("completion_tokens") or 0
                total_tokens = usage.get("total_tokens") or (prompt_tokens + completion_tokens)
                provider = n8n_result.get("provider") or "unknown"
                model = n8n_result.get("model") or "unknown"
                tier = n8n_result.get("model_tier")

                record_usage(
                    conn,
                    owner_id=owner_id,
                    project=data.project,
                    environment=data.environment,
                    request_id=data.request_id,
                    task_type=data.task_type,
                    provider=provider,
                    model=model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    model_tier=tier,
                    job_id=job_id,
                )

                job = conn.execute("SELECT * FROM ai_jobs WHERE id=%s", (job_id,)).fetchone()
                res = _serialize_job(job)
                res["reused"] = False
                return res
    except HTTPException as e:
        with db.connect() as conn:
            conn.execute(
                """
                UPDATE ai_jobs
                SET status=CASE WHEN cancel_requested THEN 'cancelled' ELSE 'failed' END,
                    error_code=%s, error_message=%s,
                    finished_at=now(), updated_at=now()
                WHERE id=%s AND status='running'
                """,
                (f"http_{e.status_code}", str(e.detail), job_id),
            )
        raise
    except Exception as exc:
        with db.connect() as conn:
            conn.execute(
                """
                UPDATE ai_jobs
                SET status=CASE WHEN cancel_requested THEN 'cancelled' ELSE 'failed' END,
                    error_code='internal_error', error_message=%s,
                    finished_at=now(), updated_at=now()
                WHERE id=%s AND status='running'
                """,
                ("ai_internal_execution_error", job_id),
            )
        raise HTTPException(500, "ai_internal_execution_error") from exc


async def process_ai_queue(db, settings):
    with db.connect() as conn:
        jobs = conn.execute(
            "SELECT * FROM ai_jobs WHERE status IN ('pending','running') "
            "ORDER BY created_at LIMIT 20"
        ).fetchall()
    for job in jobs:
        data = AiJobCreate(
            request_id=job["request_id"],
            task_type=job["task_type"],
            prompt=job["prompt"],
            project=job["project"],
            environment=job["environment"],
            input=job["input"],
            notify=job["notify"],
        )
        try:
            await _run_and_save(db, settings, job["owner_id"], job["id"], data)
        except HTTPException:
            pass


def install_ai_job_routes(app: FastAPI, db: Database, auth: Auth, settings: Settings):
    def get_settings(request: Request) -> Settings:
        return getattr(request.app.state, "settings", settings)

    def authenticate_caller(request: Request) -> tuple[UUID, bool]:
        curr_settings = get_settings(request)
        if request.url.path.startswith("/api/v1/"):
            if not curr_settings.integration_key or not secrets.compare_digest(
                request.headers.get("X-Noedaeri-API-Key", "").encode(),
                curr_settings.integration_key.encode(),
            ):
                raise HTTPException(401, "platform_key_required")
            return PLATFORM_OWNER, False
        user = auth.user(request)
        return user["id"], (user.get("role") == "admin")

    @app.post("/api/v1/translations", status_code=202)
    @app.post("/api/translations", status_code=202)
    async def create_translation(request: Request, data: TranslationRequest):
        owner_id, _ = authenticate_caller(request)
        job = AiJobCreate(
            request_id=data.request_id,
            task_type="text.translate",
            prompt=data.text,
            project=data.project,
            environment=data.environment,
            input={
                "source_language": data.source_language,
                "target_language": data.target_language,
            },
            sync=False,
            notify=data.notify,
        )
        return await execute_or_reuse_ai_job(db, get_settings(request), owner_id, job)

    @app.post("/api/v1/ai/jobs", status_code=200)
    async def create_platform_ai_job(request: Request, data: AiJobCreate):
        owner_id, _ = authenticate_caller(request)
        return await execute_or_reuse_ai_job(db, get_settings(request), owner_id, data)

    @app.post("/api/ai/jobs", status_code=200)
    async def create_web_ai_job(request: Request, data: AiJobCreate):
        owner_id, _ = authenticate_caller(request)
        return await execute_or_reuse_ai_job(db, get_settings(request), owner_id, data)

    @app.get("/api/v1/ai/jobs/{job_id}/translation.txt")
    @app.get("/api/ai/jobs/{job_id}/translation.txt")
    def download_translation(request: Request, job_id: UUID):
        owner_id, is_admin = authenticate_caller(request)
        with db.connect() as conn:
            job = conn.execute(
                "SELECT * FROM ai_jobs WHERE id=%s AND (%s OR owner_id=%s)",
                (job_id, is_admin, owner_id),
            ).fetchone()
        if not job or job["task_type"] != "text.translate":
            raise HTTPException(404, "ai_job_not_found")
        if not ai_result_available(job):
            raise HTTPException(410, "result_unavailable")
        if job["status"] != "succeeded" or not job["result"]:
            raise HTTPException(409, "result_unavailable")
        return Response(
            job["result"]["translated_text"],
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="translation.txt"'},
        )

    @app.get("/api/v1/ai/jobs/{job_id}")
    @app.get("/api/ai/jobs/{job_id}")
    def get_ai_job(request: Request, job_id: UUID):
        owner_id, is_admin = authenticate_caller(request)
        with db.connect() as conn:
            job = conn.execute(
                "SELECT * FROM ai_jobs WHERE id=%s AND (%s OR owner_id=%s)",
                (job_id, is_admin, owner_id),
            ).fetchone()
        if not job:
            raise HTTPException(404, "ai_job_not_found")
        return present_ai_job(db, get_settings(request), job)

    @app.get("/api/v1/ai/jobs")
    @app.get("/api/ai/jobs")
    def list_ai_jobs(
        request: Request,
        project: str | None = None,
        environment: str | None = None,
        status: str | None = None,
        task_type: str | None = None,
        limit: int = Query(default=50, ge=1, le=100),
    ):
        owner_id, is_admin = authenticate_caller(request)
        query = "SELECT * FROM ai_jobs WHERE (%s OR owner_id=%s)"
        params: list[Any] = [is_admin, owner_id]
        if project:
            query += " AND project=%s"
            params.append(project)
        if environment:
            query += " AND environment=%s"
            params.append(environment)
        if status:
            query += " AND status=%s"
            params.append(status)
        if task_type:
            query += " AND task_type=%s"
            params.append(task_type)
        query += " ORDER BY created_at DESC LIMIT %s"
        params.append(limit)

        with db.connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [present_ai_job(db, get_settings(request), r) for r in rows]

    class AiReceipt(BaseModel):
        model_config = ConfigDict(extra="forbid")
        event_id: UUID

    @app.post("/api/v1/ai/jobs/{job_id}/receipt")
    def receive_ai_result(request: Request, job_id: UUID, data: AiReceipt):
        owner_id, _ = authenticate_caller(request)
        collect_ai_events(db, job_id)
        with db.connect() as conn:
            job = conn.execute(
                "SELECT * FROM ai_jobs WHERE id=%s AND owner_id=%s FOR UPDATE",
                (job_id, owner_id),
            ).fetchone()
            if not job:
                raise HTTPException(404, "ai_job_not_found")
            if (
                not job["notify"]
                or job["status"] != "succeeded"
                or job["terminal_event_id"] != data.event_id
            ):
                raise HTTPException(409, "receipt_mismatch")
            if job["received_at"]:
                return {"accepted": True, "cleanup": "scheduled"}
            if not ai_result_available(job):
                raise HTTPException(410, "result_unavailable")
            conn.execute(
                "UPDATE ai_jobs SET received_at=now(),expires_at=now() WHERE id=%s", (job_id,)
            )
            conn.execute("UPDATE ai_deliveries SET state='acknowledged' WHERE job_id=%s", (job_id,))
        return {"accepted": True, "cleanup": "scheduled"}

    @app.post("/api/admin/ai/jobs/{job_id}/webhook-retry")
    def retry_ai_delivery(request: Request, job_id: UUID):
        auth.user(request, admin=True)
        with db.connect() as conn:
            row = conn.execute(
                "UPDATE ai_deliveries SET state='pending',attempts=0,next_attempt_at=now() "
                "WHERE job_id=%s AND state='failed' RETURNING id",
                (job_id,),
            ).fetchone()
        if not row:
            raise HTTPException(409, "delivery_not_failed")
        return {"accepted": True}

    @app.post("/api/v1/ai/jobs/{job_id}/cancel")
    @app.post("/api/ai/jobs/{job_id}/cancel")
    def cancel_ai_job(request: Request, job_id: UUID):
        owner_id, is_admin = authenticate_caller(request)
        with db.connect() as conn:
            row = conn.execute(
                """
                UPDATE ai_jobs
                SET cancel_requested=true,
                    status=CASE WHEN status='pending' THEN 'cancelled' ELSE status END,
                    updated_at=now(),
                    finished_at=CASE WHEN status='pending' THEN now() ELSE finished_at END
                WHERE id=%s AND (%s OR owner_id=%s) AND status IN ('pending','running')
                RETURNING id
                """,
                (job_id, is_admin, owner_id),
            ).fetchone()
        if not row:
            raise HTTPException(404, "ai_job_not_found_or_not_running")
        return {"cancelled": True}

    @app.get("/api/v1/ai/usage")
    @app.get("/api/ai/usage")
    def get_ai_usage(
        request: Request,
        project: str | None = None,
        environment: str | None = None,
        task_type: str | None = None,
        limit: int = Query(default=50, ge=1, le=200),
    ):
        owner_id, is_admin = authenticate_caller(request)
        summary_query = """
            SELECT provider, model, task_type,
                   COUNT(*)::int AS call_count,
                   COALESCE(SUM(prompt_tokens), 0)::int AS total_prompt_tokens,
                   COALESCE(SUM(completion_tokens), 0)::int AS total_completion_tokens,
                   COALESCE(SUM(total_tokens), 0)::int AS total_tokens
            FROM ai_usage
            WHERE (%s OR owner_id=%s)
        """
        records_query = """
            SELECT id, job_id, project, environment, request_id, task_type,
                   provider, model, prompt_tokens, completion_tokens, total_tokens,
                   model_tier, created_at
            FROM ai_usage
            WHERE (%s OR owner_id=%s)
        """
        params: list[Any] = [is_admin, owner_id]
        filter_clause = ""
        if project:
            filter_clause += " AND project=%s"
            params.append(project)
        if environment:
            filter_clause += " AND environment=%s"
            params.append(environment)
        if task_type:
            filter_clause += " AND task_type=%s"
            params.append(task_type)

        summary_query += (
            filter_clause + " GROUP BY provider, model, task_type ORDER BY call_count DESC"
        )
        records_query += filter_clause + " ORDER BY created_at DESC LIMIT %s"
        record_params = list(params) + [limit]

        with db.connect() as conn:
            summary = conn.execute(summary_query, tuple(params)).fetchall()
            records = conn.execute(records_query, tuple(record_params)).fetchall()

        def serialize_rec(r):
            rec = dict(r)
            if isinstance(rec.get("id"), UUID):
                rec["id"] = str(rec["id"])
            if isinstance(rec.get("job_id"), UUID):
                rec["job_id"] = str(rec["job_id"])
            if isinstance(rec.get("created_at"), datetime):
                rec["created_at"] = rec["created_at"].isoformat()
            return rec

        return {
            "summary": [dict(s) for s in summary],
            "records": [serialize_rec(r) for r in records],
        }

    @app.post("/internal/ai/usage", status_code=201)
    def internal_record_usage(request: Request, data: AiUsageReport):
        curr_settings = get_settings(request)
        worker_key = request.headers.get("X-Worker-Key") or ""
        raya_key = request.headers.get("X-Noedaeri-Raya-Key") or ""
        if worker_key != curr_settings.worker_key and raya_key != curr_settings.raya_key:
            raise HTTPException(403, "internal_auth_required")

        with db.connect() as conn:
            record_usage(
                conn,
                owner_id=PLATFORM_OWNER,
                project=data.project,
                environment=data.environment,
                request_id=data.request_id,
                task_type=data.task_type,
                provider=data.provider,
                model=data.model,
                prompt_tokens=data.prompt_tokens,
                completion_tokens=data.completion_tokens,
                total_tokens=data.total_tokens,
                model_tier=data.model_tier,
            )
        return {"accepted": True}


def cleanup_ai_results(db):
    with db.connect() as conn:
        conn.execute(
            "UPDATE ai_jobs SET result=NULL,prompt='',input='{}' "
            "WHERE status IN ('succeeded','failed','cancelled') "
            "AND expires_at<=now() AND (result IS NOT NULL OR prompt<>'' OR input<>'{}'::jsonb)"
        )
