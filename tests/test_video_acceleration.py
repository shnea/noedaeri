import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from noedaeri.config import Settings
from noedaeri.execution import hardware_encoder_lock
from noedaeri.media import JobCancelled, MediaError, video_encoding_slot, video_package


@pytest.mark.parametrize("value", [None, "", "auto", "libx264", "h264_videotoolbox"])
def test_encoder_configuration_is_optional(monkeypatch, value):
    monkeypatch.setenv("DATABASE_URL", "postgresql://database.example/app")
    monkeypatch.setenv("WORKER_API_KEY", "test-worker-key-" * 3)
    monkeypatch.setenv("PUBLIC_ORIGIN", "https://app.example")
    monkeypatch.setenv("PLATFORM_OIDC_REDIRECT_URI", "https://app.example/auth/callback")
    if value is None:
        monkeypatch.delenv("FFMPEG_VIDEO_ENCODER", raising=False)
    else:
        monkeypatch.setenv("FFMPEG_VIDEO_ENCODER", value)
    assert Settings.from_env().video_encoder == (value or "auto")


def test_hardware_wait_is_cancellable_and_bounded(tmp_path):
    stages = []
    calls = 0

    def alive():
        nonlocal calls
        calls += 1
        return calls < 3

    with hardware_encoder_lock(tmp_path):
        with (
            pytest.raises(JobCancelled),
            video_encoding_slot(tmp_path, "h264_videotoolbox", alive, lambda: 10, stages.append),
        ):
            pytest.fail("Hardware encoder assigned twice")
        assert stages == ["waiting_hardware_encoder"]

        def expired():
            raise MediaError("processing_timeout")

        with (
            pytest.raises(MediaError, match="processing_timeout"),
            video_encoding_slot(
                tmp_path, "h264_videotoolbox", lambda: True, expired, stages.append
            ),
        ):
            pytest.fail("Expired wait assigned hardware")

    with hardware_encoder_lock(tmp_path):
        pass


@pytest.mark.parametrize(
    "failure",
    ["hardware_encoding_failed", "processing_timeout", "storage_capacity_exceeded", "cancelled"],
)
def test_auto_fallback_cleans_partial_renditions_and_only_retries_encoder_failure(
    tmp_path, monkeypatch, failure
):
    from noedaeri import media

    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=s=1280x720:d=1",
            "-c:v",
            "libx264",
            str(source),
        ],
        check=True,
    )
    actual_run = media.run_process
    attempts = []
    stages = []
    target = tmp_path / "result"

    def run(args, *positional, **kwargs):
        if "-c:v" in args:
            selected = args[args.index("-c:v") + 1]
            attempts.append(selected)
            if selected == "h264_videotoolbox":
                if attempts.count(selected) == 1:
                    (target / "480p-00099.ts").write_bytes(b"partial hardware output")
                    return
                if failure == "cancelled":
                    raise JobCancelled()
                raise MediaError(failure)
        return actual_run(args, *positional, **kwargs)

    monkeypatch.setattr(media.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(media, "run_process", run)
    if failure == "hardware_encoding_failed":
        result = video_package(
            source,
            target,
            0,
            30,
            lambda: True,
            stages.append,
            encoder="auto",
            resource_root=tmp_path,
        )
        assert result["video_encoder"] == "libx264"
        assert result["hardware_fallback"] is True
        assert len(result["variants"]) == 2
        assert not (target / "480p-00099.ts").exists()
        assert "480p-00099.ts" not in result["files"]
        assert attempts == ["h264_videotoolbox"] * 2 + ["libx264"] * 2
        assert "cpu_fallback" in stages
    else:
        expected = JobCancelled if failure == "cancelled" else MediaError
        with pytest.raises(expected):
            video_package(
                source,
                target,
                0,
                30,
                lambda: True,
                stages.append,
                encoder="auto",
                resource_root=tmp_path,
            )
        assert attempts == ["h264_videotoolbox"] * 2
        assert "cpu_fallback" not in stages
    with hardware_encoder_lock(tmp_path):
        pass


def test_encoder_slot_survives_worker_crash(tmp_path):
    marker = tmp_path / "child-pid"
    script = """
import sys
from pathlib import Path
from noedaeri.execution import hardware_encoder_lock
from noedaeri.media import run_process
with hardware_encoder_lock(Path(sys.argv[1])):
    child = ('import os,sys,time;from pathlib import Path;'
             'Path(sys.argv[1]).write_text(str(os.getpid()));time.sleep(30)')
    run_process([sys.executable, '-c', child, sys.argv[2]], 60, lambda: True)
"""
    parent = subprocess.Popen(
        [sys.executable, "-c", script, str(tmp_path), str(marker)],
        env=dict(os.environ, PYTHONPATH=str(Path("src").resolve())),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    child = None
    try:
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert marker.exists()
        child = int(marker.read_text())
        parent.kill()
        parent.wait(timeout=5)
        with pytest.raises(BlockingIOError), hardware_encoder_lock(tmp_path):
            pytest.fail("Orphan child still owns encoder")
        os.kill(child, signal.SIGTERM)
        deadline = time.monotonic() + 5
        while True:
            try:
                with hardware_encoder_lock(tmp_path):
                    break
            except BlockingIOError:
                assert time.monotonic() < deadline
                time.sleep(0.05)
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=5)
        if child:
            try:
                os.kill(child, signal.SIGTERM)
            except ProcessLookupError:
                pass


def test_hardware_failure_has_no_software_retry(tmp_path, monkeypatch):
    from noedaeri import media

    encoders = []

    def run(args, *_, **kwargs):
        if args[0] == "ffprobe":
            return '{"format":{"duration":"1"},"streams":[{"width":640,"height":480}]}'
        encoders.append(args[args.index("-c:v") + 1])
        assert args[args.index("-allow_sw") + 1] == "0"
        raise MediaError(kwargs["failure_code"])

    monkeypatch.setattr(media, "thumbnail", lambda *args: None)
    monkeypatch.setattr(media, "run_process", run)
    with pytest.raises(MediaError, match="hardware_encoding_failed"):
        video_package(
            tmp_path / "input",
            tmp_path / "out",
            0,
            30,
            lambda: True,
            lambda _: None,
            encoder="h264_videotoolbox",
            resource_root=tmp_path,
        )
    assert encoders == ["h264_videotoolbox"]
    with hardware_encoder_lock(tmp_path):
        pass
