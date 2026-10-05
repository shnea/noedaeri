import json
import sys
import zipfile

import pytest
from PIL import Image, UnidentifiedImageError
from pillow_heif import register_heif_opener

from noedaeri import images
from noedaeri.media import JobCancelled, run_process


@pytest.mark.parametrize("extension", list(images.FORMATS))
def test_formats_decode_and_export_only_derivatives(tmp_path, extension):
    register_heif_opener(thumbnails=False, decode_threads=1)
    source = tmp_path / ("source." + extension)
    image = Image.new("RGB", (64, 48), "red")
    image.save(source, format=images.FORMATS[extension][0])
    output = tmp_path / "output"
    raw = run_process(
        [sys.executable, images.__file__, str(source), str(output), extension],
        30,
        lambda: True,
        capture=True,
    )
    result = json.loads(raw)
    assert result["type"] == "image_package"
    assert result["source"]["format"] == images.FORMATS[extension][0]
    assert result["preview"]["width"] <= 64
    for name in ("thumbnail.jpg", "preview.webp"):
        with Image.open(output / name) as decoded:
            decoded.load()
            assert not decoded.getexif()
    with zipfile.ZipFile(output / "image.zip") as archive:
        assert set(archive.namelist()) == {"thumbnail.jpg", "preview.webp", "metadata.json"}
        assert not any(name.startswith("source") for name in archive.namelist())


def test_orientation_alpha_and_first_frame(tmp_path):
    source = tmp_path / "source.png"
    exif = Image.Exif()
    exif[274] = 6
    exif[315] = "private-author"
    Image.new("RGBA", (100, 50), (0, 0, 0, 0)).save(source, exif=exif)
    result = images.convert(source, tmp_path / "out", "png")
    assert (result["source"]["width"], result["source"]["height"]) == (50, 100)
    with Image.open(tmp_path / "out/preview.webp") as preview:
        assert preview.getpixel((0, 0))[3] == 0
        assert not preview.getexif()
    with Image.open(tmp_path / "out/thumbnail.jpg") as thumb:
        assert min(thumb.getpixel((0, 0))) > 250
    source = tmp_path / "animated.gif"
    Image.new("RGB", (32, 32), "red").save(
        source,
        save_all=True,
        append_images=[Image.new("RGB", (32, 32), "blue")],
        duration=100,
        loop=0,
    )
    images.convert(source, tmp_path / "animated", "gif")
    with Image.open(tmp_path / "animated/preview.webp") as preview:
        pixel = preview.convert("RGB").getpixel((16, 16))
        assert pixel[0] > 200 and pixel[2] < 50
        assert not getattr(preview, "is_animated", False)


def test_mismatch_corruption_limit_and_cancellation(tmp_path):
    source = tmp_path / "source.png"
    Image.new("RGB", (10, 10)).save(source)
    with pytest.raises(UnidentifiedImageError):
        images.convert(source, tmp_path / "mismatch", "jpg")
    source.write_bytes(b"corrupt")
    with pytest.raises(UnidentifiedImageError):
        images.convert(source, tmp_path / "corrupt", "png")
    Image.new("RGB", (10001, 1)).save(source)
    with pytest.raises(ValueError, match="unsupported_media"):
        images.convert(source, tmp_path / "large", "png")
    with pytest.raises(JobCancelled):
        run_process(
            [sys.executable, images.__file__, str(source), str(tmp_path / "cancel"), "png"],
            30,
            lambda: False,
            capture=True,
        )


def test_palette_alpha_with_profile_and_byte_limit(tmp_path):
    from PIL import ImageCms

    source = tmp_path / "palette.png"
    frame = Image.new("P", (20, 20), 0)
    frame.putpalette([0, 0, 0] * 256)
    frame.save(
        source,
        transparency=0,
        icc_profile=ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes(),
    )
    images.convert(source, tmp_path / "out", "png")
    with Image.open(tmp_path / "out/preview.webp") as preview:
        assert preview.getpixel((0, 0))[3] == 0
        assert not preview.info.get("icc_profile")
    with source.open("wb") as target:
        target.truncate(32_000_001)
    with pytest.raises(ValueError, match="unsupported_media"):
        images.convert(source, tmp_path / "oversize", "png")
