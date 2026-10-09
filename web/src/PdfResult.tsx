import { useEffect, useState } from "react";
import { z } from "zod";
import { request } from "./api";

const documentSchema = z
  .object({
    page_count: z.number().int().min(1).max(500),
    pages: z
      .array(
        z.object({
          page: z.number().int().positive(),
          method: z.enum(["text", "ocr"]),
          text: z.string(),
        }),
      )
      .min(1)
      .max(500),
  })
  .refine((value) => value.page_count === value.pages.length);

export function PdfResult({ jobId }: { jobId: string }) {
  const [data, setData] = useState<z.infer<typeof documentSchema> | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [page, setPage] = useState(0);
  const base = `/api/jobs/${jobId}/files/`;

  useEffect(() => {
    const controller = new AbortController();
    setData(null);
    setError("");
    setPage(0);
    void request(base + "document.json", { signal: controller.signal })
      .then((response) => response.json())
      .then((value) => {
        if (controller.signal.aborted) return;

        const parsed = documentSchema.safeParse(value);

        if (!parsed.success)
          throw new Error(
            "PDF 결과 형식을 확인하지 못했습니다. 관리자에게 확인해 주세요.",
          );
        setData(parsed.data);
      })
      .catch((failure) => {
        if (!controller.signal.aborted)
          setError(
            failure instanceof Error
              ? failure.message
              : "PDF 결과를 불러오지 못했습니다.",
          );
      });

    return () => controller.abort();
  }, [base, attempt]);

  const current = data?.pages[page];

  return (
    <div className="ocr-result">
      <p>
        페이지별 텍스트입니다. 표·서식·다단 읽기 순서는 복원하지 않습니다. OCR
        결과는 원문과 대조해 주세요.
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
      ) : current ? (
        <>
          <p>
            {current.page} / {data?.page_count}페이지 ·{" "}
            {current.method === "text" ? "내장 텍스트 추출" : "스캔 OCR"}
          </p>
          {current.text ? (
            <div
              role="region"
              className="json-result pdf-page-text"
              tabIndex={0}
              aria-label="PDF 페이지 텍스트"
            >
              {current.text}
            </div>
          ) : (
            <p>이 페이지에서 추출한 텍스트가 없습니다.</p>
          )}
          <div className="guide-actions" aria-label="PDF 결과 페이지">
            <button
              type="button"
              disabled={page === 0}
              onClick={() => setPage((value) => value - 1)}
            >
              이전 페이지
            </button>
            <button
              type="button"
              disabled={!data || page + 1 >= data.page_count}
              onClick={() => setPage((value) => value + 1)}
            >
              다음 페이지
            </button>
          </div>
        </>
      ) : (
        <p role="status">PDF 결과를 불러오는 중입니다.</p>
      )}
      <div className="guide-actions">
        <a
          className="button"
          href={base + "document.txt"}
          download="document.txt"
        >
          전체 텍스트 다운로드
        </a>
        <a
          className="button"
          href={base + "document.json"}
          download="document.json"
        >
          페이지 JSON 다운로드
        </a>
      </div>
    </div>
  );
}
