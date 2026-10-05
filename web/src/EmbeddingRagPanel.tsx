import { useState } from "react";
import type { FormEvent } from "react";
import { z } from "zod";
import {
  aiJobSchema,
  embeddingResponseSchema,
  mutation,
  request,
} from "./api";
import type { EmbeddingResponse, AiJob, AiJobResult } from "./api";
import { RayaPanel } from "./RayaPanel";

const ragContentSchema = z.object({
  content: z.string().optional(),
  text: z.string().optional(),
  retrieved_context: z.unknown().optional(),
}).passthrough();

function formatRagAnswer(result: AiJobResult): string {
  const stringParsed = z.string().safeParse(result);

  if (stringParsed.success) {
    return stringParsed.data;
  }

  const objParsed = ragContentSchema.safeParse(result);

  if (objParsed.success) {
    if (objParsed.data.content) return objParsed.data.content;

    if (objParsed.data.text) return objParsed.data.text;
  }

  return JSON.stringify(result, null, 2);
}

function extractRetrievedContext(result: AiJobResult): string | null {
  const objParsed = ragContentSchema.safeParse(result);

  if (objParsed.success && objParsed.data.retrieved_context !== undefined) {
    return JSON.stringify(objParsed.data.retrieved_context, null, 2);
  }

  return null;
}

