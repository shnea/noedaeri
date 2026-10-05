import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { z } from "zod";
import { errorLabel, mutation, request } from "./api";

const statusSchema = z.object({
  state: z.string(),
  error_code: z.string().nullable(),
  waiting: z.number(),
  minimum_keep_seconds: z.number(),
  idle_seconds: z.number(),
  wait_seconds: z.number(),
  timeout_seconds: z.number(),
  memory_reserve_bytes: z.number(),
});

const tiers = ["L1", "L2", "L3", "L4"] as const;

const labels = new Map<string, string>([
  ["L1", "L1 · OpenRouter Free"],
  ["L2", "L2 · Groq Free"],
  ["L3", "L3 · Gemini Free"],
  ["L4", "L4 · Copilot Pro"],
]);

const resultSchema = z.object({
  model_tier: z.enum(tiers),
  probabilities: z.record(z.string(), z.number()),
  input_tokens: z.number(),
  input_truncated: z.boolean(),
  inference_ms: z.number(),
  elapsed_ms: z.number(),
  cold_start: z.boolean(),
});

const stateLabels = new Map([
  ["disabled", "활성화 전"],
  ["off", "메모리 해제됨"],
  ["loading", "모델 로딩 중"],
  ["ready", "추론 준비됨"],
  ["processing", "난이도 판단 중"],
  ["stopping", "모델 종료 중"],
  ["error", "실행 오류"],
]);

