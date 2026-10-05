import re
import shutil
from uuid import UUID

from .config import Settings
from .db import Database
from .execution import execution_lock


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

    def available(self, reserved: int | None):
        # Admission reserves inputs; an executing job must count actual inputs
        # and intermediate files as well as its growing output.
        if reserved is None:
            reserved = sum(
                p.stat().st_size
                for area in ("uploads", "jobs")
                for p in (self.root / area).rglob("*")
                if p.is_file() and not p.is_symlink()
            )
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

    def reserve(self, db, job_id, token, amount):
        with db.connect() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(756903)")
            job = conn.execute(
                "SELECT * FROM jobs WHERE id=%s AND lease_token=%s AND status='running' "
                "AND lease_until>now() AND NOT cancel_requested FOR UPDATE",
                (job_id, token),
            ).fetchone()
            if not job:
                return "lease_lost"
            used = conn.execute(
                "SELECT COALESCE(sum(CASE WHEN status='uploading' THEN %s ELSE input_bytes END "
                "+CASE WHEN id=%s THEN 0 ELSE output_reserved END),0) AS bytes "
                "FROM jobs WHERE cleanup_state<>'done'",
                (self.settings.upload_limit, job_id),
            ).fetchone()["bytes"]
            others = conn.execute(
                "SELECT COALESCE(sum(output_reserved),0) AS bytes FROM jobs WHERE id<>%s", (job_id,)
            ).fetchone()["bytes"]
            if (
                not self.available(used + amount)
                or shutil.disk_usage(self.root).free < self.settings.free_floor + others + amount
            ):
                return "storage_capacity_exceeded"
            conn.execute("UPDATE jobs SET output_reserved=%s WHERE id=%s", (amount, job_id))
        return None

    def recover(self, db):
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT id FROM jobs WHERE status='interrupted' AND execution_guarded"
            ).fetchall()
        for row in rows:
            try:
                with execution_lock(self.root, row["id"]), db.connect() as conn:
                    conn.execute(
                        "UPDATE jobs SET status='failed', finished_at=now(), updated_at=now(), "
                        "output_reserved=0 WHERE id=%s AND status='interrupted' "
                        "AND execution_guarded",
                        (row["id"],),
                    )
            except BlockingIOError:
                continue

    def cleanup(self, db: Database):
        self.recover(db)
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
                    with execution_lock(self.root, job["id"]):
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
                except BlockingIOError:
                    continue
                except (OSError, ValueError):
                    conn.execute(
                        "UPDATE jobs SET cleanup_state='failed', result_state=CASE "
                        "WHEN expires_at<=now() THEN 'cleanup_failed' ELSE result_state END "
                        "WHERE id=%s",
                        (job["id"],),
                    )
