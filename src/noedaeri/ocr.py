"""Image OCR in the existing shared native execution slot."""

import hashlib
import json
import os
import sys
import time
import zipfile
from pathlib import Path
from uuid import UUID

from .media import JobCancelled, MediaError, run_process

ROOT = Path(__file__).resolve().parents[2]
RESULT_LIMIT = 4 * 1024**2


def runtime_ready():
    binary = ROOT / "ocr_runtime/bin/vision-ocr"
    try:
        manifest = json.loads(binary.with_name("manifest.json").read_text())
        return (
            os.access(binary, os.X_OK)
            and manifest["revision"] == 3
            and manifest["source_sha256"]
            == hashlib.sha256((ROOT / "scripts/ocr_inference.swift").read_bytes()).hexdigest()
            and manifest["binary_sha256"] == hashlib.sha256(binary.read_bytes()).hexdigest()
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False


def extract_pdf(settings, storage, job, alive, stage, reserve):
    """Extract page text or recognize a rendered page, within the common native slot."""
    if not runtime_ready():
        raise MediaError("ocr_not_configured")
    deadline = time.monotonic() + settings.pdf_timeout

    def remaining():
        seconds = deadline - time.monotonic()
        if seconds <= 0:
            raise MediaError("processing_timeout")
        return seconds

    job_id = UUID(job["id"])
    source = storage.path("uploads", job_id, "input")
    if source.stat().st_size > min(settings.upload_limit, settings.pdf_input_limit):
        raise MediaError("unsupported_media")
    with source.open("rb") as file:
        if file.read(5) != b"%PDF-":
            raise MediaError("unsupported_media")
    request_file = storage.path("jobs", job_id, "pdf-request.json")
    progress = request_file.with_name("pdf-progress.json")
    output = storage.path("results", job_id, "document.json")
    request_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    reserve(16 * 1024**2)
    last = None

    def check_progress():
        nonlocal last
        if not alive():
            return False
        try:
            data = json.loads(progress.read_bytes())
            completed, total = data["completed"], data["total"]
            if not (0 <= completed <= total <= settings.pdf_max_pages):
                raise ValueError()
            value = f"pdf_pages_{completed}_of_{total}"
            if value != last:
                last = value
                stage(value)
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return True

    payload = {
        "mode": "pdf",
        "source": str(source),
        "output": str(output),
        "progress": str(progress),
        "max_pages": settings.pdf_max_pages,
        "extraction": job["options"]["mode"],
        "language": job["options"]["language"],
        "language_correction": job["options"]["language_correction"],
    }
    fd = os.open(request_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as file:
            json.dump(payload, file)
        stage("pdf_reading")
        raw = run_process(
            [str(ROOT / "ocr_runtime/bin/vision-ocr"), str(request_file)],
            remaining(),
            check_progress,
            capture=True,
            failure_code="ocr_recognition_failed",
        )
        receipt = json.loads(raw)
        if "error" in receipt:
            allowed = {"pdf_encrypted", "pdf_page_limit_exceeded", "ocr_result_too_large"}
            raise MediaError(
                receipt["error"] if receipt["error"] in allowed else "ocr_recognition_failed"
            )
        check_progress()
    finally:
        request_file.unlink(missing_ok=True)
        progress.unlink(missing_ok=True)
    if not output.is_file() or output.stat().st_size > RESULT_LIMIT:
        raise MediaError("ocr_result_too_large")
    result = json.loads(output.read_text())
    text_file = output.with_suffix(".txt")
    text_file.write_text(result["text"] + "\n", encoding="utf-8")
    stage("packaging")
    with zipfile.ZipFile(output.with_suffix(".zip"), "w", zipfile.ZIP_DEFLATED) as archive:
        for path in (output, text_file):
            if not alive():
                raise JobCancelled()
            remaining()
            archive.write(path, path.name)
    return {
        "type": "pdf_extract",
        "engine": "PDFKit + Apple Vision",
        "revision": 3,
        "mode": payload["extraction"],
        "page_count": result["page_count"],
        "text_pages": sum(page["method"] == "text" for page in result["pages"]),
        "ocr_pages": sum(page["method"] == "ocr" for page in result["pages"]),
        "files": ["document.json", "document.txt", "document.zip"],
        "elapsed_seconds": round(settings.pdf_timeout - remaining(), 3),
        "peak_memory_bytes": receipt["peak_memory_bytes"],
    }


def recognize(settings, storage, job, alive, stage, reserve):
    if not runtime_ready():
        raise MediaError("ocr_not_configured")
    deadline = time.monotonic() + settings.ocr_timeout

    def remaining():
        seconds = deadline - time.monotonic()
        if seconds <= 0:
            raise MediaError("processing_timeout")
        return seconds

    job_id = UUID(job["id"])
    source = storage.path("uploads", job_id, "input")
    image = storage.path("jobs", job_id, "ocr-input.png")
    output = storage.path("results", job_id, "text.json")
    image.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # A 4096² RGB PNG plus bounded JSON/TXT/ZIP; no uncounted persistent model cache.
    reserve(80 * 1024**2)
    request_file = image.with_name("ocr-request.json")
    try:
        stage("ocr_normalizing")
        raw = run_process(
            [
                sys.executable,
                str(Path(__file__).with_name("images.py")),
                "--ocr",
                str(source),
                str(image),
                job["input"]["extension"],
                str(min(settings.upload_limit, settings.image_input_limit)),
            ],
            min(60, remaining()),
            alive,
            capture=True,
        )
        source_info = json.loads(raw)
        if "error" in source_info:
            raise MediaError("unsupported_media")
        payload = {
            "source": str(image),
            "output": str(output),
            "source_info": source_info,
            "language": job["options"]["language"],
            "language_correction": job["options"]["language_correction"],
        }
        fd = os.open(request_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as file:
            json.dump(payload, file)
        stage("ocr_recognizing")
        raw = run_process(
            [str(ROOT / "ocr_runtime/bin/vision-ocr"), str(request_file)],
            remaining(),
            alive,
            capture=True,
            failure_code="ocr_recognition_failed",
        )
        receipt = json.loads(raw)
        if "error" in receipt:
            raise MediaError(
                "ocr_result_too_large"
                if receipt["error"] == "ocr_result_too_large"
                else "ocr_recognition_failed"
            )
    finally:
        request_file.unlink(missing_ok=True)
        image.unlink(missing_ok=True)
    if not output.is_file() or output.stat().st_size > RESULT_LIMIT:
        raise MediaError("ocr_result_too_large")
    result = json.loads(output.read_text())
    stage("packaging")
    text_file = output.with_suffix(".txt")
    text_file.write_text(result["text"] + "\n", encoding="utf-8")
    with zipfile.ZipFile(output.with_suffix(".zip"), "w", zipfile.ZIP_DEFLATED) as archive:
        for path in (output, text_file):
            if not alive():
                raise JobCancelled()
            remaining()
            archive.write(path, path.name)
    return {
        "type": "ocr_recognize",
        "engine": "Apple Vision",
        "revision": 3,
        "language": payload["language"],
        "language_correction": payload["language_correction"],
        "source": source_info,
        "line_count": len(result["lines"]),
        "files": ["text.json", "text.txt", "text.zip"],
        "elapsed_seconds": round(settings.ocr_timeout - remaining(), 3),
        "peak_memory_bytes": receipt["peak_memory_bytes"],
    }