export function RayaPanel({ csrf }: { csrf: string }) {
  const [status, setStatus] = useState<z.infer<typeof statusSchema> | null>(
    null,
  );

  const [result, setResult] = useState<z.infer<typeof resultSchema> | null>(
    null,
  );

  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [hasImages, setHasImages] = useState(false);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => {
    const abort = new AbortController();
    let fetching = false;

    async function refresh() {
      if (fetching) return;
      fetching = true;

      try {
        const response = await request("/api/admin/raya/status", {
          signal: abort.signal,
        });

        const next = statusSchema.parse(await response.json());

        if (!abort.signal.aborted) setStatus(next);
      } catch (cause) {
        if (!abort.signal.aborted)
          setError(
            cause instanceof Error
              ? cause.message
              : "모델 상태를 확인하지 못했습니다.",
          );
      } finally {
        fetching = false;
      }
    }

    void refresh();

    const interval = window.setInterval(() => {
      void refresh();
    }, 5000);

    return () => {
      abort.abort();
      controller.current?.abort();
      window.clearInterval(interval);
    };
  }, []);

  async function perform(
    path: string,
    body: string | undefined,
    method = "POST",
  ) {
    if (busy) return;
    const abort = new AbortController();
    controller.current = abort;
    setBusy(true);
    setError("");
    setNotice("");

    if (path === "/api/raya/route") setResult(null);

    try {
      const response = await request(path, {
        ...mutation(csrf, body),
        method,
        signal: abort.signal,
      });

      const data: unknown = await response.json();

      if (!abort.signal.aborted) {
        if (path === "/api/raya/route") setResult(resultSchema.parse(data));
        else {
          setStatus(statusSchema.parse(data));
          setNotice(
            path.endsWith("/policy")
              ? "실행 정책을 저장했습니다."
              : "모델 메모리를 해제했습니다.",
          );
        }
      }
    } catch (cause) {
      if (!abort.signal.aborted)
        setError(
          cause instanceof Error ? cause.message : "Raya 요청에 실패했습니다.",
        );
    } finally {
      if (!abort.signal.aborted) setBusy(false);
    }
  }

  function savePolicy(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    void perform(
      "/api/admin/raya/policy",
      JSON.stringify({
        minimum_keep_seconds: Number(data.get("minimum")),
        idle_seconds: Number(data.get("idle")),
      }),
      "PATCH",
    );
  }

  return (
    <div className="service-list raya-panel">
      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      <section aria-labelledby="raya-state">
        <h2 id="raya-state">CPU 라우터 상태</h2>
        <p role="status">
          {status
            ? (stateLabels.get(status.state) ?? status.state)
            : "모델 상태 확인 중…"}
          {status && status.waiting > 0
            ? ` · 앞선 추론을 기다리는 요청 ${status.waiting}개`
            : ""}
        </p>
        {status?.error_code && (
          <p role="alert">{errorLabel(status.error_code)}</p>
        )}
        <p>
          요청 시 모델을 로딩하고, 유휴 시간이 지나면 메모리를 해제합니다.
          난이도를 판단하며 실제 AI 답변은 생성하지 않습니다.
        </p>
        {status && (
          <>
            <form
              key={`${status.minimum_keep_seconds}/${status.idle_seconds}`}
              onSubmit={savePolicy}
            >
              <div className="form-fields">
                <label>
                  최소 유지 시간 (초)
                  <input
                    name="minimum"
                    type="number"
                    min="0"
                    max="86400"
                    required
                    defaultValue={status.minimum_keep_seconds}
                  />
                </label>
                <label>
                  유휴 해제 시간 (초)
                  <input
                    name="idle"
                    type="number"
                    min="1"
                    max="86400"
                    required
                    defaultValue={status.idle_seconds}
                  />
                </label>
              </div>
              <p className="muted">
                최대 대기 {status.wait_seconds}초 · 로딩 포함 제한{" "}
                {status.timeout_seconds}초 · 로딩 전 여유 메모리{" "}
                {(status.memory_reserve_bytes / 1024 ** 3).toFixed(1)} GiB
              </p>
              <div className="detail-actions">
                <button disabled={busy}>실행 정책 저장</button>
                <button
                  type="button"
                  disabled={
                    busy ||
                    [
                      "off",
                      "disabled",
                      "loading",
                      "processing",
                      "stopping",
                    ].includes(status.state)
                  }
                  onClick={() => {
                    void perform("/api/admin/raya/release", undefined);
                  }}
                >
                  모델 메모리 해제
                </button>
              </div>
            </form>
          </>
        )}
      </section>
      <section aria-labelledby="raya-test">
        <h2 id="raya-test">요청 난이도 테스트</h2>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void perform(
              "/api/raya/route",
              JSON.stringify({
                task_type: "chat.general",
                prompt,
                has_images: hasImages,
              }),
            );
          }}
        >
          <label className="raya-prompt">
            분류할 요청
            <textarea
              required
              maxLength={16000}
              rows={5}
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
            />
          </label>
          <label className="raya-image">
            <input
              type="checkbox"
              checked={hasImages}
              onChange={(event) => setHasImages(event.target.checked)}
            />
            이미지가 포함된 요청
          </label>
          <p className="muted">
            최대 512토큰으로 판단합니다. 첫 실행은 로딩 시간이 필요합니다. 이
            화면의 요청과 결과는 학습 데이터로 저장하지 않습니다. 이미지
            내용은 분석하지 않고 포함 여부만 전달합니다.
          </p>
          <button
            className="primary"
            disabled={
              busy || !status || status.state === "disabled" || !prompt.trim()
            }
          >
            {busy ? "요청 처리 중…" : "난이도 판단"}
          </button>
        </form>
        {result && (
          <div aria-live="polite" className="raya-result">
            <h3>판단 결과: {labels.get(result.model_tier)}</h3>
            <table>
              <thead>
                <tr>
                  <th>모델 등급</th>
                  <th>예측 확률</th>
                </tr>
              </thead>
              <tbody>
                {tiers.map((tier) => (
                  <tr key={tier}>
                    <td>{labels.get(tier)}</td>
                    <td>
                      {((result.probabilities[tier] ?? 0) * 100).toFixed(1)}%
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p>
              추론 {result.inference_ms.toFixed(1)}ms · 전체{" "}
              {result.elapsed_ms.toFixed(1)}ms
              {result.cold_start ? " (모델 로딩 포함)" : ""} · 입력{" "}
              {result.input_tokens}토큰
            </p>
            {result.input_truncated && (
              <p role="status">
                입력 한도를 넘어 일부만 분석했습니다. 짧게 줄인 요청으로 다시
                확인해 주세요.
              </p>
            )}
            <p className="muted">
              예측 확률은 정답률을 뜻하지 않습니다. 실제 사용 데이터의
              검토·파인튜닝은 다음 단계입니다.
            </p>
          </div>
        )}
      </section>
    </div>
  );
}
