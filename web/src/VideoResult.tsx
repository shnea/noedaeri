import Hls from "hls.js";
import { useEffect, useRef, useState } from "react";

export function VideoResult({
  jobId,
  variants,
}: {
  jobId: string;
  variants: { label: string; playlist: string }[];
}) {
  const video = useRef<HTMLVideoElement>(null);
  const controller = useRef<Hls | null>(null);
  const [error, setError] = useState("");
  const [quality, setQuality] = useState(-1);
  const base = `/api/jobs/${jobId}/files/`;

  useEffect(() => {
    const element = video.current;

    if (!element) return;

    if (Hls.isSupported()) {
      const hls = new Hls();
      controller.current = hls;
      hls.loadSource(base + "master.m3u8");
      hls.attachMedia(element);
      hls.on(Hls.Events.ERROR, (_, data) => {
        if (data.fatal)
          setError(
            "영상을 재생하지 못했습니다. 보관 기간과 연결 상태를 확인해 주세요.",
          );
      });

      return () => {
        hls.destroy();
        controller.current = null;
      };
    }

    if (element.canPlayType("application/vnd.apple.mpegurl")) {
      element.src = base + "master.m3u8";
    } else {
      setError(
        "이 브라우저에서는 스트리밍 재생을 지원하지 않습니다. 전체 결과를 다운로드해 주세요.",
      );
    }
  }, [base]);

  function choose(value: number) {
    setQuality(value);

    if (controller.current) {
      controller.current.currentLevel = value;

      return;
    }

    const element = video.current;

    if (!element) return;

    const position = element.currentTime;
    const paused = element.paused;
    element.addEventListener(
      "loadedmetadata",
      () => {
        element.currentTime = position;

        if (!paused)
          void element
            .play()
            .catch(() => setError("재생 버튼을 눌러 이어서 시청해 주세요."));
      },
      { once: true },
    );
    element.src = base + (value < 0 ? "master.m3u8" : variants[value].playlist);
  }

  return (
    <div>
      <video
        ref={video}
        controls
        playsInline
        preload="metadata"
        poster={base + "thumbnail.jpg"}
        aria-label="변환된 영상 미리보기"
        onError={() =>
          setError(
            "영상을 재생하지 못했습니다. 보관 기간과 연결 상태를 확인해 주세요.",
          )
        }
        style={{ width: "100%", maxHeight: "320px" }}
      />
      <label>
        재생 화질{" "}
        <select
          value={quality}
          onChange={(event) => choose(Number(event.target.value))}
        >
          <option value={-1}>자동</option>
          {variants.map((variant, index) => (
            <option key={variant.label} value={index}>
              {variant.label}
            </option>
          ))}
        </select>
      </label>
      {error && <p role="status">{error}</p>}
    </div>
  );
}
