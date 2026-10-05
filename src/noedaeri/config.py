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
    upload_limit: int = 512 * 1024 * 1024
    storage_limit: int = 5 * 1024 * 1024 * 1024
    free_floor: int = 2 * 1024 * 1024 * 1024
    job_timeout: int = 120
    video_timeout: int = 1800
    lease_seconds: int = 30
    result_ttl: int = 86400

    @classmethod
    def from_env(cls):
        settings = cls(
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
