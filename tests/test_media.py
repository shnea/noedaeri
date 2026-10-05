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
