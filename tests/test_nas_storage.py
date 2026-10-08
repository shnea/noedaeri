import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from noedaeri.config import Settings


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, Path("scripts") / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "status,mounted,expected",
    [(0, True, True), (17, True, True), (0, False, False), (17, False, False), (13, True, False)],
)
def test_native_mount_result_requires_expected_mount(monkeypatch, status, mounted, expected):
    module = load_script("nas_watch")
    values = {
        "SERVICE_STORAGE_SMB_URL": "smb://nas.example/share",
        "SERVICE_STORAGE_MOUNT_ROOT": "/example/mount",
        "SERVICE_STORAGE_SMB_USERNAME": "fixture-user",
        "SERVICE_STORAGE_SMB_PASSWORD": "fixture-secret",
    }

    def run(command, **kwargs):
        assert command == ["/example/helper"]
        assert "fixture-secret" not in " ".join(command)
        assert json.loads(kwargs["input"])["password"] == "fixture-secret"
        assert kwargs["timeout"] == 30
        assert "SERVICE_STORAGE_SMB_PASSWORD" not in kwargs["env"]
        return SimpleNamespace(
            returncode=0 if status == 0 else 1, stdout=json.dumps({"status": status}).encode()
        )

    monkeypatch.setattr(module.subprocess, "run", run)
    monkeypatch.setattr(module.os.path, "ismount", lambda root: mounted)
    assert module.mount_once(values, Path("/example/helper")) is expected


def test_native_mount_timeout_is_recoverable(monkeypatch):
    module = load_script("nas_watch")

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("mount", 30)

    monkeypatch.setattr(module.subprocess, "run", timeout)
    assert not module.mount_once(
        {
            "SERVICE_STORAGE_SMB_URL": "smb://nas.example/share",
            "SERVICE_STORAGE_MOUNT_ROOT": "/example/mount",
        },
        Path("/example/helper"),
    )


def test_service_storage_separates_tts_from_common_root(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "fixture")
    monkeypatch.setenv("WORKER_API_KEY", "x" * 32)
    monkeypatch.setenv("PUBLIC_ORIGIN", "https://fixture.example")
    monkeypatch.setenv("PLATFORM_OIDC_REDIRECT_URI", "https://fixture.example/auth/callback")
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path / "temporary"))
    monkeypatch.setenv("SERVICE_STORAGE_ROOT", str(tmp_path / "shared"))
    monkeypatch.setenv("SERVICE_STORAGE_MOUNT_ROOT", str(tmp_path))
    monkeypatch.delenv("VOICE_STORAGE_ROOT", raising=False)
    monkeypatch.delenv("VOICE_STORAGE_MOUNT_ROOT", raising=False)
    settings = Settings.from_env()
    assert settings.voice_root == tmp_path / "shared/tts/voices"
    assert settings.voice_mount_root == tmp_path
