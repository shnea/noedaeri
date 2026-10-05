import asyncio
import secrets
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from .auth import Auth
from .config import Settings
from .db import Database
from .integration import PLATFORM_OWNER


class AiJobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=128)
    task_type: str = Field(min_length=1, max_length=64)
    prompt: str = Field(min_length=1, max_length=200000)
    project: str = Field(default="default", min_length=1, max_length=128)
    environment: str = Field(default="production", min_length=1, max_length=128)
    input: dict[str, Any] = Field(default_factory=dict)
    sync: bool = True


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
    "result",
    "error_code",
    "error_message",
    "created_at",
    "updated_at",
    "finished_at",
    "expires_at",
)


def _serialize_job(job: dict) -> dict:
    serialized = {}
    for key in PUBLIC_AI_JOB_FIELDS:
        val = job.get(key)
        if isinstance(val, UUID):
            serialized[key] = str(val)
        elif isinstance(val, datetime):
            serialized[key] = val.isoformat()
        else:
            serialized[key] = val
    return serialized


async def run_n8n_workflow(settings: Settings, data: AiJobCreate) -> dict:
    if not settings.n8n_ai_webhook_url:
        raise HTTPException(503, "n8n_ai_webhook_not_configured")

    payload = {
        "request_id": data.request_id,
        "task_type": data.task_type,
        "prompt": data.prompt,
        "project": data.project,
        "environment": data.environment,
        "input": data.input,
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(settings.n8n_ai_webhook_url, json=payload)
    except httpx.TimeoutException:
        raise HTTPException(504, "ai_execution_timeout") from None
    except httpx.RequestError as exc:
        raise HTTPException(502, f"n8n_network_error: {str(exc)[:100]}") from None

    if response.status_code != 200:
        raise HTTPException(502, f"n8n_execution_failed: status {response.status_code}")

    try:
        return response.json()
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
    with db.connect() as conn:
        existing = conn.execute(
            """
            SELECT * FROM ai_jobs
            WHERE owner_id=%s AND project=%s AND environment=%s AND request_id=%s
            """,
            (owner_id, data.project, data.environment, data.request_id),
        ).fetchone()

        if existing:
            if existing["status"] == "succeeded":
                res = _serialize_job(existing)
                res["reused"] = True
                return res
            if existing["status"] == "running":
                if not data.sync:
                    res = _serialize_job(existing)
                    res["reused"] = True
                    return res
                # If sync, poll for completion
                job_id = existing["id"]
            else:
                job_id = existing["id"]
        else:
            job_id = uuid4()
            conn.execute(
                """
                INSERT INTO ai_jobs (
                    id, owner_id, project, environment, request_id, task_type,
                    prompt, input, status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'running')
                """,
                (
                    job_id,
                    owner_id,
                    data.project,
                    data.environment,
                    data.request_id,
                    data.task_type,
                    data.prompt,
                    Jsonb(data.input),
                ),
            )

    if existing and existing["status"] == "running" and data.sync:
        for _ in range(120):
            await asyncio.sleep(0.5)
            with db.connect() as conn:
                polled = conn.execute("SELECT * FROM ai_jobs WHERE id=%s", (job_id,)).fetchone()
                if polled and polled["status"] in ("succeeded", "failed", "cancelled"):
                    res = _serialize_job(polled)
                    res["reused"] = True
                    return res
        raise HTTPException(504, "ai_execution_timeout")

    if not data.sync:
        asyncio.create_task(_async_worker(db, settings, owner_id, job_id, data))
        with db.connect() as conn:
            job = conn.execute("SELECT * FROM ai_jobs WHERE id=%s", (job_id,)).fetchone()
            res = _serialize_job(job)
            res["reused"] = False
            return res

    return await _run_and_save(db, settings, owner_id, job_id, data)


async def _run_and_save(
    db: Database, settings: Settings, owner_id: UUID, job_id: UUID, data: AiJobCreate
) -> dict:
    try:
        n8n_result = await run_n8n_workflow(settings, data)
        with db.connect() as conn:
            conn.execute(
                """
                UPDATE ai_jobs
                SET status='succeeded', result=%s, finished_at=now(), updated_at=now()
                WHERE id=%s
                """,
                (Jsonb(n8n_result), job_id),
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
                SET status='failed', error_code=%s, error_message=%s,
                    finished_at=now(), updated_at=now()
                WHERE id=%s
                """,
                (f"http_{e.status_code}", str(e.detail), job_id),
            )
        raise
    except Exception as exc:
        with db.connect() as conn:
            conn.execute(
                """
                UPDATE ai_jobs
                SET status='failed', error_code='internal_error', error_message=%s,
                    finished_at=now(), updated_at=now()
                WHERE id=%s
                """,
                (str(exc)[:200], job_id),
            )
        raise HTTPException(500, "ai_internal_execution_error") from exc


async def _async_worker(
    db: Database, settings: Settings, owner_id: UUID, job_id: UUID, data: AiJobCreate
):
    try:
        await _run_and_save(db, settings, owner_id, job_id, data)
    except Exception:
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
            return PLATFORM_OWNER, True
        user = auth.user(request)
        return user["id"], (user.get("role") == "admin")

    @app.post("/api/v1/ai/jobs", status_code=200)
    async def create_platform_ai_job(request: Request, data: AiJobCreate):
        owner_id, _ = authenticate_caller(request)
        return await execute_or_reuse_ai_job(db, get_settings(request), owner_id, data)

    @app.post("/api/ai/jobs", status_code=200)
    async def create_web_ai_job(request: Request, data: AiJobCreate):
        owner_id, _ = authenticate_caller(request)
        return await execute_or_reuse_ai_job(db, get_settings(request), owner_id, data)

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
        return _serialize_job(job)

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
        return [_serialize_job(r) for r in rows]

    @app.post("/api/v1/ai/jobs/{job_id}/cancel")
    @app.post("/api/ai/jobs/{job_id}/cancel")
    def cancel_ai_job(request: Request, job_id: UUID):
        owner_id, is_admin = authenticate_caller(request)
        with db.connect() as conn:
            row = conn.execute(
                """
                UPDATE ai_jobs
                SET status='cancelled', updated_at=now(), finished_at=now()
                WHERE id=%s AND (%s OR owner_id=%s) AND status='running'
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
