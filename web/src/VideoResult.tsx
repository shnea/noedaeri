import Hls from "hls.js";
import { useEffect, useRef, useState } from "react";

export function VideoResult({
  jobId,
  variants,
  subtitles,
}: {
  jobId: string;
  variants: { label: string; playlist: string }[];
  subtitles?: {
    mode: "sidecar" | "burned";
    language: string;
    cue_count: number;
  };
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
      >
        {subtitles?.mode === "sidecar" && (
          <track
            kind="subtitles"
            src={base + "subtitles.vtt"}
            srcLang={
              subtitles.language === "auto" ? undefined : subtitles.language
            }
            label="자동 생성 자막"
            default
          />
        )}
      </video>
      {subtitles && (
        <>
          <p>
            {subtitles.mode === "burned"
              ? "영상에 자막을 입혔습니다. 모든 화질에 적용되며 재생 중 끌 수 없습니다."
              : "자동 생성 자막입니다. 재생기의 자막 메뉴에서 켜고 끌 수 있습니다."}
            {` ${subtitles.cue_count}개 자막 · 시각은 근사값입니다.`}
            {subtitles.cue_count === 0 &&
              " 인식된 음성이 없어 표시할 자막이 없습니다."}
          </p>
          <div className="guide-actions">
            <a className="button" href={base + "subtitles.srt"} download>
              SRT 자막
            </a>
            <a className="button" href={base + "subtitles.vtt"} download>
              VTT 자막
            </a>
          </div>
        </>
      )}
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
