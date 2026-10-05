import re
import shutil
from uuid import UUID

from .config import Settings
from .db import Database


class Storage:
    def __init__(self, settings: Settings):
        self.root = settings.storage_root.resolve()
        self.settings = settings
        for name in ("uploads", "jobs", "results"):
            (self.root / name).mkdir(parents=True, exist_ok=True, mode=0o700)

    def path(self, area: str, job_id: UUID, filename: str):
        if area not in {"uploads", "jobs", "results"} or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", filename
        ):
            raise ValueError("Invalid storage target")
        path = self.root / area / str(UUID(str(job_id))) / filename
        if not path.resolve().is_relative_to(self.root):
            raise ValueError("Unsafe storage target")
        return path

    def available(self, reserved: int):
        return (
            reserved
            + sum(
                p.stat().st_size
                for p in (self.root / "results").rglob("*")
                if p.is_file() and not p.is_symlink()
            )
            <= self.settings.storage_limit
            and shutil.disk_usage(self.root).free
            >= self.settings.free_floor + self.settings.upload_limit
        )

    def remove(self, area: str, job_id: UUID):
        folder = self.root / area / str(UUID(str(job_id)))
        if folder.is_symlink() or not folder.resolve().is_relative_to(self.root):
            raise ValueError("Unsafe cleanup target")
        if folder.exists():
            shutil.rmtree(folder)

    def cleanup(self, db: Database):
        with db.connect() as conn:
            conn.execute(
                "UPDATE jobs SET status='interrupted', error_code='lease_lost', updated_at=now() "
                "WHERE status='running' AND lease_until<now()"
            )
            conn.execute(
                "UPDATE jobs SET status='failed', error_code='input_timeout', finished_at=now() "
                "WHERE id IN (SELECT id FROM jobs WHERE status='uploading' "
                "AND updated_at<now()-interval '30 minutes' FOR UPDATE SKIP LOCKED)"
            )
            # Row locks prevent multiple cleaners racing and completion overwrites.
            jobs = conn.execute(
                "SELECT * FROM jobs WHERE status IN ('succeeded','failed','cancelled') "
                "AND (cleanup_state <> 'done' OR "
                "(expires_at<=now() AND result_state IN ('available','cleanup_failed'))) "
                "FOR UPDATE SKIP LOCKED LIMIT 100"
            ).fetchall()
            for job in jobs:
                try:
                    self.remove("uploads", job["id"])
                    self.remove("jobs", job["id"])
                    expired = conn.execute(
                        "SELECT %s <= now() AS expired", (job["expires_at"],)
                    ).fetchone()["expired"]
                    if job["status"] != "succeeded" or expired:
                        self.remove("results", job["id"])
                    conn.execute(
                        "UPDATE jobs SET cleanup_state='done', result_state=CASE "
                        "WHEN %s THEN 'expired' ELSE result_state END, "
                        "result=CASE WHEN %s THEN NULL ELSE result END WHERE id=%s",
                        (bool(expired), bool(expired), job["id"]),
                    )
                except (OSError, ValueError):
                    conn.execute(
                        "UPDATE jobs SET cleanup_state='failed', result_state=CASE "
                        "WHEN expires_at<=now() THEN 'cleanup_failed' ELSE result_state END "
                        "WHERE id=%s",
                        (job["id"],),
                    )
