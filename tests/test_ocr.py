import io
import json
import os
from dataclasses import replace
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from conftest import login
from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

from noedaeri.config import Settings
from noedaeri.images import prepare_ocr
from noedaeri.media import JobCancelled, MediaError
from noedaeri.ocr import recognize
from noedaeri.storage import Storage


def create_ocr(client, key=None, options=None):
    return client.post(
        "/api/jobs",
        json={
            "kind": "ocr.recognize",
            "title": "문자 인식 검수",
            "idempotency_key": str(key or uuid4()),
            "input": {"type": "upload", "extension": "png"},
            "options": options or {},
        },
    )


def fixture_image(blank=False):
    image = Image.new("RGBA", (1200, 360), "white")
    if not blank:
        # A system font, not private deployment configuration or a downloaded asset.
        font = ImageFont.truetype("/System/Library/Fonts/AppleSDGothicNeo.ttc", 48)
        draw = ImageDraw.Draw(image)
        draw.text((45, 60), "뇌대리 문자 인식 테스트", fill="black", font=font)
        draw.text((45, 160), "Hello OCR 2026", fill="black", font=font)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_ocr_catalog_validation_idempotency_and_access(app, monkeypatch):
    monkeypatch.setattr("noedaeri.api.runtime_ready", lambda: True)
    monkeypatch.setattr("noedaeri.services.runtime_ready", lambda: True)
    client, _ = login(app)
    assert create_ocr(client).status_code == 503
    app.state.settings = replace(app.state.settings, ocr_enabled=True)
    catalog = {row["kind"]: row for row in client.get("/api/services").json()}
    assert catalog["ocr.recognize"]["available"] is True
    assert catalog["ocr.recognize"]["ocr_limits"]["max_pixels"] == 40_000_000
    assert (
        catalog["ocr.recognize"]["ocr_limits"]["max_input_bytes"] == app.state.settings.upload_limit
    )
    assert create_ocr(client, options={"language": "invalid"}).status_code == 422
    assert create_ocr(client, options={"seconds": 0}).status_code == 422
    key = uuid4()
    first = create_ocr(client, key).json()
    assert first["options"] == {"language": "auto", "language_correction": True}
    assert create_ocr(client, key).json()["id"] == first["id"]
    assert create_ocr(client, key, {"language": "en"}).status_code == 409
    assert client.get("/api/tasks?service=ocr").json()[0]["id"] == first["id"]
    login(app, status="pending")
    assert create_ocr(client).status_code == 403
    assert client.get(f"/api/jobs/{first['id']}/result").status_code == 403
    monkeypatch.setattr("noedaeri.api.runtime_ready", lambda: False)
    login(app)
    assert create_ocr(client).status_code == 503


