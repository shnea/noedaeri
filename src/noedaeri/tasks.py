"""One authorized work history across media, workflows, indexing and direct inference."""

import asyncio
import secrets
from datetime import UTC, datetime
from functools import wraps
from uuid import uuid4

from fastapi import HTTPException
from psycopg.types.json import Jsonb

from .integration import PLATFORM_OWNER
from .services import AI_TASK_TYPES, SERVICES


def tracked_operation(db, settings, auth, principal, kind, title):
    """Keep existing synchronous response contracts while recording execution history."""

    def decorate(function):
        @wraps(function)
        async def execute(request):
            if request.url.path.startswith("/api/ai/v1/"):
                current = getattr(request.app.state, "settings", settings)
                if not current.raya_key or not secrets.compare_digest(
                    request.headers.get("X-Noedaeri-Raya-Key", "").encode(),
                    current.raya_key.encode(),
                ):
                    raise HTTPException(
                        401, "raya_key_required" if kind == "raya.route" else "ai_key_required"
                    )
                owner_id = PLATFORM_OWNER
            else:
                owner_id = principal(request)["id"]
            job_id = uuid4()
            with db.connect() as conn:
                conn.execute(
                    "INSERT INTO operation_jobs(id,owner_id,kind,title) VALUES(%s,%s,%s,%s)",
                    (job_id, owner_id, kind, title),
                )
            state, code, result = "succeeded", None, None
            try:
                response = await function(request)
                data = (
                    response.model_dump(mode="json")
                    if hasattr(response, "model_dump")
                    else response
                )
                # Vector arrays and retrieved document bodies stay with the response, not history.
                if kind == "embedding.encode":
                    result = {key: data[key] for key in ("model", "dimensions", "usage")}
                    result["vector_count"] = len(data["data"])
                elif kind == "indexing.search":
                    result = {
                        key: data[key]
                        for key in (
                            "collection",
                            "project",
                            "environment",
                            "total_candidates",
                            "matched_count",
                        )
                    }
                else:
                    result = data
                return response
            except asyncio.CancelledError:
                state, code = "interrupted", "execution_unconfirmed"
                raise
            except HTTPException as error:
                state, code = "failed", f"http_{error.status_code}"
                raise
            except Exception:
                state, code = "failed", "operation_failed"
                raise
            finally:
                current = getattr(request.app.state, "settings", settings)
                ttl = (
                    current.platform_result_ttl
                    if owner_id == PLATFORM_OWNER
                    else current.result_ttl
                )
                with db.connect() as conn:
                    conn.execute(
                        "UPDATE operation_jobs SET status=%s,error_code=%s,result=%s,"
                        "finished_at=now(),expires_at=now()+make_interval(secs=>%s) WHERE id=%s",
                        (state, code, Jsonb(result) if result is not None else None, ttl, job_id),
                    )

        return execute

    return decorate


def cleanup_operation_results(db):
    with db.connect() as conn:
        conn.execute(
            "UPDATE operation_jobs SET result=NULL WHERE expires_at<=now() AND result IS NOT NULL"
        )


def list_tasks(db, user, present_media, limit, offset, status=None, service=None):
    # Bound and order the combined set before reading potentially large result payloads.
    sources = (
        (
            "media",
            "jobs",
            "service",
            "CASE WHEN status='pending' THEN 'queued' ELSE status END",
            "owner_id=%s OR (%s AND origin='platform')",
        ),
        (
            "ai",
            "ai_jobs",
            "'n8n'",
            "CASE WHEN status='pending' THEN 'queued' ELSE status END",
            "owner_id=%s OR %s",
        ),
        (
            "indexing",
            "ai_indexing_jobs",
            "'indexing'",
            "CASE WHEN status='pending' THEN 'queued' ELSE status END",
            "owner_id=%s OR %s",
        ),
        ("operation", "operation_jobs", "split_part(kind,'.',1)", "status", "owner_id=%s OR %s"),
    )
    queries, parameters = [], []
    for source, table, service_column, state_column, permission in sources:
        queries.append(
            f"SELECT '{source}' AS source,id,created_at,{service_column} AS service,"
            f"{state_column} AS status FROM {table} WHERE ({permission})"
        )
        parameters.extend((user["id"], user["role"] == "admin"))
    query = "SELECT * FROM (" + " UNION ALL ".join(queries) + ") AS work WHERE true"
    for field, value in (("status", status), ("service", service)):
        if value:
            query += f" AND {field}=%s"
            parameters.append(value)
    query += " ORDER BY created_at DESC,source,id DESC LIMIT %s OFFSET %s"
    parameters.extend((limit, offset))
    tasks = []
    with db.connect() as conn:
        selected = conn.execute(query, parameters).fetchall()
        for row in selected:
            table = next(item[1] for item in sources if item[0] == row["source"])
            job = conn.execute(f"SELECT * FROM {table} WHERE id=%s", (row["id"],)).fetchone()
            if row["source"] == "media":
                data = present_media(job)
                title, kind = job["title"], job["kind"]
                label = SERVICES[kind].label if kind in SERVICES else kind
                executor = str(job["worker_id"])[:8] if job["worker_id"] else "배정 대기"
            elif row["source"] == "ai":
                from .ai_jobs import _serialize_job

                data = _serialize_job(job)
                if job["expires_at"] and job["expires_at"] <= datetime.now(UTC):
                    data["result"] = None
                title = AI_TASK_TYPES.get(job["task_type"], job["task_type"])
                kind, label, executor = "ai.workflow", title, "n8n"
            elif row["source"] == "indexing":
                from .ai_indexing import serialize_job

                data = serialize_job(job)
                modes = {
                    "upsert": "문서 추가·갱신",
                    "replace_all": "문서 전체 교체",
                    "delete": "문서 삭제",
                }
                label = modes.get(job["mode"], job["mode"])
                title, kind, executor = (
                    f"{label} · {job['collection']}",
                    "indexing.documents",
                    "색인 처리기",
                )
            else:
                data = dict(job)
                if job["expires_at"] and job["expires_at"] <= datetime.now(UTC):
                    data["result"] = None
                title, kind, label, executor = job["title"], job["kind"], job["title"], "직접 호출"
            tasks.append(
                {
                    "source": row["source"],
                    "id": job["id"],
                    "title": title,
                    "kind": kind,
                    "service": row["service"],
                    "label": label,
                    "status": row["status"],
                    "owner_id": job["owner_id"],
                    "origin": "platform" if job["owner_id"] == PLATFORM_OWNER else "web",
                    "executor": executor,
                    "created_at": job["created_at"],
                    "data": data,
                }
            )
    return tasks
