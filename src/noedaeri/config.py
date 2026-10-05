import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


@dataclass(frozen=True)
class Settings:
    database_url: str
    worker_key: str
    public_origin: str
    storage_root: Path
    issuer: str = ""
    client_id: str = "app"
    redirect_uri: str = ""
    admin_issuer: str = ""
    admin_subject: str = ""
    upload_limit: int = 5 * 1024 * 1024 * 1024
    storage_limit: int = 20 * 1024 * 1024 * 1024
    free_floor: int = 2 * 1024 * 1024 * 1024
    job_timeout: int = 120
    video_timeout: int = 1800
    lease_seconds: int = 30
    result_ttl: int = 86400
    integration_key: str = ""
    webhook_url: str = ""
    webhook_secret: str = ""
    platform_result_ttl: int = 604800
    video_encoder: str = "auto"
    raya_enabled: bool = False
    raya_key: str = ""
    raya_model_root: Path = Path(__file__).resolve().parents[2] / "models/raya"
    raya_minimum_keep: int = 60
    raya_idle: int = 300
    raya_wait: int = 5
    raya_timeout: int = 90
    raya_memory_reserve: int = 3 * 1024**3
    openrouter_key: str = ""
    groq_key: str = ""
    gemini_key: str = ""
    mistral_key: str = ""
    ai_l1_model: str = "openrouter/free"
    ai_l1_vision_model: str = "openrouter/free"
    ai_l2_model: str = "openai/gpt-oss-120b"
    ai_l2_vision_model: str = "qwen/qwen3.8-27b"
    ai_l3_model: str = "gemini-flash-latest"
    ai_l3_vision_model: str = "gemini-flash-latest"
    ai_fallback_model: str = "ministral-8b-latest"
    ai_fallback_vision_model: str = "ministral-8b-latest"
    ai_cache_ttl: int = 86400
    ai_timeout: int = 90

    @classmethod
    def from_env(cls):
        settings = cls(
            raya_enabled=os.environ.get("RAYA_ENABLED", "0") == "1",
            raya_key=os.environ.get("NOEDAERI_RAYA_API_KEY", ""),
            raya_model_root=Path(
                os.environ.get(
                    "RAYA_MODEL_ROOT", str(Path(__file__).resolve().parents[2] / "models/raya")
                )
            ).resolve(),
            raya_minimum_keep=int(os.environ.get("RAYA_MINIMUM_KEEP_SECONDS", "60")),
            raya_idle=int(os.environ.get("RAYA_IDLE_SECONDS", "300")),
            raya_wait=int(os.environ.get("RAYA_WAIT_SECONDS", "5")),
            raya_timeout=int(os.environ.get("RAYA_TIMEOUT_SECONDS", "90")),
            raya_memory_reserve=int(os.environ.get("RAYA_MEMORY_RESERVE_BYTES", str(3 * 1024**3))),
            openrouter_key=os.environ.get("OPENROUTER_API_KEY", ""),
            groq_key=os.environ.get("GROQ_API_KEY", ""),
            gemini_key=os.environ.get("GEMINI_API_KEY", ""),
            mistral_key=os.environ.get("MISTRAL_API_KEY", ""),
            **{
                field: os.environ[name]
                for field, name in (
                    ("ai_l1_model", "AI_L1_MODEL"),
                    ("ai_l1_vision_model", "AI_L1_VISION_MODEL"),
                    ("ai_l2_model", "AI_L2_MODEL"),
                    ("ai_l2_vision_model", "AI_L2_VISION_MODEL"),
                    ("ai_l3_model", "AI_L3_MODEL"),
                    ("ai_l3_vision_model", "AI_L3_VISION_MODEL"),
                    ("ai_fallback_model", "AI_FALLBACK_MODEL"),
                    ("ai_fallback_vision_model", "AI_FALLBACK_VISION_MODEL"),
                )
                if name in os.environ
            },
            ai_cache_ttl=int(os.environ.get("AI_CACHE_TTL_SECONDS", "86400")),
            ai_timeout=int(os.environ.get("AI_TIMEOUT_SECONDS", "90")),
            video_encoder=os.environ.get("FFMPEG_VIDEO_ENCODER") or "auto",
            integration_key=os.environ.get("NOEDAERI_PLATFORM_API_KEY", ""),
            webhook_url=os.environ.get("NOEDAERI_PLATFORM_WEBHOOK_URL", ""),
            webhook_secret=os.environ.get("NOEDAERI_PLATFORM_WEBHOOK_SECRET", ""),
            platform_result_ttl=int(os.environ.get("PLATFORM_RESULT_TTL_SECONDS", "604800")),
            upload_limit=int(os.environ.get("UPLOAD_MAX_BYTES", str(5 * 1024**3))),
            storage_limit=int(os.environ.get("STORAGE_MAX_BYTES", str(20 * 1024**3))),
            database_url=os.environ["DATABASE_URL"],
            worker_key=os.environ["WORKER_API_KEY"],
            public_origin=os.environ["PUBLIC_ORIGIN"].rstrip("/"),
            storage_root=Path(os.environ.get("STORAGE_ROOT", "tmp")).resolve(),
            issuer=os.environ.get("PLATFORM_OIDC_ISSUER", ""),
            client_id=os.environ.get("PLATFORM_OIDC_CLIENT_ID", "app"),
            redirect_uri=os.environ.get("PLATFORM_OIDC_REDIRECT_URI", ""),
            admin_issuer=os.environ.get("ADMIN_OIDC_ISSUER", ""),
            admin_subject=os.environ.get("ADMIN_OIDC_SUBJECT", ""),
        )
        if settings.video_encoder not in {"auto", "libx264", "h264_videotoolbox"}:
            raise ValueError("Unsupported FFMPEG_VIDEO_ENCODER")
        if settings.raya_enabled and len(settings.raya_key) < 32:
            raise ValueError("Raya requires a dedicated API key of at least 32 characters")
        if not (
            0 <= settings.raya_minimum_keep <= 86400
            and 1 <= settings.raya_idle <= 86400
            and 1 <= settings.raya_wait <= 30
            and 5 <= settings.raya_timeout <= 180
            and settings.raya_memory_reserve >= 1024**3
        ):
            raise ValueError("Invalid Raya execution policy")
        if not (0 <= settings.ai_cache_ttl <= 2592000 and 5 <= settings.ai_timeout <= 300):
            raise ValueError("Invalid AI cache retention or timeout")
        if settings.upload_limit <= 0 or settings.storage_limit <= settings.upload_limit:
            raise ValueError("Storage capacity must exceed the positive upload limit")
        if settings.integration_key and len(settings.integration_key) < 32:
            raise ValueError("Platform API key must contain at least 32 characters")
        if settings.webhook_secret and len(settings.webhook_secret) < 32:
            raise ValueError("Webhook secret must contain at least 32 characters")
        hook = urlparse(settings.webhook_url)
        if settings.webhook_url and (
            hook.scheme != "https"
            or not hook.hostname
            or hook.username
            or hook.password
            or hook.fragment
            or hook.query
            or not settings.webhook_secret
            or not settings.integration_key
        ):
            raise ValueError("Webhook requires fixed HTTPS URL without credentials/query/fragment")
        if not 3600 <= settings.platform_result_ttl <= 2592000:
            raise ValueError("Platform result retention must be between 1 hour and 30 days")
        if len(settings.worker_key) < 32:
            raise ValueError("WORKER_API_KEY must contain at least 32 characters")
        if urlparse(settings.public_origin).scheme != "https":
            raise ValueError("PUBLIC_ORIGIN must use HTTPS")
        if settings.redirect_uri != settings.public_origin + "/auth/callback":
            raise ValueError("OIDC callback must match PUBLIC_ORIGIN/auth/callback")
        if settings.issuer and urlparse(settings.issuer).scheme != "https":
            raise ValueError("OIDC issuer must use HTTPS")
        if bool(settings.admin_issuer) != bool(settings.admin_subject):
            raise ValueError("Both administrator identifiers must be configured together")
        return settings
