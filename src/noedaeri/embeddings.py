"""Common AI embedding endpoint backed by Google AI Studio."""

import secrets

import httpx
from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .tasks import tracked_operation

DEFAULT_MODEL = "models/gemini-embedding-001"
DEFAULT_DIMENSIONS = 768
MAX_BATCH_SIZE = 100
MAX_TEXT_LENGTH = 16000


class EmbeddingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input: str | list[str] = Field(description="단일 문자열 또는 문자열 배열")
    model: str = Field(default=DEFAULT_MODEL, max_length=120)
    dimensions: int = Field(default=DEFAULT_DIMENSIONS, ge=64, le=3072)


class EmbeddingItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: int
    embedding: list[float]


class EmbeddingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str
    dimensions: int
    data: list[EmbeddingItem]
    usage: dict[str, int]


async def generate_embeddings(
    api_key: str, texts: list[str], model: str, dimensions: int
) -> tuple[list[list[float]], int]:
    if not api_key:
        raise HTTPException(503, "embedding_provider_not_configured")

    # Normalize model name
    normalized_model = model if model.startswith("models/") else f"models/{model}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        if len(texts) == 1:
            url = f"https://generativelanguage.googleapis.com/v1beta/{normalized_model}:embedContent?key={api_key}"
            payload = {
                "model": normalized_model,
                "content": {"parts": [{"text": texts[0]}]},
                "outputDimensionality": dimensions,
            }
            try:
                resp = await client.post(url, json=payload)
            except httpx.HTTPError:
                raise HTTPException(503, "embedding_provider_unavailable") from None

            if resp.status_code != 200:
                raise HTTPException(502, f"embedding_provider_error: {resp.status_code}")

            data = resp.json()
            embedding = data.get("embedding", {}).get("values", [])
            tokens = len(texts[0].split())
            return [embedding], tokens
        else:
            url = f"https://generativelanguage.googleapis.com/v1beta/{normalized_model}:batchEmbedContents?key={api_key}"
            requests = [
                {
                    "model": normalized_model,
                    "content": {"parts": [{"text": t}]},
                    "outputDimensionality": dimensions,
                }
                for t in texts
            ]
            try:
                resp = await client.post(url, json={"requests": requests})
            except httpx.HTTPError:
                raise HTTPException(503, "embedding_provider_unavailable") from None

            if resp.status_code != 200:
                raise HTTPException(502, f"embedding_provider_error: {resp.status_code}")

            data = resp.json()
            embeddings_list = [item.get("values", []) for item in data.get("embeddings", [])]
            tokens = sum(len(t.split()) for t in texts)
            return embeddings_list, tokens


def install_embedding_routes(app, settings, auth, principal, db):
    @app.post(
        "/api/v1/ai/embeddings",
        response_model=EmbeddingResponse,
        summary="공통 텍스트 임베딩 생성 (플랫폼)",
        openapi_extra={
            "description": (
                "Google Gemini 임베딩 모델로 단일 또는 복수 텍스트의 벡터를 생성합니다."
            )
        },
    )
    @app.post("/api/ai/v1/embeddings", response_model=EmbeddingResponse)
    @app.post("/api/ai/embeddings", response_model=EmbeddingResponse)
    @tracked_operation(db, settings, auth, principal, "embedding.encode", "텍스트 임베딩 생성")
    async def create_embeddings(request: Request):
        if request.url.path.startswith("/api/v1/"):
            principal(request)
        elif request.url.path.startswith("/api/ai/v1/"):
            if not settings.raya_key or not secrets.compare_digest(
                request.headers.get("X-Noedaeri-Raya-Key", "").encode(),
                settings.raya_key.encode(),
            ):
                raise HTTPException(401, "ai_key_required")
        else:
            auth.user(request)

        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 262144:  # 256 KiB
                raise HTTPException(413, "embedding_request_too_large")

        try:
            payload = EmbeddingInput.model_validate_json(bytes(raw))
        except ValidationError:
            raise HTTPException(422, "invalid_embedding_request") from None

        input_data = payload.input
        texts = [input_data] if isinstance(input_data, str) else input_data

        if not texts or len(texts) > MAX_BATCH_SIZE:
            raise HTTPException(422, f"batch_size_must_be_between_1_and_{MAX_BATCH_SIZE}")

        for t in texts:
            if not t or len(t) > MAX_TEXT_LENGTH:
                raise HTTPException(422, f"text_length_exceeded_{MAX_TEXT_LENGTH}")

        current_settings = getattr(request.app.state, "settings", settings)
        embeddings, tokens = await generate_embeddings(
            current_settings.gemini_api_key, texts, payload.model, payload.dimensions
        )

        items = [EmbeddingItem(index=idx, embedding=emb) for idx, emb in enumerate(embeddings)]

        return EmbeddingResponse(
            model=payload.model,
            dimensions=payload.dimensions,
            data=items,
            usage={"prompt_tokens": tokens, "total_tokens": tokens},
        )
