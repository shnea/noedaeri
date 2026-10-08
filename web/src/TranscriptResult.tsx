import { useEffect, useState } from "react";
import { z } from "zod";
import { request } from "./api";

const transcriptSchema = z.object({
  text: z.string(),
  duration_seconds: z.number().nonnegative(),
  segments: z.array(
    z.object({
      start: z.number().nonnegative(),
      end: z.number().nonnegative(),
      text: z.string(),
    }),
  ),
});

function timestamp(seconds: number) {
  const tenths = Math.round(seconds * 10);
  const minutes = Math.floor(tenths / 600);

  return `${minutes}:${((tenths % 600) / 10).toFixed(1).padStart(4, "0")}`;
}

export function TranscriptResult({ jobId }: { jobId: string }) {
  const [data, setData] = useState<z.infer<typeof transcriptSchema> | null>(
    null,
  );

  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [page, setPage] = useState(0);
  const base = `/api/jobs/${jobId}/files/`;

  useEffect(() => {
    const controller = new AbortController();
    setData(null);
    setError("");
    setPage(0);
    void request(base + "transcript.json", { signal: controller.signal })
      .then((response) => response.json())
      .then((value) => {
        if (!controller.signal.aborted) {
          const parsed = transcriptSchema.safeParse(value);

          if (!parsed.success)
            throw new Error(
              "인식 결과 형식을 확인하지 못했습니다. 관리자에게 확인해 주세요.",
            );

          setData(parsed.data);
        }
      })
      .catch((failure) => {
        if (!controller.signal.aborted)
          setError(
            failure instanceof Error
              ? failure.message
              : "인식 결과를 불러오지 못했습니다.",
          );
      });

    return () => controller.abort();
  }, [base, attempt]);

  return (
    <div className="transcript-result">
      <p>
        음성 구간 기준 시각입니다. 단어별 정렬과 화자 구분은 제공하지 않습니다.
      </p>
      {error ? (
        <div role="alert">
          <p>{error}</p>
          <button
            type="button"
            onClick={() => setAttempt((value) => value + 1)}
          >
            결과 다시 불러오기
          </button>
        </div>
      ) : data ? (
        <>
          <p>
            {data.duration_seconds.toFixed(1)}초 · {data.segments.length}개 음성
            구간
          </p>
          {data.segments.length === 0 ? (
            <p>인식된 음성이 없습니다. 무음 또는 입력 상태를 확인해 주세요.</p>
          ) : (
            <div className="transcript-segments" aria-label="음성 인식 결과">
              {data.segments
                .slice(page * 50, (page + 1) * 50)
                .map((segment, index) => (
                  <p key={index}>
                    <span className="segment-time">
                      {timestamp(segment.start)}–{timestamp(segment.end)}
                    </span>
                    <span>{segment.text}</span>
                  </p>
                ))}
            </div>
          )}
          {data.segments.length > 50 && (
            <div className="guide-actions" aria-label="인식 구간 페이지">
              <button
                type="button"
                disabled={page === 0}
                onClick={() => setPage((value) => value - 1)}
              >
                이전 구간
              </button>
              <span aria-live="polite">
                {page + 1} / {Math.ceil(data.segments.length / 50)}
              </span>
              <button
                type="button"
                disabled={(page + 1) * 50 >= data.segments.length}
                onClick={() => setPage((value) => value + 1)}
              >
                다음 구간
              </button>
            </div>
          )}
        </>
      ) : (
        <p role="status">인식 결과를 불러오는 중입니다.</p>
      )}
      <div className="guide-actions">
        <a
          className="button"
          href={base + "transcript.txt"}
          download="transcript.txt"
        >
          텍스트 다운로드
        </a>
        <a
          className="button"
          href={base + "transcript.json"}
          download="transcript.json"
        >
          구간 JSON 다운로드
        </a>
      </div>
    </div>
  );
}
