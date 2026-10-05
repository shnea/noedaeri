"""AI provider gateway: Raya tier choice, provider rotation, result cache and usage records.

n8n owns task flow and prompts. This module owns provider keys, the
L3 -> L2 -> L1 -> fallback rotation, exhaustion handling, cache and usage.
"""

import asyncio
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from typing import Literal

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .raya import RayaError

# Rotation ring. Starting at the recommended tier, walk forward once.
# L1 start: L1 -> FALLBACK -> L3 -> L2.
CYCLE = ("L3", "L2", "L1", "FALLBACK")
EXHAUSTED_MESSAGE = "현재 사용 가능한 AI 토큰이 없습니다. 한도 갱신 후 다시 시도해 주세요."
MAX_BODY = 12 * 1024 * 1024
DEFAULT_COOLDOWN = 60
MAX_COOLDOWN = 3600


@dataclass(frozen=True)
class Provider:
    slot: str
    name: str
    url: str
    key: str
    model: str
    vision_model: str


def providers(settings):
    return {
        "L1": Provider(
            "L1",
            "openrouter",
            "https://openrouter.ai/api/v1/chat/completions",
            settings.openrouter_key,
            settings.ai_l1_model,
            settings.ai_l1_vision_model,
        ),
        "L2": Provider(
            "L2",
            "groq",
            "https://api.groq.com/openai/v1/chat/completions",
            settings.groq_key,
            settings.ai_l2_model,
            settings.ai_l2_vision_model,
        ),
        "L3": Provider(
            "L3",
            "gemini",
            "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
            settings.gemini_key,
            settings.ai_l3_model,
            settings.ai_l3_vision_model,
        ),
        "FALLBACK": Provider(
            "FALLBACK",
            "mistral",
            "https://api.mistral.ai/v1/chat/completions",
            settings.mistral_key,
            settings.ai_fallback_model,
            settings.ai_fallback_vision_model,
        ),
    }


def rotation(tier):
    start = CYCLE.index(tier)
    return CYCLE[start:] + CYCLE[:start]


class GenerateInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    task_type: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_.-]*$")
    service: str = Field(default="n8n", min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_.-]*$")
    request_id: str | None = Field(default=None, min_length=1, max_length=120)
    prompt: str = Field(min_length=1, max_length=200000)
    instruction: str = Field(default="", max_length=40000)
    images: list[str] = Field(default_factory=list, max_length=4)
    tier: Literal["L1", "L2", "L3"] | None = None
    cache: bool = Field(default=True, strict=True)
    max_tokens: int | None = Field(default=None, ge=1, le=32768)
    temperature: float | None = Field(default=None, ge=0, le=2, allow_inf_nan=False)

    @field_validator("images")
    @classmethod
    def image_refs(cls, images):
        for image in images:
            if not (
                image.startswith(("data:image/png;base64,", "data:image/jpeg;base64,"))
                or image.startswith(("data:image/webp;base64,", "data:image/gif;base64,"))
                or image.startswith("https://")
            ):
                raise ValueError("images must be base64 data URLs or HTTPS URLs")
        return images


class ProviderFailure(Exception):
    def __init__(self, reason, http_status=None, cooldown=None):
        self.reason, self.http_status, self.cooldown = reason, http_status, cooldown
        super().__init__(reason)


