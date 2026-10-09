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
    gemini_api_key: str = ""
    n8n_origin: str = ""
    n8n_ai_webhook_url: str = ""
    n8n_compute_context_ready: bool = False
    tts_enabled: bool = False
    ocr_enabled: bool = False
    ocr_timeout: int = 120
    image_input_limit: int = 200_000_000
    stt_enabled: bool = False
    stt_timeout: int = 900
    stt_max_duration: int = 3600
    stt_threads: int = 4
    tts_timeout: int = 600
    native_wait: int = 600
    tts_memory_limit: int = 6 * 1024**3
    voice_root: Path = Path(__file__).resolve().parents[2] / "data/tts/voices"
    voice_storage_limit: int = 512 * 1024**2
    voice_mount_root: Path | None = None

    @classmethod
    def from_env(cls):
        origin = os.environ.get("N8N_ORIGIN", "").rstrip("/")
        settings = cls(
            voice_mount_root=Path(
                os.environ.get("VOICE_STORAGE_MOUNT_ROOT")
                or os.environ["SERVICE_STORAGE_MOUNT_ROOT"]
            ).resolve()
            if os.environ.get("VOICE_STORAGE_MOUNT_ROOT")
            or os.environ.get("SERVICE_STORAGE_MOUNT_ROOT")
            else None,
            tts_enabled=os.environ.get("TTS_ENABLED", "0") == "1",
            ocr_enabled=os.environ.get("OCR_ENABLED", "0") == "1",
            ocr_timeout=int(os.environ.get("OCR_TIMEOUT_SECONDS", "120")),
            image_input_limit=int(os.environ.get("IMAGE_MAX_INPUT_BYTES", "200000000")),
            stt_enabled=os.environ.get("STT_ENABLED", "0") == "1",
            stt_timeout=int(os.environ.get("STT_TIMEOUT_SECONDS", "900")),
            stt_max_duration=int(os.environ.get("STT_MAX_DURATION_SECONDS", "3600")),
            stt_threads=int(os.environ.get("STT_CPU_THREADS", "4")),
            native_wait=int(os.environ.get("NATIVE_COMPUTE_WAIT_SECONDS", "600")),
            tts_timeout=int(os.environ.get("TTS_TIMEOUT_SECONDS", "600")),
            tts_memory_limit=int(os.environ.get("TTS_MEMORY_LIMIT_BYTES", str(6 * 1024**3))),
            voice_root=Path(
                os.environ.get(
                    "VOICE_STORAGE_ROOT",
                    str(
                        Path(
                            os.environ.get(
                                "SERVICE_STORAGE_ROOT",
                                os.environ.get(
                                    "STATE_ROOT", Path(__file__).resolve().parents[2] / "data"
                                ),
                            )
                        )
                        / "tts"
                        / "voices"
                    ),
                )
            ).resolve(),
            voice_storage_limit=int(os.environ.get("VOICE_STORAGE_MAX_BYTES", str(512 * 1024**2))),
            n8n_origin=origin,
            n8n_compute_context_ready=os.environ.get("N8N_COMPUTE_CONTEXT_READY", "0") == "1",
            n8n_ai_webhook_url=os.environ.get("N8N_AI_WEBHOOK_URL")
            or (f"{origin}/webhook/noedaeri-ai" if origin else ""),
            raya_enabled=os.environ.get("RAYA_ENABLED", "0") == "1",
            raya_key=os.environ.get("NOEDAERI_RAYA_API_KEY", ""),
            gemini_api_key=os.environ.get("GEMINI_API_KEY", ""),
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
        if not (30 <= settings.tts_timeout <= 3600 and settings.tts_memory_limit >= 4 * 1024**3):
            raise ValueError("Invalid TTS execution limits")
        if not 1 <= settings.native_wait <= 3600:
            raise ValueError("Invalid native compute wait limit")
        if not (
            30 <= settings.stt_timeout <= 7200
            and 1 <= settings.stt_max_duration <= 14400
            and 1 <= settings.stt_threads <= 8
        ):
            raise ValueError("Invalid STT execution limits")
        if settings.voice_root.is_relative_to(settings.storage_root):
            raise ValueError("Voice profiles must be outside temporary storage")
        if settings.voice_mount_root and not settings.voice_root.is_relative_to(
            settings.voice_mount_root
        ):
            raise ValueError("Voice storage must be inside its required mount")
        if settings.voice_storage_limit < 3_000_000:
            raise ValueError("Invalid voice storage limit")
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
        if settings.upload_limit <= 0 or settings.storage_limit <= settings.upload_limit:
            raise ValueError("Storage capacity must exceed the positive upload limit")
        if not 1 <= settings.image_input_limit <= 5 * 1024**3:
            raise ValueError("Image input limit must be between 1 byte and 5 GiB")
        if not 30 <= settings.ocr_timeout <= 600:
            raise ValueError("OCR timeout must be between 30 and 600 seconds")
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
