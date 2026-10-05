"""Local process ownership; children retain the lock if their worker disappears."""

import fcntl
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import UUID

inherited_lock = ContextVar("inherited_lock", default=None)
inherited_encoder_lock = ContextVar("inherited_encoder_lock", default=None)


@contextmanager
def hardware_encoder_lock(root):
    folder = root / "locks"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    # All local workers sharing this storage root use one hardware encoder session.
    with (folder / "videotoolbox.lock").open("a+b") as file:
        fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        token = inherited_encoder_lock.set(file.fileno())
        try:
            yield
        finally:
            inherited_encoder_lock.reset(token)
            # An orphan FFmpeg child retains this reservation until it exits.


@contextmanager
def execution_lock(root, job_id):
    folder = root / "locks"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = folder / (str(UUID(str(job_id))) + ".lock")
    # Keep the inode stable across recovery and cleanup; never unlink a held lock.
    with path.open("a+b") as file:
        fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        token = inherited_lock.set(file.fileno())
        try:
            yield
        finally:
            inherited_lock.reset(token)
            # Closing our descriptor preserves ownership held by an orphan child.
