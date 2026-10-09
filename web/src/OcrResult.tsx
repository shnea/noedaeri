import { useEffect, useState } from "react";
import { z } from "zod";
import { request } from "./api";

const ocrSchema = z.object({
  text: z.string(),
  lines: z.array(
    z.object({ text: z.string(), confidence: z.number().min(0).max(1) }),
  ),
});

export function OcrResult({ jobId }: { jobId: string }) {
  const [data, setData] = useState<z.infer<typeof ocrSchema> | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [page, setPage] = useState(0);
  const base = `/api/jobs/${jobId}/files/`;
  useEffect(() => {
    const controller = new AbortController();
    setData(null);
    setError("");
    setPage(0);
    void request(base + "text.json", { signal: controller.signal })
      .then((response) => response.json())
      .then((value) => {
        if (controller.signal.aborted) return;

        const parsed = ocrSchema.safeParse(value);

        if (!parsed.success)
          throw new Error(
            "문자 인식 결과 형식을 확인하지 못했습니다. 관리자에게 확인해 주세요.",
          );
        setData(parsed.data);
      })
      .catch((failure) => {
        if (!controller.signal.aborted)
          setError(
            failure instanceof Error
              ? failure.message
              : "문자 인식 결과를 불러오지 못했습니다.",
          );
      });

    return () => controller.abort();
  }, [base, attempt]);

  return (
    <div className="ocr-result">
      <p>
        인식한 줄과 신뢰도입니다. 줄별 위치는 JSON에 포함되며 표의 행·열 구조는
        복원하지 않습니다.
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
            {data.lines.length}개 줄 인식 · 신뢰도는 정확성을 보장하지 않습니다.
          </p>
          {data.lines.length === 0 ? (
            <p>
              인식된 문자가 없습니다. 해상도·대비와 입력 내용을 확인해 주세요.
            </p>
          ) : (
            <ol
              className="ocr-lines"
              aria-label="문자 인식 결과"
              start={page * 50 + 1}
            >
              {data.lines
                .slice(page * 50, (page + 1) * 50)
                .map((line, index) => (
                  <li key={index}>
                    <span>{line.text}</span>
                    <small>신뢰도 {Math.round(line.confidence * 100)}%</small>
                  </li>
                ))}
            </ol>
          )}
          {data.lines.length > 50 && (
            <div className="guide-actions" aria-label="인식 줄 페이지">
              <button
                type="button"
                disabled={page === 0}
                onClick={() => setPage((value) => value - 1)}
              >
                이전 줄
              </button>
              <span aria-live="polite">
                {page + 1} / {Math.ceil(data.lines.length / 50)}
              </span>
              <button
                type="button"
                disabled={(page + 1) * 50 >= data.lines.length}
                onClick={() => setPage((value) => value + 1)}
              >
                다음 줄
              </button>
            </div>
          )}
        </>
      ) : (
        <p role="status">문자 인식 결과를 불러오는 중입니다.</p>
      )}
      <div className="guide-actions">
        <a className="button" href={base + "text.txt"} download="text.txt">
          텍스트 다운로드
        </a>
        <a className="button" href={base + "text.json"} download="text.json">
          줄별 JSON 다운로드
        </a>
      </div>
    </div>
  );
}
