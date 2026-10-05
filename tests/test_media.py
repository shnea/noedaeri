import time

import pytest

from noedaeri.media import JobCancelled, MediaError, run_process, thumbnail


def test_invalid_input(tmp_path):
    source = tmp_path / "input"
    source.write_text("not media")
    with pytest.raises(MediaError):
        thumbnail(source, tmp_path / "out.jpg", 0, 3, lambda: True)


def test_process_cancellation_reaps_child():
    started = time.monotonic()
    with pytest.raises(JobCancelled):
        run_process(["sleep", "20"], 30, lambda: False)
    assert time.monotonic() - started < 4


def test_process_timeout():
    with pytest.raises(MediaError, match="processing_timeout"):
        run_process(["sleep", "20"], 0.1, lambda: True)


def test_playlist_cannot_open_remote_input(tmp_path):
    source = tmp_path / "input"
    source.write_text(
        "#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6,\nhttps://example.invalid/segment.ts\n"
    )
    with pytest.raises(MediaError):
        thumbnail(source, tmp_path / "out.jpg", 0, 3, lambda: True)


def test_package_preserves_display_aspect_and_cancellation(tmp_path):
    import subprocess

    import pytest

    from noedaeri.media import JobCancelled, video_package

    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=s=720x576:d=1",
            "-vf",
            "setsar=16/15",
            "-c:v",
            "libx264",
            "-y",
            str(source),
        ],
        check=True,
    )
    result = video_package(source, tmp_path / "result", 0, 30, lambda: True, lambda _: None)
    variant = result["variants"][0]
    assert (variant["width"], variant["height"]) == (640, 480)
    with pytest.raises(JobCancelled):
        video_package(source, tmp_path / "cancelled", 0, 30, lambda: False, lambda _: None)


@pytest.mark.parametrize("probe", ['{"streams":[]}', '{"format":{"duration":"N/A"}}'])
def test_package_rejects_incomplete_probe(tmp_path, monkeypatch, probe):
    from noedaeri import media

    monkeypatch.setattr(media, "run_process", lambda *args, **kwargs: probe)
    with pytest.raises(MediaError, match="unsupported_media"):
        media.video_package(
            tmp_path / "input", tmp_path / "out", 0, 30, lambda: True, lambda _: None
        )


def test_package_reserves_before_writing_outputs(tmp_path, monkeypatch):
    from noedaeri import media

    monkeypatch.setattr(
        media,
        "run_process",
        lambda *args, **kwargs: (
            '{"format":{"duration":"60"},"streams":[{"width":1280,"height":720}]}'
        ),
    )

    def reject(amount):
        assert amount > 60_000_000
        assert list((tmp_path / "out").iterdir()) == []
        raise MediaError("storage_capacity_exceeded")

    with pytest.raises(MediaError, match="storage_capacity_exceeded"):
        media.video_package(
            tmp_path / "input", tmp_path / "out", 0, 30, lambda: True, lambda _: None, reject
        )