def cache_key(body):
    material = {
        key: body[key]
        for key in ("task_type", "instruction", "prompt", "images", "max_tokens", "temperature")
    }
    encoded = json.dumps(material, ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def retry_after(response):
    try:
        return max(1, min(MAX_COOLDOWN, int(float(response.headers.get("Retry-After", "")))))
    except ValueError:
        return DEFAULT_COOLDOWN


class Gateway:
    def __init__(self, settings, db, raya):
        self.settings, self.db, self.raya = settings, db, raya
        self.providers = providers(settings)
        self.cooldown_until = {}
        self.transport = None

    def status(self):
        now = time.monotonic()
        return {
            "rotation": list(CYCLE),
            "cache_ttl_seconds": self.settings.ai_cache_ttl,
            "timeout_seconds": self.settings.ai_timeout,
            "providers": [
                {
                    "slot": p.slot,
                    "name": p.name,
                    "configured": bool(p.key),
                    "model": p.model,
                    "vision_model": p.vision_model or None,
                    "cooldown_seconds": max(0, round(self.cooldown_until.get(p.slot, 0) - now)),
                }
                for p in self.providers.values()
            ],
        }

    async def recommend(self, body):
        if body["tier"]:
            return body["tier"], None
        payload = {
            "task_type": body["task_type"],
            "prompt": body["prompt"][:16000],
            "instruction": body["instruction"][:8000],
            "has_images": bool(body["images"]),
        }
        try:
            result = await asyncio.to_thread(self.raya.route, payload)
            return result["model_tier"], None
        except RayaError as error:
            # Routing is advisory; start from the cheapest tier rather than refusing.
            return None, error.code

    async def call(self, client, provider, body):
        model = provider.vision_model if body["images"] else provider.model
        content = body["prompt"]
        if body["images"]:
            content = [{"type": "text", "text": body["prompt"]}] + [
                {"type": "image_url", "image_url": {"url": image}} for image in body["images"]
            ]
        messages = [{"role": "user", "content": content}]
        if body["instruction"]:
            messages.insert(0, {"role": "system", "content": body["instruction"]})
        request = {"model": model, "messages": messages}
        if body["max_tokens"] is not None:
            request["max_tokens"] = body["max_tokens"]
        if body["temperature"] is not None:
            request["temperature"] = body["temperature"]
        headers = {"Authorization": "Bearer " + provider.key}
        if provider.name == "openrouter":
            headers["X-Title"] = "noedaeri"
        try:
            response = await client.post(provider.url, json=request, headers=headers)
        except httpx.TimeoutException:
            raise ProviderFailure("timeout") from None
        except httpx.HTTPError:
            raise ProviderFailure("network_error") from None
        status = response.status_code
        if status == 429:
            raise ProviderFailure("exhausted", status, retry_after(response))
        if status == 402:
            raise ProviderFailure("exhausted", status, MAX_COOLDOWN)
        if status in (401, 403):
            raise ProviderFailure("auth_failed", status)
        if status >= 500:
            raise ProviderFailure("provider_error", status)
        if status >= 400:
            raise ProviderFailure("rejected", status)
        try:
            data = response.json()
            message = data["choices"][0]["message"]
            text = message.get("content")
            if isinstance(text, list):
                text = "".join(part.get("text", "") for part in text if isinstance(part, dict))
            if not isinstance(text, str) or not text.strip():
                raise ProviderFailure("empty_response", status)
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise ProviderFailure("invalid_response", status) from None
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}

        def count(name):
            value = usage.get(name)
            # Missing token counts stay unknown; never record them as zero.
            return value if isinstance(value, int) and value >= 0 else None

        return {
            "text": text,
            "model": data.get("model") if isinstance(data.get("model"), str) else model,
            "input_tokens": count("prompt_tokens"),
            "output_tokens": count("completion_tokens"),
        }

    def cached(self, key):
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT response FROM ai_cache WHERE key=%s AND expires_at>now()", (key,)
            ).fetchone()
        return row["response"] if row else None

    def store_cache(self, key, response):
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO ai_cache(key,response,expires_at) "
                "VALUES(%s,%s,now()+make_interval(secs=>%s)) ON CONFLICT(key) DO UPDATE SET "
                "response=excluded.response, created_at=now(), expires_at=excluded.expires_at",
                (key, Jsonb(response), self.settings.ai_cache_ttl),
            )

    def record(self, body, outcome):
        with self.db.connect() as conn:
            row = conn.execute(
                "INSERT INTO ai_usage(service,task_type,request_id,recommended_tier,raya_error,"
                "final_slot,provider,model,status,cache_hit,input_tokens,output_tokens,"
                "latency_ms,attempts,error_code) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                (
                    body["service"],
                    body["task_type"],
                    body["request_id"],
                    outcome.get("recommended_tier"),
                    outcome.get("raya_error"),
                    outcome.get("slot"),
                    outcome.get("provider"),
                    outcome.get("model"),
                    outcome["status"],
                    outcome.get("cache_hit", False),
                    outcome.get("input_tokens"),
                    outcome.get("output_tokens"),
                    outcome["latency_ms"],
                    Jsonb(outcome.get("attempts", [])),
                    outcome.get("error_code"),
                ),
            ).fetchone()
        return str(row["id"])

    async def generate(self, body):
        started = time.monotonic()

        def elapsed():
            return round((time.monotonic() - started) * 1000)

        key = cache_key(body)
        if body["cache"]:
            hit = await asyncio.to_thread(self.cached, key)
            if hit:
                outcome = {
                    "status": "cache_hit",
                    "cache_hit": True,
                    "slot": hit["slot"],
                    "provider": hit["provider"],
                    "model": hit["model"],
                    "latency_ms": elapsed(),
                }
                usage_id = await asyncio.to_thread(self.record, body, outcome)
                return 200, dict(hit, cache_hit=True, attempts=[], usage_id=usage_id)
        recommended, raya_error = await self.recommend(body)
        attempts = []
        timeout = httpx.Timeout(self.settings.ai_timeout, connect=10)
        async with httpx.AsyncClient(timeout=timeout, transport=self.transport) as client:
            for slot in rotation(recommended or "L1"):
                provider = self.providers[slot]
                attempt = {"slot": slot, "provider": provider.name}
                if not provider.key:
                    attempts.append(dict(attempt, result="not_configured"))
                    continue
                if body["images"] and not provider.vision_model:
                    attempts.append(dict(attempt, result="no_vision"))
                    continue
                if self.cooldown_until.get(slot, 0) > time.monotonic():
                    attempts.append(dict(attempt, result="cooldown"))
                    continue
                attempt_started = time.monotonic()
                try:
                    result = await self.call(client, provider, body)
                except ProviderFailure as failure:
                    if failure.cooldown:
                        self.cooldown_until[slot] = time.monotonic() + failure.cooldown
                    attempts.append(
                        dict(
                            attempt,
                            result=failure.reason,
                            http_status=failure.http_status,
                            ms=round((time.monotonic() - attempt_started) * 1000),
                        )
                    )
                    continue
                attempts.append(
                    dict(
                        attempt,
                        result="succeeded",
                        model=result["model"],
                        ms=round((time.monotonic() - attempt_started) * 1000),
                    )
                )
                response = {
                    "text": result["text"],
                    "slot": slot,
                    "provider": provider.name,
                    "model": result["model"],
                    "recommended_tier": recommended,
                    "usage": {
                        "input_tokens": result["input_tokens"],
                        "output_tokens": result["output_tokens"],
                    },
                }
                if body["cache"]:
                    await asyncio.to_thread(self.store_cache, key, response)
                outcome = dict(
                    status="succeeded",
                    recommended_tier=recommended,
                    raya_error=raya_error,
                    slot=slot,
                    provider=provider.name,
                    model=result["model"],
                    input_tokens=result["input_tokens"],
                    output_tokens=result["output_tokens"],
                    latency_ms=elapsed(),
                    attempts=attempts,
                )
                usage_id = await asyncio.to_thread(self.record, body, outcome)
                return 200, dict(
                    response,
                    cache_hit=False,
                    raya_error=raya_error,
                    attempts=attempts,
                    usage_id=usage_id,
                )
        results = {a["result"] for a in attempts}
        usable = results - {"not_configured", "no_vision"}
        if not usable:
            code, http, message = "ai_not_configured", 503, "사용할 수 있는 AI 공급자가 없습니다."
        elif usable <= {"exhausted", "cooldown"}:
            code, http, message = "ai_tokens_exhausted", 503, EXHAUSTED_MESSAGE
        else:
            code, http, message = "ai_providers_failed", 502, "모든 AI 공급자 호출이 실패했습니다."
        outcome = dict(
            status="exhausted" if code == "ai_tokens_exhausted" else "failed",
            recommended_tier=recommended,
            raya_error=raya_error,
            latency_ms=elapsed(),
            attempts=attempts,
            error_code=code,
        )
        usage_id = await asyncio.to_thread(self.record, body, outcome)
        return http, {
            "detail": code,
            "message": message,
            "recommended_tier": recommended,
            "raya_error": raya_error,
            "attempts": attempts,
            "usage_id": usage_id,
        }

    def cleanup(self):
        with self.db.connect() as conn:
            conn.execute("DELETE FROM ai_cache WHERE expires_at<=now()")

    def usage_summary(self, days):
        with self.db.connect() as conn:
            totals = conn.execute(
                "SELECT count(*) AS requests, "
                "count(*) FILTER (WHERE status='succeeded') AS succeeded, "
                "count(*) FILTER (WHERE status='cache_hit') AS cache_hits, "
                "count(*) FILTER (WHERE status='exhausted') AS exhausted, "
                "count(*) FILTER (WHERE status='failed') AS failed, "
                "sum(input_tokens) AS input_tokens, sum(output_tokens) AS output_tokens, "
                "count(*) FILTER (WHERE status='succeeded' AND "
                "(input_tokens IS NULL OR output_tokens IS NULL)) AS missing_token_counts "
                "FROM ai_usage WHERE created_at>now()-make_interval(days=>%s)",
                (days,),
            ).fetchone()
            by_provider = conn.execute(
                "SELECT provider, model, count(*) AS requests, sum(input_tokens) AS input_tokens, "
                "sum(output_tokens) AS output_tokens FROM ai_usage "
                "WHERE status='succeeded' AND created_at>now()-make_interval(days=>%s) "
                "GROUP BY provider, model ORDER BY requests DESC",
                (days,),
            ).fetchall()
            by_task = conn.execute(
                "SELECT service, task_type, count(*) AS requests, "
                "sum(input_tokens) AS input_tokens, sum(output_tokens) AS output_tokens "
                "FROM ai_usage WHERE created_at>now()-make_interval(days=>%s) "
                "GROUP BY service, task_type ORDER BY requests DESC",
                (days,),
            ).fetchall()
            recent = conn.execute(
                "SELECT id,created_at,service,task_type,request_id,recommended_tier,raya_error,"
                "final_slot,provider,model,status,cache_hit,input_tokens,output_tokens,"
                "latency_ms,attempts,error_code FROM ai_usage ORDER BY created_at DESC LIMIT 50"
            ).fetchall()
        return {
            "days": days,
            "totals": totals,
            "by_provider": by_provider,
            "by_task": by_task,
            "recent": recent,
        }