@pytest.mark.parametrize("kind", ["ocr.recognize", "pdf.extract"])
def test_ocr_callback_result_receipt(app, monkeypatch, kind):
    from test_integration import platform

    monkeypatch.setattr("noedaeri.api.runtime_ready", lambda: True)
    app.state.settings = replace(app.state.settings, ocr_enabled=True)
    client = platform(app)
    response = client.post(
        "/api/v1/jobs",
        json={
            "kind": kind,
            "title": "외부 문자 인식",
            "idempotency_key": str(uuid4()),
            "input": {
                "type": "upload",
                **({"extension": "png"} if kind == "ocr.recognize" else {}),
            },
            "options": {"language": "ko"},
        },
    )
    assert response.status_code == 201, response.text
    key = UUID(response.json()["id"])
    assert (
        client.put(
            f"/api/v1/jobs/{key}/input",
            content=b"fixture",
            headers={"Content-Type": "application/octet-stream"},
        ).status_code
        == 200
    )
    claimed = app.state.queue.claim(uuid4(), [kind])
    base = "text" if kind == "ocr.recognize" else "document"
    names = [base + suffix for suffix in (".json", ".txt", ".zip")]
    for name in names:
        path = app.state.storage.path("results", key, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")
    worker = {"Authorization": "Bearer " + app.state.settings.worker_key}
    payload = {
        "token": str(claimed["lease_token"]),
        "status": "succeeded",
        "result": {"type": kind.replace(".", "_"), "files": names, "line_count": 1},
    }
    (path.parent / (base + ".txt")).unlink()
    assert (
        client.post(f"/internal/jobs/{key}/finish", headers=worker, json=payload).status_code == 409
    )
    (path.parent / (base + ".txt")).write_bytes(b"fixture")
    assert (
        client.post(f"/internal/jobs/{key}/finish", headers=worker, json=payload).status_code == 200
    )
    app.state.webhooks.collect()
    assert app.state.webhooks.dispatch_one()
    job = client.get(f"/api/v1/jobs/{key}").json()
    assert job["delivery"]["state"] == "delivered"
    assert client.get(f"/api/v1/jobs/{key}/files/ocr-input.png").status_code == 404
    assert (
        client.post(
            f"/api/v1/jobs/{key}/receipt", json={"event_id": job["terminal_event_id"]}
        ).status_code
        == 200
    )
    app.state.storage.cleanup(app.state.db)
    assert client.get(f"/api/v1/jobs/{key}/result").status_code == 410


@pytest.mark.parametrize("kind", ["ocr.recognize", "image.package", "pdf.extract"])
def test_ocr_upload_specific_limit_and_retry(app, monkeypatch, kind):
    from fastapi.testclient import TestClient

    from noedaeri.api import create_app

    monkeypatch.setattr("noedaeri.api.runtime_ready", lambda: True)
    limited = create_app(
        replace(
            app.state.settings,
            ocr_enabled=True,
            image_input_limit=32_000_000,
            pdf_input_limit=32_000_000,
            upload_limit=33_000_000,
            storage_limit=128 * 1024**2,
        )
    )
    with TestClient(limited, base_url="https://testserver") as client:
        limited.state.client = client
        login(limited)
        job = client.post(
            "/api/jobs",
            json={
                "kind": kind,
                "title": "이미지 한도 검수",
                "idempotency_key": str(uuid4()),
                "input": {
                    "type": "upload",
                    **({"extension": "png"} if kind != "pdf.extract" else {}),
                },
                "options": {},
            },
        ).json()
        row = next(row for row in client.get("/api/services").json() if row["kind"] == kind)
        limits = (
            row["pdf_limits"]
            if kind == "pdf.extract"
            else row["ocr_limits"]
            if kind == "ocr.recognize"
            else row["image_limits"]
        )
        assert limits["max_input_bytes"] == 32_000_000
        url = f"/api/jobs/{job['id']}/input"
        response = client.put(
            url, content=b"x" * 32_000_001, headers={"Content-Type": "application/octet-stream"}
        )
        assert response.status_code == 413 and response.json()["detail"] == "upload_too_large"
        assert not limited.state.storage.path("uploads", job["id"], "input").exists()
        assert (
            client.put(
                url, content=b"fixture", headers={"Content-Type": "application/octet-stream"}
            ).status_code
            == 200
        )


def test_image_limit_settings(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "fixture")
    monkeypatch.setenv("WORKER_API_KEY", "x" * 32)
    monkeypatch.setenv("PUBLIC_ORIGIN", "https://fixture.example")
    monkeypatch.setenv("PLATFORM_OIDC_REDIRECT_URI", "https://fixture.example/auth/callback")
    monkeypatch.delenv("IMAGE_MAX_INPUT_BYTES", raising=False)
    assert Settings.from_env().image_input_limit == 200_000_000
    monkeypatch.setenv("IMAGE_MAX_INPUT_BYTES", "400000000")
    assert Settings.from_env().image_input_limit == 400_000_000
    for invalid in ("0", "-1", str(5 * 1024**3 + 1)):
        monkeypatch.setenv("IMAGE_MAX_INPUT_BYTES", invalid)
        with pytest.raises(ValueError, match="Image input limit"):
            Settings.from_env()


def test_ocr_image_normalization_and_rejection(tmp_path):
    image = Image.new("RGBA", (5000, 100), (0, 0, 0, 0))
    image.putpixel((2500, 50), (0, 0, 0, 255))
    source, output = tmp_path / "source", tmp_path / "normalized.png"
    image.save(source, format="PNG")
    info = prepare_ocr(source, output, "png")
    assert info["width"] == 4096 and info["original"]["width"] == 5000
    with Image.open(output) as normalized:
        assert normalized.mode == "RGB" and normalized.getpixel((0, 0)) == (255, 255, 255)
        assert not normalized.info
    rotated = Image.new("RGB", (500, 300), "white")
    exif = Image.Exif()
    exif[274] = 6
    rotated.save(source, format="JPEG", exif=exif)
    assert prepare_ocr(source, output, "jpg")["original"] == {"width": 300, "height": 500}
    with pytest.raises(UnidentifiedImageError):
        prepare_ocr(source, output, "png")
    source.write_bytes(b"not an image")
    with pytest.raises(UnidentifiedImageError):
        prepare_ocr(source, output, "png")


def setup_source(tmp_path, content):
    settings = Settings(database_url="", worker_key="", public_origin="", storage_root=tmp_path)
    storage = Storage(settings)
    key = uuid4()
    source = storage.path("uploads", key, "input")
    source.parent.mkdir()
    source.write_bytes(content)
    return (
        settings,
        storage,
        {
            "id": str(key),
            "input": {"extension": "png"},
            "options": {"language": "auto", "language_correction": True},
        },
    )


@pytest.mark.skipif(os.environ.get("RUN_OCR_SMOKE") != "1", reason="Native Vision OCR opt-in")
def test_native_ocr_blank_cancel_timeout_invalid(tmp_path):
    settings, storage, job = setup_source(tmp_path, fixture_image(blank=True))
    result = recognize(settings, storage, job, lambda: True, lambda _: None, lambda _: None)
    assert result["line_count"] == 0 and result["peak_memory_bytes"] > 0
    data = json.loads(storage.path("results", job["id"], "text.json").read_text())
    assert data["text"] == "" and data["lines"] == []
    assert not list(storage.path("jobs", job["id"], "ocr-input.png").parent.iterdir())
    with pytest.raises(JobCancelled):
        recognize(settings, storage, job, lambda: False, lambda _: None, lambda _: None)
    cancelled = False

    def cancel_during_inference(stage):
        nonlocal cancelled
        if stage == "ocr_recognizing":
            cancelled = True

    with pytest.raises(JobCancelled):
        recognize(
            settings, storage, job, lambda: not cancelled, cancel_during_inference, lambda _: None
        )
    assert not list(storage.path("jobs", job["id"], "ocr-input.png").parent.iterdir())
    with pytest.raises(MediaError, match="processing_timeout"):
        recognize(
            replace(settings, ocr_timeout=0),
            storage,
            job,
            lambda: True,
            lambda _: None,
            lambda _: None,
        )
    storage.path("uploads", job["id"], "input").write_bytes(b"not an image")
    with pytest.raises(MediaError, match="unsupported_media"):
        recognize(settings, storage, job, lambda: True, lambda _: None, lambda _: None)


def test_ocr_missing_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr("noedaeri.ocr.ROOT", Path(tmp_path) / "missing")
    settings, storage, job = setup_source(tmp_path, b"fixture")
    with pytest.raises(MediaError, match="ocr_not_configured"):
        recognize(settings, storage, job, lambda: True, lambda _: None, lambda _: None)


@pytest.mark.skipif(os.environ.get("RUN_OCR_SMOKE") != "1", reason="Native Vision OCR opt-in")
def test_native_ocr_large_bmp(tmp_path):
    settings, storage, job = setup_source(tmp_path, b"placeholder")
    source = storage.path("uploads", job["id"], "input")
    image = Image.new("RGB", (4000, 3000), "white")
    font = ImageFont.truetype("/System/Library/Fonts/AppleSDGothicNeo.ttc", 80)
    ImageDraw.Draw(image).text((100, 100), "Hello OCR 200 MB", font=font, fill="black")
    image.save(source, format="BMP")
    assert 32_000_000 < source.stat().st_size < settings.image_input_limit
    job["input"]["extension"] = "bmp"
    result = recognize(settings, storage, job, lambda: True, lambda _: None, lambda _: None)
    data = json.loads(storage.path("results", job["id"], "text.json").read_text())
    assert "Hello OCR 200 MB" in data["text"]
    assert result["source"]["original"] == {"width": 4000, "height": 3000}
