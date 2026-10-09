import json
import os
import subprocess
import zlib
from dataclasses import replace
from uuid import uuid4

import pytest
from conftest import login
from PIL import Image, ImageDraw, ImageFont

from noedaeri.config import Settings
from noedaeri.media import JobCancelled, MediaError
from noedaeri.ocr import extract_pdf
from noedaeri.storage import Storage


def pdf_fixture(scanned=False, rotated=False):
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [4 0 R 6 0 R] /Count 2 >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    def stream(data, prefix=b""):
        return (
            b"<< "
            + prefix
            + b" /Length "
            + str(len(data)).encode()
            + b" >>\nstream\n"
            + data
            + b"\nendstream"
        )

    page = (
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] "
        b"/Resources << /Font << /F1 3 0 R >> >> /Contents 5 0 R >>"
    )
    objects.extend([page, stream(b"BT /F1 30 Tf 50 650 Td (Hello PDF 2026) Tj ET")])
    if scanned:
        image = Image.new("RGB", (1200, 360), "white")
        font = ImageFont.truetype("/System/Library/Fonts/AppleSDGothicNeo.ttc", 55)
        ImageDraw.Draw(image).text((45, 80), "Scanned page OCR 2026", font=font, fill="black")
        objects.extend(
            [
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] "
                + (b"/Rotate 90 " if rotated else b"")
                + b"/Resources << /XObject << /Im1 8 0 R >> >> /Contents 7 0 R >>",
                stream(b"q 600 0 0 180 0 300 cm /Im1 Do Q"),
                stream(
                    zlib.compress(image.tobytes()),
                    b"/Type /XObject /Subtype /Image /Width 1200 /Height 360 "
                    b"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode",
                ),
            ]
        )
    else:
        objects.extend([page.replace(b"5 0 R", b"7 0 R"), stream(b"")])
    data = b"%PDF-1.7\n"
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data += f"{index} 0 obj\n".encode() + obj + b"\nendobj\n"
    offset = len(data)
    data += f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode()
    data += b"".join(f"{value:010} 00000 n \n".encode() for value in offsets[1:])
    data += f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{offset}\n%%EOF".encode()
    return data


def setup(tmp_path, content, **options):
    settings = Settings(database_url="", worker_key="", public_origin="", storage_root=tmp_path)
    storage = Storage(settings)
    key = uuid4()
    source = storage.path("uploads", key, "input")
    source.parent.mkdir()
    source.write_bytes(content)
    job = {
        "id": str(key),
        "kind": "pdf.extract",
        "input": {"type": "upload"},
        "options": {"mode": "auto", "language": "en", "language_correction": True, **options},
    }
    return settings, storage, job


def create_pdf(client):
    return client.post(
        "/api/jobs",
        json={
            "kind": "pdf.extract",
            "title": "PDF 검수",
            "idempotency_key": str(uuid4()),
            "input": {"type": "upload"},
            "options": {"mode": "auto"},
        },
    )


def test_pdf_catalog_access_and_options(app, monkeypatch):
    monkeypatch.setattr("noedaeri.api.runtime_ready", lambda: True)
    monkeypatch.setattr("noedaeri.services.runtime_ready", lambda: True)
    client, _ = login(app)
    assert create_pdf(client).status_code == 503
    app.state.settings = replace(app.state.settings, ocr_enabled=True)
    row = next(row for row in client.get("/api/services").json() if row["kind"] == "pdf.extract")
    assert row["available"] and row["pdf_limits"]["max_pages"] == 100
    job = create_pdf(client).json()
    assert job["service"] == "pdf" and job["options"]["mode"] == "auto"
    assert client.get("/api/tasks?service=pdf").json()[0]["id"] == job["id"]
    login(app, status="pending")
    assert create_pdf(client).status_code == 403


@pytest.mark.skipif(os.environ.get("RUN_OCR_SMOKE") != "1", reason="Native PDFKit opt-in")
def test_pdf_text_scan_modes_progress_cancel_limits(tmp_path):
    settings, storage, job = setup(tmp_path, pdf_fixture(scanned=True))
    stages = []
    result = extract_pdf(settings, storage, job, lambda: True, stages.append, lambda _: None)
    document = storage.path("results", job["id"], "document.json")
    data = json.loads(document.read_text())
    assert result["text_pages"] == 1 and result["ocr_pages"] == 1 and result["page_count"] == 2
    assert "Hello PDF 2026" in data["pages"][0]["text"]
    assert "Scanned page OCR 2026" in data["pages"][1]["text"]
    assert data["pages"][1]["lines"][0]["bounding_box"]["top"] >= 0
    assert "pdf_pages_2_of_2" in stages
    assert not list(storage.path("jobs", job["id"], "pdf-request.json").parent.iterdir())
    job["options"]["mode"] = "text"
    extract_pdf(settings, storage, job, lambda: True, lambda _: None, lambda _: None)
    assert json.loads(document.read_text())["pages"][1]["text"] == ""
    job["options"]["mode"] = "ocr"
    assert (
        extract_pdf(settings, storage, job, lambda: True, lambda _: None, lambda _: None)[
            "ocr_pages"
        ]
        == 2
    )
    with pytest.raises(MediaError, match="pdf_page_limit_exceeded"):
        extract_pdf(
            replace(settings, pdf_max_pages=1),
            storage,
            job,
            lambda: True,
            lambda _: None,
            lambda _: None,
        )
    with pytest.raises(JobCancelled):
        extract_pdf(settings, storage, job, lambda: False, lambda _: None, lambda _: None)
    with pytest.raises(MediaError, match="processing_timeout"):
        extract_pdf(
            replace(settings, pdf_timeout=0),
            storage,
            job,
            lambda: True,
            lambda _: None,
            lambda _: None,
        )
    storage.path("uploads", job["id"], "input").write_bytes(b"not pdf")
    with pytest.raises(MediaError, match="unsupported_media"):
        extract_pdf(settings, storage, job, lambda: True, lambda _: None, lambda _: None)


@pytest.mark.skipif(os.environ.get("RUN_OCR_SMOKE") != "1", reason="Native PDFKit opt-in")
def test_pdf_rotated_and_encrypted(tmp_path):
    settings, storage, job = setup(tmp_path, pdf_fixture(scanned=True, rotated=True))
    result = extract_pdf(settings, storage, job, lambda: True, lambda _: None, lambda _: None)
    assert result["ocr_pages"] == 1
    data = json.loads(storage.path("results", job["id"], "document.json").read_text())
    assert "Scanned page OCR 2026" in data["pages"][1]["text"]
    source = storage.path("uploads", job["id"], "input")
    encryptor = tmp_path / "encrypt.swift"
    encryptor.write_text(
        "import PDFKit\nimport Foundation\n"
        "let url=URL(fileURLWithPath:CommandLine.arguments[1]);\n"
        "let pdf=PDFDocument(url:url)!;\n"
        'assert(pdf.write(to:url,withOptions:[.userPasswordOption:"fixture",'
        '.ownerPasswordOption:"owner"]))\n'
    )
    subprocess.run(
        [
            "xcrun",
            "swift",
            "-module-cache-path",
            str(tmp_path / "cache"),
            str(encryptor),
            str(source),
        ],
        check=True,
        capture_output=True,
    )
    with pytest.raises(MediaError, match="pdf_encrypted"):
        extract_pdf(settings, storage, job, lambda: True, lambda _: None, lambda _: None)