def install_ai_routes(app, settings, auth, gateway, principal=None):
    @app.get("/api/admin/ai/status")
    def ai_status(request: Request):
        auth.user(request, admin=True)
        return gateway.status()

    @app.get("/api/admin/ai/usage")
    def ai_usage(request: Request, days: int = 7):
        auth.user(request, admin=True)
        if not 1 <= days <= 90:
            raise HTTPException(422, "invalid_days")
        return gateway.usage_summary(days)

    @app.post("/api/ai/generate")
    @app.post("/api/ai/v1/generate")
    @app.post(
        "/api/v1/ai/generate",
        summary="AI 공급자 호출 (Raya 난이도 판단 → 순환 폴백 → 캐시 → 사용량 기록)",
    )
    async def generate(request: Request):
        if request.url.path.startswith("/api/v1/"):
            if principal:
                principal(request)
            else:
                auth.user(request)
        elif request.url.path.startswith("/api/ai/v1/"):
            if not settings.raya_key or not secrets.compare_digest(
                request.headers.get("X-Noedaeri-Raya-Key", "").encode(), settings.raya_key.encode()
            ):
                raise HTTPException(401, "raya_key_required")
        else:
            auth.user(request)
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > MAX_BODY:
                raise HTTPException(413, "ai_request_too_large")
        try:
            body = GenerateInput.model_validate_json(bytes(raw)).model_dump()
        except ValidationError:
            raise HTTPException(422, "invalid_ai_request") from None
        if request.url.path.startswith("/api/v1/") and body.get("service") == "n8n":
            body["service"] = "platform"
        elif request.url.path == "/api/ai/generate" and body.get("service") == "n8n":
            body["service"] = "web"
        status, payload = await gateway.generate(body)
        return JSONResponse(payload, status_code=status)
