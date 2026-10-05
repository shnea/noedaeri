"""Bounded, serialized routing in an isolated, lazily loaded CPU process."""

import asyncio
import fcntl
import json
import math
import os
import selectors
import signal
import subprocess
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

TIERS = ("L1", "L2", "L3", "L4")
ROOT = Path(__file__).resolve().parents[2]


class RouteInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    task_type: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_.-]*$")
    prompt: str = Field(min_length=1, max_length=16000)
    instruction: str = Field(default="", max_length=8000)
    has_images: bool = Field(default=False, strict=True)


class RouteResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_type: str
    model_tier: Literal["L1", "L2", "L3", "L4"]
    probabilities: dict[str, float]
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    input_tokens: int = Field(ge=1, le=512)
    input_truncated: bool
    inference_ms: float = Field(ge=0, allow_inf_nan=False)
    model: Literal["TextCortex/raya"]
    revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    device: Literal["cpu"]
    runtime: Literal["onnx-fp32"]


class RayaError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class RayaPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    minimum_keep_seconds: int = Field(ge=0, le=86400)
    idle_seconds: int = Field(ge=1, le=86400)


class Raya:
    def __init__(self, settings):
        self.settings = settings
        self.lock = threading.Lock()
        self.process = None
        self.execution_lock = None
        self.state = "off" if settings.raya_enabled else "disabled"
        self.error = None
        self.started = self.last_used = 0
        self.closed = False
        self.waiting = 0

    def snapshot(self):
        if self.process is not None and self.process.poll() is not None:
            state = "error"
        else:
            state = self.state
        return {
            "state": state,
            "error_code": self.error or ("raya_process_failed" if state == "error" else None),
            "waiting": self.waiting,
            "device": "cpu",
            "runtime": "onnx-fp32",
            "max_tokens": 512,
            "minimum_keep_seconds": self.settings.raya_minimum_keep,
            "idle_seconds": self.settings.raya_idle,
            "wait_seconds": self.settings.raya_wait,
            "timeout_seconds": self.settings.raya_timeout,
            "memory_reserve_bytes": self.settings.raya_memory_reserve,
        }

    def _stop(self):
        if self.process is None:
            if self.execution_lock is not None:
                self.execution_lock.close()
                self.execution_lock = None
            return
        self.state = "stopping"
        process = self.process
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=3)
        for stream in (process.stdin, process.stdout):
            stream.close()
        self.process = None
        if self.execution_lock is not None:
            self.execution_lock.close()
            self.execution_lock = None
        self.state = "off"

    def _read(self, deadline):
        line = bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdout, selectors.EVENT_READ)
            while not line.endswith(b"\n"):
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise RayaError("raya_timeout")
                chunk = os.read(self.process.stdout.fileno(), 65537 - len(line))
                if not chunk:
                    raise RayaError("raya_process_failed")
                line.extend(chunk)
                if len(line) > 65536:
                    raise RayaError("raya_process_failed")
        value = json.loads(line)
        if not isinstance(value, dict):
            raise RayaError("raya_process_failed")
        if "error" in value:
            code = value["error"]
            if code not in {
                "raya_memory_unavailable",
                "raya_loading_failed",
                "raya_inference_failed",
            }:
                code = "raya_process_failed"
            raise RayaError(code)
        return value

    def _write(self, payload, deadline):
        pending = memoryview((json.dumps(payload, ensure_ascii=False) + "\n").encode())
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdin, selectors.EVENT_WRITE)
            while pending:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise RayaError("raya_timeout")
                try:
                    count = os.write(self.process.stdin.fileno(), pending[:4096])
                except BlockingIOError:
                    continue
                pending = pending[count:]

    def route(self, payload):
        if not self.settings.raya_enabled:
            raise RayaError("raya_not_configured")
        self.waiting += 1
        try:
            acquired = self.lock.acquire(timeout=self.settings.raya_wait)
        finally:
            self.waiting -= 1
        if not acquired:
            raise RayaError("raya_busy")
        started = time.monotonic()
        try:
            if self.closed:
                raise RayaError("raya_not_configured")
            if self.state == "error" and self.process is not None:
                self._stop()
            deadline = started + self.settings.raya_timeout
            cold = self.process is None or self.process.poll() is not None
            if cold:
                self._stop()
                self.state = "loading"
                lock_root = ROOT / "models"
                lock_root.mkdir(exist_ok=True, mode=0o700)
                self.execution_lock = (lock_root / ".raya-execution.lock").open("a+b")
                try:
                    fcntl.flock(self.execution_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise RayaError("raya_busy") from None
                self.process = subprocess.Popen(
                    [
                        str(ROOT / "raya_runtime/.venv/bin/python"),
                        str(ROOT / "scripts/raya_inference.py"),
                        str(self.settings.raya_model_root),
                        str(self.settings.raya_memory_reserve),
                    ],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                    bufsize=0,
                    pass_fds=(self.execution_lock.fileno(),),
                    cwd=self.settings.raya_model_root,
                    env={
                        "PATH": os.environ.get("PATH", ""),
                        "HF_HUB_OFFLINE": "1",
                        "TRANSFORMERS_OFFLINE": "1",
                        "TOKENIZERS_PARALLELISM": "false",
                        "OMP_NUM_THREADS": "8",
                        "PYTHONUNBUFFERED": "1",
                    },
                )
                os.set_blocking(self.process.stdin.fileno(), False)
                if self._read(deadline) != {"ready": True}:
                    raise RayaError("raya_loading_failed")
                self.started = time.monotonic()
            self.state = "processing"
            self._write(payload, deadline)
            result = RouteResult.model_validate(self._read(deadline)).model_dump()
            probs = result["probabilities"]
            if (
                set(probs) != set(TIERS)
                or any(not math.isfinite(p) or not 0 <= p <= 1 for p in probs.values())
                or abs(sum(probs.values()) - 1) > 0.001
                or result["task_type"] != payload["task_type"]
            ):
                raise RayaError("raya_process_failed")
            self.last_used = time.monotonic()
            self.state, self.error = "ready", None
            result.update(cold_start=cold, elapsed_ms=round((self.last_used - started) * 1000, 2))
            return result
        except (RayaError, OSError, ValueError, subprocess.SubprocessError) as error:
            self._stop()
            self.state = "error"
            self.error = error.code if isinstance(error, RayaError) else "raya_process_failed"
            raise RayaError(self.error) from None
        finally:
            self.lock.release()

    def reap_idle(self):
        if not self.lock.acquire(blocking=False):
            return
        try:
            now = time.monotonic()
            if self.process is not None and (
                now - self.last_used >= self.settings.raya_idle
                and now - self.started >= self.settings.raya_minimum_keep
            ):
                self._stop()
        finally:
            self.lock.release()

    def close(self):
        self.closed = True
        with self.lock:
            self._stop()

    def release(self):
        if not self.lock.acquire(blocking=False):
            raise RayaError("raya_busy")
        try:
            self._stop()
            self.state = "off" if self.settings.raya_enabled else "disabled"
            self.error = None
        finally:
            self.lock.release()

    def set_policy(self, db, policy):
        if not self.lock.acquire(blocking=False):
            raise RayaError("raya_busy")
        try:
            with db.connect() as conn:
                conn.execute(
                    "INSERT INTO raya_policy VALUES(true,%s,%s) ON CONFLICT(singleton) "
                    "DO UPDATE SET minimum_keep_seconds=excluded.minimum_keep_seconds, "
                    "idle_seconds=excluded.idle_seconds",
                    (policy.minimum_keep_seconds, policy.idle_seconds),
                )
            self.settings = replace(
                self.settings,
                raya_minimum_keep=policy.minimum_keep_seconds,
                raya_idle=policy.idle_seconds,
            )
        finally:
            self.lock.release()


def install_raya_routes(app, settings, auth, raya, db):
    import secrets

    slots = asyncio.Semaphore(2)

    @app.get("/api/admin/raya/status")
    def status(request: Request):
        auth.user(request, admin=True)
        return raya.snapshot()

    @app.post("/api/admin/raya/release")
    def release(request: Request):
        auth.user(request, admin=True)
        try:
            raya.release()
        except RayaError as error:
            raise HTTPException(429, error.code) from None
        return raya.snapshot()

    @app.patch("/api/admin/raya/policy")
    def policy(request: Request, body: RayaPolicy):
        auth.user(request, admin=True)
        try:
            raya.set_policy(db, body)
        except RayaError as error:
            raise HTTPException(429, error.code) from None
        return raya.snapshot()

    @app.post("/api/raya/route")
    @app.post("/api/ai/v1/raya/route")
    async def route(request: Request):
        if request.url.path.startswith("/api/ai/v1/"):
            if not settings.raya_key or not secrets.compare_digest(
                request.headers.get("X-Noedaeri-Raya-Key", "").encode(), settings.raya_key.encode()
            ):
                raise HTTPException(401, "raya_key_required")
        else:
            auth.user(request)
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 65536:
                raise HTTPException(413, "raya_request_too_large")
        try:
            payload = RouteInput.model_validate_json(bytes(raw)).model_dump()
        except ValidationError:
            raise HTTPException(422, "invalid_raya_request") from None
        if slots.locked():
            raise HTTPException(429, "raya_busy", headers={"Retry-After": "5"})
        await slots.acquire()
        try:
            return await asyncio.to_thread(raya.route, payload)
        except RayaError as error:
            code = (
                429 if error.code == "raya_busy" else 504 if error.code == "raya_timeout" else 503
            )
            raise HTTPException(code, error.code, headers={"Retry-After": "5"}) from None
        finally:
            slots.release()
