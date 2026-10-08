"""Durable, owner-scoped document indexing and search using common embeddings."""

import asyncio
import hashlib
import json
import math
import secrets
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Query, Request, Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .ai_jobs import record_usage
from .auth import Auth
from .config import Settings
from .db import Database
from .embeddings import (
    DEFAULT_DIMENSIONS,
    DEFAULT_MODEL,
    MAX_BATCH_SIZE,
    MAX_TEXT_LENGTH,
    generate_embeddings,
)
from .integration import PLATFORM_OWNER
from .tasks import tracked_operation

MAX_REQUEST_BYTES = 1024 * 1024
MAX_SEARCH_DOCUMENTS = 10000


class DocumentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=256)
    title: str | None = Field(default=None, max_length=512)
    content: str = Field(min_length=1, max_length=MAX_TEXT_LENGTH)
    metadata: dict[str, Any] = Field(default_factory=dict)


class IndexingJobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=128)
    project: str = Field(default="default", min_length=1, max_length=128)
    environment: str = Field(default="production", min_length=1, max_length=128)
    collection: str = Field(default="portfolio", min_length=1, max_length=256)
    mode: Literal["upsert", "replace_all", "delete"] = "upsert"
    documents: list[DocumentInput] = Field(default_factory=list, max_length=MAX_BATCH_SIZE)
    delete_ids: list[str] = Field(default_factory=list, max_length=100)
    sync: bool = True

    @model_validator(mode="after")
    def validate_operation(self):
        ids = [doc.id for doc in self.documents]
        if len(set(ids)) != len(ids) or len(set(self.delete_ids)) != len(self.delete_ids):
            raise ValueError("duplicate_document_ids")
        if any(not item.strip() or len(item) > 256 for item in self.delete_ids):
            raise ValueError("invalid_delete_ids")
        if any(not doc.id.strip() or not doc.content.strip() for doc in self.documents):
            raise ValueError("empty_document")
        if any(len(document_text(doc)) > MAX_TEXT_LENGTH for doc in self.documents):
            raise ValueError("document_text_too_large")
        if self.mode == "delete" and (self.documents or not self.delete_ids):
            raise ValueError("delete_requires_only_delete_ids")
        if self.mode == "replace_all" and self.delete_ids:
            raise ValueError("replace_all_does_not_accept_delete_ids")
        if self.mode == "upsert" and not (self.documents or self.delete_ids):
            raise ValueError("empty_upsert")
        if set(ids) & set(self.delete_ids):
            raise ValueError("conflicting_document_ids")
        return self


class VectorSearchQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    owner_id: UUID | None = Field(default=None, description="관리자만 다른 소유자의 색인 조회 가능")
    query: str = Field(min_length=1, max_length=10000)
    collection: str = Field(default="portfolio", min_length=1, max_length=256)
    project: str = Field(default="default", min_length=1, max_length=128)
    environment: str = Field(default="production", min_length=1, max_length=128)
    limit: int = Field(default=5, ge=1, le=50)
    min_similarity: float = Field(default=0.0, ge=-1.0, le=1.0)


class IndexingJobResponse(BaseModel):
    id: UUID
    owner_id: UUID
    request_id: str
    project: str
    environment: str
    collection: str
    mode: str
    status: str
    document_count: int
    indexed_count: int
    deleted_count: int
    total_tokens: int
    result: dict[str, Any] | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None
    expires_at: datetime | None
    result_state: str
    reused: bool = False


def document_text(doc: DocumentInput) -> str:
    return f"{doc.title}\n{doc.content}" if doc.title else doc.content


def serialize_job(job: dict, reused=False) -> dict:
    result = IndexingJobResponse.model_validate({**job, "reused": reused}).model_dump(mode="json")
    if job["expires_at"] and job["expires_at"] <= datetime.now(UTC):
        result["result"] = None
        result["result_state"] = "expired"
    return result


def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    if not vec_a or len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b, strict=True))
    norm_a = sum(a * a for a in vec_a)
    norm_b = sum(b * b for b in vec_b)
    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0
    return max(-1.0, min(1.0, dot / math.sqrt(norm_a * norm_b)))


def lock_key(value: str) -> int:
    return int.from_bytes(hashlib.sha256(value.encode()).digest()[:8], "big", signed=True)


def request_hash(data: IndexingJobCreate) -> str:
    payload = data.model_dump(exclude={"sync", "request_id"})
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


