"""Typed sentence translation through the existing n8n job executor."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Language = Literal["ko", "en", "ja", "zh", "es", "fr", "de"]


class TranslationOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_language: Literal["auto", "ko", "en", "ja", "zh", "es", "fr", "de"] = "auto"
    target_language: Language


class TranslationRequest(TranslationOptions):
    notify: bool = False
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    request_id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=4000, pattern=r"^[^\x00-\x08\x0b\x0c\x0e-\x1f]+$")
    project: str = Field(default="default", min_length=1, max_length=128)
    environment: str = Field(default="production", min_length=1, max_length=128)


def translation_result(result, options):
    """Reject malformed/truncated results rather than reporting a successful translation."""
    parsed = result.get("result") or result.get("ai_result")
    if isinstance(parsed, str):
        import json

        try:
            parsed = json.loads(parsed)
        except ValueError:
            return None
    if not isinstance(parsed, dict):
        return None
    text = parsed.get("translated_text")
    if not isinstance(text, str) or not text.strip() or len(text) > 80000:
        return None
    return {
        "type": "text_translate",
        "translated_text": text.strip(),
        "source_language": options.source_language,
        "target_language": options.target_language,
        "provider": result.get("provider"),
        "model": result.get("model"),
        "usage": result.get("usage") or {},
    }
