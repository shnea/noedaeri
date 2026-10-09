"""Isolated, bounded still-image decoder. No original bytes or metadata are exported."""

import io
import json
import resource
import sys
import warnings
import zipfile
from pathlib import Path

from PIL import Image, ImageCms, ImageOps
from pillow_heif import register_heif_opener

FORMATS = {
    "png": ("PNG", "image/png"),
    "jpg": ("JPEG", "image/jpeg"),
    "jpeg": ("JPEG", "image/jpeg"),
    "jfif": ("JPEG", "image/jpeg"),
    "gif": ("GIF", "image/gif"),
    "webp": ("WEBP", "image/webp"),
    "bmp": ("BMP", "image/bmp"),
    "ico": ("ICO", "image/vnd.microsoft.icon"),
    "tif": ("TIFF", "image/tiff"),
    "tiff": ("TIFF", "image/tiff"),
    "heic": ("HEIF", "image/heic"),
    "heif": ("HEIF", "image/heif"),
    "avif": ("AVIF", "image/avif"),
}


def load_frame(source: Path, extension: str, max_input_bytes: int = 200_000_000):
    register_heif_opener(thumbnails=False, decode_threads=1)
    Image.MAX_IMAGE_PIXELS = 40_000_000
    warnings.simplefilter("error", Image.DecompressionBombWarning)
    if source.stat().st_size > max_input_bytes or extension not in FORMATS:
        raise ValueError("unsupported_media")
    expected, mime = FORMATS[extension]
    with Image.open(source, formats=[expected]) as opened:
        if opened.format != expected:
            raise ValueError("unsupported_media")
        width, height = opened.size
        if not (0 < width <= 10000 and 0 < height <= 10000 and width * height <= 40_000_000):
            raise ValueError("unsupported_media")
        opened.seek(0)
        opened.load()
        frame = ImageOps.exif_transpose(opened)
        profile = opened.info.get("icc_profile")
        if profile:
            alpha = frame.convert("RGBA").getchannel("A")
            color = (
                frame if frame.mode in {"RGB", "RGBA", "CMYK", "LAB", "L"} else frame.convert("RGB")
            )
            frame = ImageCms.profileToProfile(
                color,
                ImageCms.ImageCmsProfile(io.BytesIO(profile)),
                ImageCms.createProfile("sRGB"),
                outputMode="RGB",
            )
            frame.putalpha(alpha)
        frame = frame.convert("RGBA")
        frame.info.clear()
    return frame, expected, mime


def prepare_ocr(source: Path, output: Path, extension: str, max_input_bytes: int = 200_000_000):
    frame, expected, mime = load_frame(source, extension, max_input_bytes)
    original = {"width": frame.width, "height": frame.height}
    frame.thumbnail((4096, 4096), Image.Resampling.LANCZOS)
    opaque = Image.new("RGB", frame.size, "white")
    opaque.paste(frame, mask=frame.getchannel("A"))
    opaque.save(output, "PNG")
    return {
        "format": expected,
        "media_type": mime,
        "frame_policy": "first",
        "original": original,
        "width": frame.width,
        "height": frame.height,
    }


def convert(source: Path, folder: Path, extension: str, max_input_bytes: int = 200_000_000):
    frame, expected, mime = load_frame(source, extension, max_input_bytes)
    folder.mkdir(parents=True, exist_ok=False, mode=0o700)
    preview = frame.copy()
    preview.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
    preview.info.clear()
    preview.save(folder / "preview.webp", "WEBP", quality=80, method=4, exif=b"", icc_profile=b"")
    small = frame.copy()
    small.thumbnail((480, 320), Image.Resampling.LANCZOS)
    opaque = Image.new("RGB", small.size, "white")
    opaque.paste(small, mask=small.getchannel("A"))
    opaque.save(folder / "thumbnail.jpg", "JPEG", quality=85, exif=b"")
    result = {
        "type": "image_package",
        "source": {
            "format": expected,
            "media_type": mime,
            "width": frame.width,
            "height": frame.height,
            "frame_policy": "first",
        },
        "download": "image.zip",
        "files": ["thumbnail.jpg", "preview.webp", "metadata.json", "image.zip"],
    }
    for key, name, image, media_type in [
        ("thumbnail", "thumbnail.jpg", small, "image/jpeg"),
        ("preview", "preview.webp", preview, "image/webp"),
    ]:
        size = (folder / name).stat().st_size
        if not 0 < size <= 2_000_000:
            raise ValueError("result_too_large")
        result[key] = {
            "name": name,
            "width": image.width,
            "height": image.height,
            "media_type": media_type,
            "bytes": size,
        }
    (folder / "metadata.json").write_text(json.dumps(result), encoding="utf-8")
    with zipfile.ZipFile(folder / "image.zip", "w", compression=zipfile.ZIP_STORED) as archive:
        for name in ("thumbnail.jpg", "preview.webp", "metadata.json"):
            archive.write(folder / name, name)
    return result


if __name__ == "__main__":
    resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
    ocr = sys.argv[1] == "--ocr"
    limit = 64 * 1024**2 if ocr else 8_000_000
    resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))
    try:
        result = (
            prepare_ocr(
                Path(sys.argv[2]),
                Path(sys.argv[3]),
                sys.argv[4],
                int(sys.argv[5]) if len(sys.argv) > 5 else 200_000_000,
            )
            if ocr
            else convert(
                Path(sys.argv[1]),
                Path(sys.argv[2]),
                sys.argv[3],
                int(sys.argv[4]) if len(sys.argv) > 4 else 200_000_000,
            )
        )
    except Exception:
        # Codec diagnostics and original metadata must not escape into process logs.
        result = {"error": "unsupported_media"}
    print(json.dumps(result))