async def execute_indexing_job(db, settings, owner_id, data):
    fingerprint = request_hash(data)
    with db.connect() as conn:
        job = conn.execute(
            """INSERT INTO ai_indexing_jobs
            (id,owner_id,project,environment,request_id,collection,mode,status,
             document_count,payload,request_hash,retention_seconds,expires_at)
            VALUES(%s,%s,%s,%s,%s,%s,%s,'pending',%s,%s,%s,%s,NULL)
            ON CONFLICT(owner_id,project,environment,request_id) DO NOTHING RETURNING *""",
            (
                uuid4(),
                owner_id,
                data.project,
                data.environment,
                data.request_id,
                data.collection,
                data.mode,
                len(data.documents),
                Jsonb(data.model_dump()),
                fingerprint,
                settings.platform_result_ttl if owner_id == PLATFORM_OWNER else settings.result_ttl,
            ),
        ).fetchone()
        if not job:
            existing = conn.execute(
                """SELECT * FROM ai_indexing_jobs
                WHERE owner_id=%s AND project=%s AND environment=%s AND request_id=%s""",
                (owner_id, data.project, data.environment, data.request_id),
            ).fetchone()
            if existing["request_hash"] != fingerprint:
                raise HTTPException(409, "indexing_request_id_conflict")
            return serialize_job(existing, reused=True)
    if data.sync:
        return await run_indexing_job(db, settings, job["id"])
    return serialize_job(job)


async def run_indexing_job(db: Database, settings: Settings, job_id: UUID) -> dict:
    # Session locks survive commits, and PostgreSQL releases them if this process dies.
    with db.connect() as conn:
        claimed = conn.execute(
            "SELECT pg_try_advisory_lock(%s) AS ok", (lock_key(f"index-job:{job_id}"),)
        ).fetchone()["ok"]
        job = conn.execute("SELECT * FROM ai_indexing_jobs WHERE id=%s", (job_id,)).fetchone()
        conn.commit()
        if not claimed:
            return serialize_job(job)
        if job["status"] not in {"pending", "running"}:
            return serialize_job(job)
        try:
            if job["status"] == "running":
                # A lost worker's provider call may have completed: never silently replay it.
                raise HTTPException(503, "indexing_execution_interrupted")
            data = IndexingJobCreate.model_validate(job["payload"])
            scope = (job["owner_id"], data.project, data.environment, data.collection)
            scope_key = lock_key("index-scope:" + ":".join(map(str, scope)))
            available = conn.execute(
                "SELECT pg_try_advisory_lock(%s) AS ok", (scope_key,)
            ).fetchone()["ok"]
            conn.commit()
            if not available:
                return serialize_job(job)
            user = conn.execute(
                "SELECT status FROM users WHERE id=%s", (job["owner_id"],)
            ).fetchone()
            if not user or user["status"] != "approved":
                raise HTTPException(403, "approval_required")
            conn.execute(
                "UPDATE ai_indexing_jobs SET status='running',updated_at=now() WHERE id=%s",
                (job_id,),
            )
            conn.commit()
            vectors, tokens = [], 0
            if data.documents:
                vectors, tokens = await generate_embeddings(
                    settings.gemini_api_key,
                    [document_text(doc) for doc in data.documents],
                    DEFAULT_MODEL,
                    DEFAULT_DIMENSIONS,
                )
                if len(vectors) != len(data.documents) or any(
                    len(v) != DEFAULT_DIMENSIONS
                    or not all(math.isfinite(x) for x in v)
                    or not any(v)
                    for v in vectors
                ):
                    raise HTTPException(502, "invalid_embedding_response")
                # Persist usage before index mutation so failed writes don't hide provider calls.
                with db.connect() as usage_conn:
                    record_usage(
                        usage_conn,
                        owner_id=job["owner_id"],
                        project=data.project,
                        environment=data.environment,
                        request_id=f"indexing:{job_id}",
                        task_type="indexing",
                        provider="google",
                        model=DEFAULT_MODEL,
                        prompt_tokens=tokens,
                        completion_tokens=0,
                        total_tokens=tokens,
                    )
            # Serialize publication; searches see the old or new collection, never half a replace.
            user = conn.execute(
                "SELECT status FROM users WHERE id=%s FOR SHARE", (job["owner_id"],)
            ).fetchone()
            if user["status"] != "approved":
                raise HTTPException(403, "approval_required")
            deleted = 0
            if data.mode == "replace_all":
                deleted = conn.execute(
                    "DELETE FROM ai_documents WHERE owner_id=%s AND project=%s "
                    "AND environment=%s AND collection=%s",
                    scope,
                ).rowcount
            elif data.delete_ids:
                deleted = conn.execute(
                    "DELETE FROM ai_documents WHERE owner_id=%s AND project=%s "
                    "AND environment=%s AND collection=%s AND document_id=ANY(%s)",
                    (*scope, data.delete_ids),
                ).rowcount
            for doc, vector in zip(data.documents, vectors, strict=True):
                conn.execute(
                    """INSERT INTO ai_documents
                    (owner_id,project,environment,collection,document_id,title,content,
                     metadata,embedding,token_count,model,dimensions)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(owner_id,project,environment,collection,document_id)
                    DO UPDATE SET title=EXCLUDED.title,content=EXCLUDED.content,
                    metadata=EXCLUDED.metadata,embedding=EXCLUDED.embedding,
                    token_count=EXCLUDED.token_count,model=EXCLUDED.model,
                    dimensions=EXCLUDED.dimensions,updated_at=now()""",
                    (
                        *scope,
                        doc.id,
                        doc.title,
                        doc.content,
                        Jsonb(doc.metadata),
                        Jsonb(vector),
                        tokens // len(data.documents),
                        DEFAULT_MODEL,
                        DEFAULT_DIMENSIONS,
                    ),
                )
            result = {
                "indexed_count": len(vectors),
                "deleted_count": deleted,
                "model": DEFAULT_MODEL,
                "dimensions": DEFAULT_DIMENSIONS,
                "total_tokens": tokens,
                "usage_estimated": True,
            }
            job = conn.execute(
                """UPDATE ai_indexing_jobs SET status='succeeded',indexed_count=%s,
                deleted_count=%s,total_tokens=%s,result=%s,payload=NULL,result_state='available',
                finished_at=now(),updated_at=now(),
                expires_at=now()+retention_seconds*interval '1 second'
                WHERE id=%s RETURNING *""",
                (len(vectors), deleted, tokens, Jsonb(result), job_id),
            ).fetchone()
            conn.commit()
        except (Exception, asyncio.CancelledError) as exc:
            conn.rollback()
            code = exc.detail if isinstance(exc, HTTPException) else "indexing_execution_failed"
            if isinstance(exc, asyncio.CancelledError):
                code = "indexing_execution_interrupted"
            job = conn.execute(
                """UPDATE ai_indexing_jobs SET status='failed',error_code=%s,error_message=%s,
                payload=NULL,finished_at=now(),updated_at=now(),
                expires_at=now()+retention_seconds*interval '1 second'
                WHERE id=%s RETURNING *""",
                (code, code, job_id),
            ).fetchone()
            conn.commit()
            if isinstance(exc, asyncio.CancelledError):
                raise
        return serialize_job(job)