export function EmbeddingRagPanel({ csrf }: { csrf: string }) {
  const [inputText, setInputText] = useState("");
  const [embeddingResult, setEmbeddingResult] = useState<EmbeddingResponse | null>(null);
  const [embeddingDuration, setEmbeddingDuration] = useState<number | null>(null);
  const [embeddingBusy, setEmbeddingBusy] = useState(false);
  const [embeddingError, setEmbeddingError] = useState("");

  const [ragQuery, setRagQuery] = useState("");
  const [ragResult, setRagResult] = useState<AiJob | null>(null);
  const [ragBusy, setRagBusy] = useState(false);
  const [ragError, setRagError] = useState("");

  const [subTab, setSubTab] = useState<"embedding" | "rag" | "raya">("embedding");

  async function handleEmbeddingTest(e: FormEvent) {
    e.preventDefault();

    if (!inputText.trim()) return;

    setEmbeddingBusy(true);
    setEmbeddingError("");
    setEmbeddingResult(null);

    const startTime = performance.now();

    try {
      const res = await request(
        "/api/ai/embeddings",
        mutation(
          csrf,
          JSON.stringify({
            input: inputText.trim(),
            model: "models/gemini-embedding-001",
            dimensions: 768,
          }),
        ),
      );

      const parsed = embeddingResponseSchema.parse(await res.json());

      setEmbeddingDuration(Math.round(performance.now() - startTime));
      setEmbeddingResult(parsed);
    } catch (err) {
      setEmbeddingError(
        err instanceof Error ? err.message : "임베딩 생성에 실패했습니다.",
      );
    } finally {
      setEmbeddingBusy(false);
    }
  }

  async function handleRagTest(e: FormEvent) {
    e.preventDefault();

    if (!ragQuery.trim()) return;

    setRagBusy(true);
    setRagError("");
    setRagResult(null);

    const reqId = `rag-test-${Date.now()}-${Math.random().toString(36).substring(2, 7)}`;

    try {
      const res = await request(
        "/api/ai/jobs",
        mutation(
          csrf,
          JSON.stringify({
            request_id: reqId,
            task_type: "portfolio_search",
            prompt: ragQuery.trim(),
            project: "rag-console",
            environment: "production",
            sync: true,
          }),
        ),
      );

      const parsed = aiJobSchema.parse(await res.json());

      setRagResult(parsed);
    } catch (err) {
      setRagError(
        err instanceof Error ? err.message : "RAG 검색 실행에 실패했습니다.",
      );
    } finally {
      setRagBusy(false);
    }
  }

  return (
    <div className="tab-panel">
      <div className="section-head">
        <div>
          <h2>공통 임베딩 · RAG 및 벡터 검색 관리</h2>
          <p>
            Google Gemini 고성능 임베딩(768차원)과 Qdrant 벡터 DB를 기반으로 한
            공통 RAG 인덱싱 및 검색 상태를 테스트하고 모니터링합니다.
          </p>
        </div>
      </div>

      <div style={{ display: "flex", gap: "8px", marginBottom: "20px" }}>
        <button
          className={subTab === "embedding" ? "active primary" : ""}
          onClick={() => setSubTab("embedding")}
        >
          공통 임베딩 테스트
        </button>
        <button
          className={subTab === "rag" ? "active primary" : ""}
          onClick={() => setSubTab("rag")}
        >
          포트폴리오 RAG · 벡터 검색 테스트
        </button>
        <button
          className={subTab === "raya" ? "active primary" : ""}
          onClick={() => setSubTab("raya")}
        >
          Raya 난이도 라우터 상태
        </button>
      </div>

      {subTab === "embedding" && (
        <section>
          <div
            style={{
              padding: "16px",
              background: "var(--paper)",
              border: "1px solid var(--line)",
              borderRadius: "8px",
              marginBottom: "20px",
            }}
          >
            <h3>공통 임베딩 모델 규격</h3>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "10px", marginTop: "10px" }}>
              <div><strong>모델명:</strong> <code>models/gemini-embedding-001</code></div>
              <div><strong>출력 차원:</strong> <code>768 dimensions</code> (공통 고정)</div>
              <div><strong>제공자:</strong> Google AI Studio API</div>
              <div><strong>연동 목적:</strong> 포트폴리오, 블로그, 문서 검색 등 전 서비스 단일 벡터 공간 공유</div>
            </div>
          </div>

          <form onSubmit={handleEmbeddingTest} style={{ marginBottom: "20px" }}>
            <label style={{ display: "block", marginBottom: "8px", fontWeight: "bold" }}>
              임베딩할 텍스트 입력
            </label>
            <textarea
              rows={4}
              value={inputText}
              onChange={(e) => setInputText(e.target.value)}
              placeholder="예: 뇌대리 프로젝트의 고자원 서비스 동적 실행 및 임베딩 정책"
              style={{
                width: "100%",
                padding: "10px",
                borderRadius: "6px",
                border: "1px solid var(--line)",
                marginBottom: "10px",
              }}
              required
            />
            <button className="primary" disabled={embeddingBusy || !inputText.trim()}>
              {embeddingBusy ? "벡터 생성 중…" : "768차원 임베딩 생성 테스트"}
            </button>
          </form>

          {embeddingError && (
            <p className="error" role="alert">
              {embeddingError}
            </p>
          )}

          {embeddingResult && (
            <div
              style={{
                padding: "16px",
                background: "var(--paper)",
                border: "1px solid var(--line)",
                borderRadius: "8px",
              }}
            >
              <h3>임베딩 생성 결과</h3>
              <div style={{ display: "flex", gap: "20px", margin: "10px 0" }}>
                <div><strong>차원 수:</strong> {embeddingResult.data[0]?.embedding.length} dims</div>
                <div><strong>소모 토큰:</strong> {embeddingResult.usage.total_tokens} tokens</div>
                <div><strong>소요 시간:</strong> {embeddingDuration} ms</div>
              </div>

              <div>
                <strong>벡터 프리뷰 (앞뒤 5개 요소):</strong>
                <pre
                  style={{
                    background: "#f4f6f5",
                    padding: "10px",
                    borderRadius: "6px",
                    fontSize: "13px",
                    overflowX: "auto",
                  }}
                >
                  {(() => {
                    const vec = embeddingResult.data[0]?.embedding ?? [];
                    const head = vec.slice(0, 5).map((n) => n.toFixed(6)).join(", ");
                    const tail = vec.slice(-5).map((n) => n.toFixed(6)).join(", ");

                    return `[${head}, ... (총 ${vec.length}개 차원) ..., ${tail}]`;
                  })()}
                </pre>
              </div>

              <button
                style={{ marginTop: "10px" }}
                onClick={() => {
                  const vec = embeddingResult.data[0]?.embedding ?? [];
                  void navigator.clipboard.writeText(JSON.stringify(vec));
                  alert("전체 768차원 벡터가 클립보드에 복사되었습니다.");
                }}
              >
                전체 768차원 벡터 JSON 복사
              </button>
            </div>
          )}
        </section>
      )}

      {subTab === "rag" && (
        <section>
          <div
            style={{
              padding: "16px",
              background: "var(--paper)",
              border: "1px solid var(--line)",
              borderRadius: "8px",
              marginBottom: "20px",
            }}
          >
            <h3>포트폴리오 RAG / 벡터 검색 파이프라인</h3>
            <p style={{ margin: "6px 0 0" }}>
              입력된 질의어를 768차원으로 임베딩한 뒤, Qdrant 벡터 DB 컬렉션에서
              유사도 높은 문서를 검색(Top-K)하고 n8n AI 에이전트가 종합 답변을 도출합니다.
            </p>
          </div>

          <form onSubmit={handleRagTest} style={{ marginBottom: "20px" }}>
            <label style={{ display: "block", marginBottom: "8px", fontWeight: "bold" }}>
              RAG 질의어 입력
            </label>
            <input
              type="text"
              value={ragQuery}
              onChange={(e) => setRagQuery(e.target.value)}
              placeholder="예: 뇌대리에서 사용되는 관리자 승인 정책에 대해 알려줘"
              style={{
                width: "100%",
                padding: "10px",
                borderRadius: "6px",
                border: "1px solid var(--line)",
                marginBottom: "10px",
              }}
              required
            />
            <button className="primary" disabled={ragBusy || !ragQuery.trim()}>
              {ragBusy ? "RAG 검색 및 AI 분석 중…" : "포트폴리오 RAG 질의 실행"}
            </button>
          </form>

          {ragError && (
            <p className="error" role="alert">
              {ragError}
            </p>
          )}

          {ragResult && (
            <div
              style={{
                padding: "16px",
                background: "var(--paper)",
                border: "1px solid var(--line)",
                borderRadius: "8px",
              }}
            >
              <h3>RAG 처리 결과 (상태: <span className={`status ${ragResult.status}`}>{ragResult.status}</span>)</h3>

              <div style={{ marginTop: "12px" }}>
                <h4>AI 종합 답변</h4>
                <div
                  style={{
                    background: "#f4f6f5",
                    padding: "14px",
                    borderRadius: "6px",
                    whiteSpace: "pre-wrap",
                    lineHeight: "1.6",
                  }}
                >
                  {formatRagAnswer(ragResult.result)}
                </div>
              </div>

              {extractRetrievedContext(ragResult.result) && (
                <div style={{ marginTop: "16px" }}>
                  <h4>Qdrant 검색 컨텍스트 (Retrieved Context)</h4>
                  <pre
                    style={{
                      background: "#eef3f0",
                      padding: "12px",
                      borderRadius: "6px",
                      fontSize: "13px",
                      overflowX: "auto",
                      maxHeight: "250px",
                    }}
                  >
                    {extractRetrievedContext(ragResult.result)}
                  </pre>
                </div>
              )}
            </div>
          )}
        </section>
      )}

      {subTab === "raya" && (
        <section>
          <RayaPanel csrf={csrf} />
        </section>
      )}
    </div>
  );
}
