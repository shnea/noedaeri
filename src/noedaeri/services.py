"""Service-specific input validation stays outside the shared job queue."""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class UploadInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["upload"]


class ThumbnailOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seconds: float = Field(default=0, ge=0, le=3600, allow_inf_nan=False)


class ImageInput(UploadInput):
    extension: Literal[
        "png",
        "jpg",
        "jpeg",
        "jfif",
        "gif",
        "webp",
        "bmp",
        "ico",
        "tif",
        "tiff",
        "heic",
        "heif",
        "avif",
    ]


class ImageOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TranscriptionOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    language: Literal["auto", "ko", "en", "ja", "zh", "yue"] = "auto"
    use_itn: bool = True


class SpeechInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    type: Literal["text"]
    text: str = Field(min_length=1, max_length=4000)
    language: Literal[
        "Korean",
        "English",
        "Japanese",
        "Chinese",
        "German",
        "French",
        "Russian",
        "Portuguese",
        "Spanish",
        "Italian",
    ] = "Korean"
    requester_id: str | None = Field(default=None, min_length=1, max_length=128)
    project: str = Field(default="default", min_length=1, max_length=128)
    environment: str = Field(default="production", min_length=1, max_length=128)


class SpeechOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    voice_id: UUID | None = None
    instruct: str = Field(default="", max_length=300)


class VoiceRegistrationOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    voice_id: UUID


@dataclass(frozen=True)
class Service:
    kind: str
    service: str
    label: str
    input_type: str
    options: type[BaseModel]
    result_filename: str | None = None
    result_media_type: str | None = None
    input_model: type[BaseModel] = UploadInput


SERVICES = {
    "stt.transcribe": Service(
        "stt.transcribe",
        "stt",
        "음성 인식 · 텍스트 + 구간 시각",
        "upload",
        TranscriptionOptions,
    ),
    "tts.synthesize": Service(
        "tts.synthesize",
        "tts",
        "텍스트 음성 생성",
        "text",
        SpeechOptions,
        "speech.wav",
        "audio/wav",
        SpeechInput,
    ),
    "tts.voice.register": Service(
        "tts.voice.register",
        "tts",
        "목소리 등록 · 참조 음성 검증",
        "upload",
        VoiceRegistrationOptions,
        "reference.wav",
        "audio/wav",
    ),
    "image.package": Service(
        "image.package",
        "image",
        "이미지 통합 처리 · 썸네일 + WebP 미리보기",
        "upload",
        ImageOptions,
        input_model=ImageInput,
    ),
    "video.package": Service(
        "video.package",
        "ffmpeg",
        "영상 통합 처리 · 썸네일 + 해상도별 스트리밍",
        "upload",
        ThumbnailOptions,
    ),
    "video.thumbnail": Service(
        "video.thumbnail",
        "ffmpeg",
        "영상 썸네일",
        "upload",
        ThumbnailOptions,
        "thumbnail.jpg",
        "image/jpeg",
    ),
}

AI_TASK_TYPES = {
    "blog.tags": "블로그 태그 생성",
    "blog.summary": "블로그 요약",
    "article.draft": "글 초안 작성",
    "portfolio.search": "포트폴리오 검색",
    "ui.render": "UI 생성",
    "comment.generate": "댓글 생성",
    "document.analyze": "문서 분석",
    "code.analyze": "코드 분석",
    "chat.general": "일반 질답",
}


def service_catalog(settings):
    """Expose every implemented capability without changing media-job validation."""
    catalog = [
        {
            "kind": item.kind,
            "service": item.service,
            "label": item.label,
            "input_type": item.input_type,
            "options_schema": item.options.model_json_schema(),
            "interface": "tts" if item.service == "tts" else "media",
            "available": settings.tts_enabled
            if item.service == "tts"
            else (settings.stt_enabled if item.service == "stt" else True),
            "limits": {
                "max_duration_seconds": settings.stt_max_duration,
                "timeout_seconds": settings.stt_timeout,
                "cpu_threads": settings.stt_threads,
            }
            if item.service == "stt"
            else None,
        }
        for item in SERVICES.values()
    ]
    for kind, service, label, interface, available in (
        (
            "ai.workflow",
            "n8n",
            "AI 작업 · n8n 워크플로",
            "ai",
            bool(settings.n8n_ai_webhook_url) and settings.n8n_compute_context_ready,
        ),
        ("raya.route", "raya", "Raya 요청 난이도 판단", "raya", settings.raya_enabled),
        (
            "embedding.encode",
            "embedding",
            "텍스트 임베딩 생성",
            "embedding",
            bool(settings.gemini_api_key),
        ),
        (
            "indexing.documents",
            "indexing",
            "문서 색인 · 추가·교체·삭제",
            "indexing",
            bool(settings.gemini_api_key),
        ),
        ("indexing.search", "indexing", "벡터 검색 · RAG", "search", bool(settings.gemini_api_key)),
    ):
        catalog.append(
            {
                "kind": kind,
                "service": service,
                "label": label,
                "input_type": "text" if interface in {"ai", "embedding", "raya"} else "json",
                "interface": interface,
                "available": available,
                "unavailable_reason": "n8n의 공통 자원 헤더 연결·검수가 필요합니다."
                if interface == "ai" and not settings.n8n_compute_context_ready
                else None,
                "task_types": [
                    {"value": key, "label": value} for key, value in AI_TASK_TYPES.items()
                ]
                if interface == "ai"
                else [],
            }
        )
    return catalog