async def process_indexing_queue(db, settings):
    with db.connect() as conn:
        jobs = conn.execute(
            "SELECT id FROM ai_indexing_jobs WHERE status IN ('pending','running') "
            "ORDER BY created_at LIMIT 20"
        ).fetchall()
    for job in jobs:
        await run_indexing_job(db, settings, job["id"])


def cleanup_indexing_results(db):
    with db.connect() as conn:
        conn.execute(
            "UPDATE ai_indexing_jobs SET result=NULL,result_state='expired' "
            "WHERE expires_at<=now() AND result_state='available'"
        )


async def read_payload(request, model):
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > MAX_REQUEST_BYTES:
            raise HTTPException(413, "indexing_request_too_large")
    try:
        return model.model_validate_json(bytes(raw))
    except ValidationError:
        raise HTTPException(422, "invalid_indexing_request") from None


def install_indexing_routes(app: FastAPI, db: Database, auth: Auth, settings: Settings):
    def get_settings(request):
        return getattr(request.app.state, "settings", settings)

    def authenticate_caller(request):
        current = get_settings(request)
        if request.url.path.startswith("/api/ai/v1/"):
            if not current.raya_key or not secrets.compare_digest(
                request.headers.get("X-Noedaeri-Raya-Key", "").encode(),
                current.raya_key.encode(),
            ):
                raise HTTPException(401, "ai_key_required")
            return PLATFORM_OWNER, False
        if request.url.path.startswith("/api/v1/"):
            if not current.integration_key or not secrets.compare_digest(
                request.headers.get("X-Noedaeri-API-Key", "").encode(),
                current.integration_key.encode(),
            ):
                raise HTTPException(401, "platform_key_required")
            return PLATFORM_OWNER, False
        user = auth.user(request)
        return user["id"], user["role"] == "admin"

    @app.post("/api/ai/v1/indexing", include_in_schema=False)
    @app.post(
        "/api/v1/ai/indexing",
        response_model=IndexingJobResponse,
        responses={202: {"model": IndexingJobResponse}, 409: {"description": "요청 ID 충돌"}},
        operation_id="create_platform_indexing_job",
    )
    @app.post(
        "/api/ai/indexing",
        response_model=IndexingJobResponse,
        responses={202: {"model": IndexingJobResponse}, 409: {"description": "요청 ID 충돌"}},
        operation_id="create_web_indexing_job",
    )
    async def create_indexing_job(request: Request, response: Response):
        owner, _ = authenticate_caller(request)
        data = await read_payload(request, IndexingJobCreate)
        job = await execute_indexing_job(db, get_settings(request), owner, data)
        response.status_code = 202 if job["status"] in {"pending", "running"} else 200
        return job

    @app.get("/api/ai/v1/indexing/collections", include_in_schema=False)
    @app.get("/api/v1/ai/indexing/collections", operation_id="get_platform_collections_overview")
    @app.get("/api/ai/indexing/collections", operation_id="get_web_collections_overview")
    def get_collections_overview(
        request: Request, project: str | None = None, environment: str | None = None
    ):
        owner, admin = authenticate_caller(request)
        with db.connect() as conn:
            return conn.execute(
                """SELECT owner_id,collection,project,environment,model,dimensions,
                COUNT(*)::int AS document_count,COALESCE(SUM(token_count),0)::int AS total_tokens,
                MAX(updated_at) AS last_updated_at FROM ai_documents
                WHERE (%s OR owner_id=%s) AND (%s::text IS NULL OR project=%s)
                AND (%s::text IS NULL OR environment=%s)
                GROUP BY owner_id,collection,project,environment,model,dimensions
                ORDER BY collection,project,environment""",
                (admin, owner, project, project, environment, environment),
            ).fetchall()

    @app.post("/api/ai/v1/indexing/search", include_in_schema=False)
    @app.post("/api/v1/ai/indexing/search", operation_id="search_platform_vector_index")
    @app.post("/api/ai/indexing/search", operation_id="search_web_vector_index")
    @tracked_operation(
        db,
        settings,
        auth,
        lambda request: {"id": authenticate_caller(request)[0]},
        "indexing.search",
        "벡터 검색",
    )
    async def search_vector_index(request: Request):
        caller, admin = authenticate_caller(request)
        query = await read_payload(request, VectorSearchQuery)
        owner = query.owner_id or caller
        if owner != caller and not admin:
            raise HTTPException(403, "index_owner_access_denied")
        clean_project = query.project.replace("-", "")
        clean_env = query.environment.replace("-", "")
        prefix = f"platform_{clean_project}_{clean_env}_"
        candidates = [query.collection]
        if query.collection.startswith(prefix):
            candidates.append(query.collection[len(prefix) :])
        elif not query.collection.startswith("platform_"):
            candidates.append(f"{prefix}{query.collection}")
        with db.connect() as conn:
            docs = conn.execute(
                """SELECT document_id,title,content,metadata,embedding FROM ai_documents
                WHERE owner_id=%s AND collection = ANY(%s) AND project=%s AND environment=%s
                AND model=%s AND dimensions=%s ORDER BY document_id LIMIT %s""",
                (
                    owner,
                    candidates,
                    query.project,
                    query.environment,
                    DEFAULT_MODEL,
                    DEFAULT_DIMENSIONS,
                    MAX_SEARCH_DOCUMENTS + 1,
                ),
            ).fetchall()
        if len(docs) > MAX_SEARCH_DOCUMENTS:
            raise HTTPException(422, "vector_search_collection_too_large")
        results = []
        if docs:
            vectors, tokens = await generate_embeddings(
                get_settings(request).gemini_api_key,
                [query.query],
                DEFAULT_MODEL,
                DEFAULT_DIMENSIONS,
            )
            if (
                len(vectors) != 1
                or len(vectors[0]) != DEFAULT_DIMENSIONS
                or not all(math.isfinite(x) for x in vectors[0])
                or not any(vectors[0])
            ):
                raise HTTPException(502, "invalid_embedding_response")
            with db.connect() as conn:
                record_usage(
                    conn,
                    owner_id=owner,
                    project=query.project,
                    environment=query.environment,
                    request_id=f"search:{uuid4()}",
                    task_type="vector_search",
                    provider="google",
                    model=DEFAULT_MODEL,
                    prompt_tokens=tokens,
                    completion_tokens=0,
                    total_tokens=tokens,
                )
            # Recheck approval after waiting for a provider response.
            if request.url.path.startswith("/api/ai/indexing"):
                user = auth.user(request)
                if owner != caller and user["role"] != "admin":
                    raise HTTPException(403, "index_owner_access_denied")
            for doc in docs:
                score = cosine_similarity(vectors[0], doc["embedding"])
                if score >= query.min_similarity:
                    results.append(
                        {key: doc[key] for key in ("document_id", "title", "content", "metadata")}
                        | {"similarity": round(score, 4)}
                    )
            results.sort(key=lambda item: item["similarity"], reverse=True)
        return {
            "owner_id": str(owner),
            "query": query.query,
            "collection": query.collection,
            "project": query.project,
            "environment": query.environment,
            "total_candidates": len(docs),
            "matched_count": len(results[: query.limit]),
            "results": results[: query.limit],
        }

    @app.get("/api/ai/v1/indexing/{job_id}", include_in_schema=False)
    @app.get(
        "/api/v1/ai/indexing/{job_id}",
        response_model=IndexingJobResponse,
        operation_id="get_platform_indexing_job",
    )
    @app.get(
        "/api/ai/indexing/{job_id}",
        response_model=IndexingJobResponse,
        operation_id="get_web_indexing_job",
    )
    def get_indexing_job(request: Request, job_id: UUID):
        owner, admin = authenticate_caller(request)
        with db.connect() as conn:
            job = conn.execute(
                "SELECT * FROM ai_indexing_jobs WHERE id=%s AND (%s OR owner_id=%s)",
                (job_id, admin, owner),
            ).fetchone()
        if not job:
            raise HTTPException(404, "indexing_job_not_found")
        return serialize_job(job)

    @app.get("/api/ai/v1/indexing", include_in_schema=False)
    @app.get(
        "/api/v1/ai/indexing",
        response_model=list[IndexingJobResponse],
        operation_id="list_platform_indexing_jobs",
    )
    @app.get(
        "/api/ai/indexing",
        response_model=list[IndexingJobResponse],
        operation_id="list_web_indexing_jobs",
    )
    def list_indexing_jobs(
        request: Request,
        collection: str | None = None,
        project: str | None = None,
        environment: str | None = None,
        status: str | None = None,
        limit: int = Query(default=50, ge=1, le=100),
    ):
        owner, admin = authenticate_caller(request)
        sql = "SELECT * FROM ai_indexing_jobs WHERE (%s OR owner_id=%s)"
        params: list[Any] = [admin, owner]
        for name, value in (
            ("collection", collection),
            ("project", project),
            ("environment", environment),
            ("status", status),
        ):
            if value:
                sql += f" AND {name}=%s"
                params.append(value)
        sql += " ORDER BY created_at DESC LIMIT %s"
        with db.connect() as conn:
            rows = conn.execute(sql, (*params, limit)).fetchall()
        return [serialize_job(row) for row in rows]

    @app.post("/api/ai/v1/indexing/{job_id}/cancel", include_in_schema=False)
    @app.post("/api/v1/ai/indexing/{job_id}/cancel", operation_id="cancel_platform_indexing_job")
    @app.post("/api/ai/indexing/{job_id}/cancel", operation_id="cancel_web_indexing_job")
    def cancel_indexing_job(request: Request, job_id: UUID):
        owner, admin = authenticate_caller(request)
        with db.connect() as conn:
            claimed = conn.execute(
                "SELECT pg_try_advisory_xact_lock(%s) AS ok", (lock_key(f"index-job:{job_id}"),)
            ).fetchone()["ok"]
            if not claimed:
                raise HTTPException(409, "indexing_job_not_pending")
            job = conn.execute(
                """UPDATE ai_indexing_jobs SET status='cancelled',payload=NULL,finished_at=now(),
                updated_at=now(),expires_at=now()+retention_seconds*interval '1 second'
                WHERE id=%s AND (%s OR owner_id=%s) AND status='pending' RETURNING *""",
                (job_id, admin, owner),
            ).fetchone()
        if not job:
            raise HTTPException(409, "indexing_job_not_pending")
        return serialize_job(job)

    for route in app.routes:
        if (
            getattr(route, "path", "")
            in {
                "/api/v1/ai/indexing",
                "/api/ai/indexing",
                "/api/v1/ai/indexing/search",
                "/api/ai/indexing/search",
            }
            and "POST" in route.methods
        ):
            model = VectorSearchQuery if route.path.endswith("/search") else IndexingJobCreate
            schema = model.model_json_schema()
            definitions = schema.pop("$defs", {})

            # Inline local references so generated OpenAPI remains self-contained.
            def inline(value, definitions=definitions):
                if isinstance(value, dict):
                    if "$ref" in value:
                        return inline(definitions[value["$ref"].split("/")[-1]])
                    return {key: inline(item) for key, item in value.items()}
                if isinstance(value, list):
                    return [inline(item) for item in value]
                return value

            route.openapi_extra = {
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": inline(schema)}},
                }
            }
