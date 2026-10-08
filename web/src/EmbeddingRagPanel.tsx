import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { z } from "zod";
import {
  collectionOverviewSchema,
  embeddingResponseSchema,
  indexingJobSchema,
  mutation,
  request,
  vectorSearchResponseSchema,
} from "./api";
import type {
  CollectionOverview,
  EmbeddingResponse,
  IndexingJob,
  VectorSearchResponse,
} from "./api";
import { RayaPanel } from "./RayaPanel";

const documentSchema = z
  .object({
    id: z.string().min(1).max(256),
    title: z.string().max(512).optional(),
    content: z.string().min(1).max(16000),
    metadata: z.record(z.string(), z.json()).optional(),
  })
  .strict();

const modeSchema = z.enum(["upsert", "replace_all", "delete"]);

const modeLabels = new Map(
  Object.entries({
    upsert: "추가 · 갱신 (Upsert)",
    replace_all: "전체 교체 (Replace All)",
    delete: "삭제 (Delete)",
  }),
);

const statusLabels = new Map(
  Object.entries({
    pending: "접수 · 대기",
    running: "진행 중",
    cancelled: "취소",
    succeeded: "완료",
    failed: "실패",
  }),
);

function formatDate(isoString: string | null | undefined): string {
  if (!isoString) return "—";

  const parsed = new Date(isoString);

  return Number.isNaN(parsed.getTime())
    ? isoString
    : parsed.toLocaleString("ko-KR", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      });
}

export function EmbeddingRagPanel({
  csrf,
  initialTab = "indexing",
  isAdmin = false,
}: {
  csrf: string;
  initialTab?: "indexing" | "search" | "embedding" | "raya";
  isAdmin?: boolean;
}) {
  const [subTab, setSubTab] = useState<
    "indexing" | "search" | "embedding" | "raya"
  >(initialTab);

  const [collections, setCollections] = useState<CollectionOverview[]>([]);
  const [indexingJobs, setIndexingJobs] = useState<IndexingJob[]>([]);
  const [loadingOverview, setLoadingOverview] = useState(true);
  const [overviewError, setOverviewError] = useState("");
  const [selectedJob, setSelectedJob] = useState<IndexingJob | null>(null);
  const [idxEnvironment, setIdxEnvironment] = useState("production");

  const [idxRequestId, setIdxRequestId] = useState<string>(() =>
    crypto.randomUUID(),
  );

  const [replaceConfirmed, setReplaceConfirmed] = useState(false);

  const [batchDocuments, setBatchDocuments] = useState<
    z.infer<typeof documentSchema>[] | null
  >(null);

  const [searchProject, setSearchProject] = useState("default");
  const [searchEnvironment, setSearchEnvironment] = useState("production");
  const [searchOwner, setSearchOwner] = useState("");
  const [searchTarget, setSearchTarget] = useState("");

  // Indexing Form State
  const [idxCollection, setIdxCollection] = useState("portfolio");

  const [idxMode, setIdxMode] = useState<"upsert" | "replace_all" | "delete">(
    "upsert",
  );

  const [idxDocId, setIdxDocId] = useState("");
  const [idxTitle, setIdxTitle] = useState("");
  const [idxContent, setIdxContent] = useState("");
  const [idxProject, setIdxProject] = useState("default");
  const [idxSubmitting, setIdxSubmitting] = useState(false);
  const [idxNotice, setIdxNotice] = useState("");
  const [idxError, setIdxError] = useState("");

  // Vector Search State
  const [searchQuery, setSearchQuery] = useState("");
  const [searchCollection, setSearchCollection] = useState("portfolio");

  const [searchResult, setSearchResult] = useState<VectorSearchResponse | null>(
    null,
  );

  const [searchBusy, setSearchBusy] = useState(false);
  const [searchError, setSearchError] = useState("");

  // Direct Embedding Test State
  const [inputText, setInputText] = useState("");

  const [embeddingResult, setEmbeddingResult] =
    useState<EmbeddingResponse | null>(null);

  const [embeddingDuration, setEmbeddingDuration] = useState<number | null>(
    null,
  );

  const [embeddingBusy, setEmbeddingBusy] = useState(false);
  const [embeddingError, setEmbeddingError] = useState("");

  async function refreshCollectionsAndJobs() {
    try {
      const [colsRes, jobsRes] = await Promise.all([
        request("/api/ai/indexing/collections"),
        request("/api/ai/indexing?limit=20"),
      ]);

      const colsData = z
        .array(collectionOverviewSchema)
        .parse(await colsRes.json());

      const jobsData = z.array(indexingJobSchema).parse(await jobsRes.json());

      setCollections(colsData);
      setIndexingJobs(jobsData);
      setOverviewError("");
      setSelectedJob((current) =>
        current
          ? (jobsData.find((job) => job.id === current.id) ?? current)
          : null,
      );
    } catch {
      setOverviewError(
        "현황을 갱신하지 못했습니다. 연결과 로그인 상태를 확인한 뒤 새로고침해 주세요.",
      );
    } finally {
      setLoadingOverview(false);
    }
  }

  useEffect(() => {
    void refreshCollectionsAndJobs();

    const timer = window.setInterval(() => {
      void refreshCollectionsAndJobs();
    }, 5000);

    return () => window.clearInterval(timer);
  }, []);

  const selectedJobId = selectedJob?.id;

  useEffect(() => {
    if (!selectedJobId) return;

    let active = true;

    async function refreshDetail() {
      try {
        const res = await request(`/api/ai/indexing/${selectedJobId}`);
        const job = indexingJobSchema.parse(await res.json());

        if (active) setSelectedJob(job);
      } catch {
        if (active)
          setOverviewError(
            "작업 상세를 갱신하지 못했습니다. 새로고침해 주세요.",
          );
      }
    }

    const timer = window.setInterval(() => void refreshDetail(), 5000);

    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [selectedJobId]);

  useEffect(() => {
    setReplaceConfirmed(false);
  }, [idxProject, idxEnvironment, idxCollection]);

  async function handleExecuteIndexing(e: FormEvent) {
    e.preventDefault();

    if (idxMode !== "delete" && batchDocuments === null && !idxContent.trim()) {
      setIdxError("문서 본문 내용을 입력해 주세요.");

      return;
    }

    if ((idxMode === "delete" || batchDocuments === null) && !idxDocId.trim()) {
      setIdxError("문서 식별자(ID)를 입력해 주세요.");

      return;
    }

    setIdxSubmitting(true);
    setIdxError("");
    setIdxNotice("");

    try {
      const payload = {
        request_id: idxRequestId,
        project: idxProject.trim() || "default",
        environment: idxEnvironment.trim(),
        collection: idxCollection.trim() || "portfolio",
        mode: idxMode,
        documents:
          idxMode === "delete"
            ? []
            : (batchDocuments ?? [
                {
                  id: idxDocId.trim(),
                  title: idxTitle.trim() || undefined,
                  content: idxContent.trim(),
                  metadata: { indexed_via: "web_console" },
                },
              ]),
        delete_ids: idxMode === "delete" ? [idxDocId.trim()] : [],
        sync: false,
      };

      const res = await request(
        "/api/ai/indexing",
        mutation(csrf, JSON.stringify(payload)),
      );

      const parsed = indexingJobSchema.parse(await res.json());

      setSelectedJob(parsed);

      if (parsed.status === "failed") {
        setIdxError(
          `인덱싱 실패: ${parsed.error_message || parsed.error_code}`,
        );
      } else {
        setIdxNotice(
          `${parsed.reused ? "기존 작업 조회" : "작업 접수"}: ${parsed.request_id}. 아래 상세에서 진행 상태와 결과를 확인하세요.`,
        );
      }

      await refreshCollectionsAndJobs();
    } catch (err) {
      setIdxError(
        err instanceof Error ? err.message : "인덱싱 작업 실행에 실패했습니다.",
      );
    } finally {
      setIdxSubmitting(false);
    }
  }

  async function handleSearch(e: FormEvent) {
    e.preventDefault();

    if (!searchQuery.trim()) return;

    setSearchBusy(true);
    setSearchError("");
    setSearchResult(null);

    try {
      const res = await request(
        "/api/ai/indexing/search",
        mutation(
          csrf,
          JSON.stringify({
            owner_id: searchOwner || undefined,
            query: searchQuery.trim(),
            collection: searchCollection.trim() || "portfolio",
            project: searchProject.trim(),
            environment: searchEnvironment.trim(),
            limit: 5,
          }),
        ),
      );

      const parsed = vectorSearchResponseSchema.parse(await res.json());

      setSearchResult(parsed);
    } catch (err) {
      setSearchError(
        err instanceof Error ? err.message : "벡터 검색에 실패했습니다.",
      );
    } finally {
      setSearchBusy(false);
    }
  }

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

  return (
    <div className="tab-panel indexing-panel">
      <div className="section-head">
        <div>
          <h2>공통 임베딩 · 문서 인덱싱 및 벡터 검색</h2>
          <p>
            문서를 추가·교체·삭제하고 같은 모델과 768차원 벡터로 검색합니다.
            문서는 명시적으로 삭제할 때까지 보관되며 기존 Qdrant 예제와 별도
            저장소입니다.
          </p>
        </div>
        <button onClick={() => void refreshCollectionsAndJobs()}>
          새로고침
        </button>
      </div>

      {overviewError && (
        <p className="error" role="alert">
          {overviewError}
        </p>
      )}
      <div className="indexing-tabs" aria-label="임베딩 관리 메뉴">
        <button
          className={subTab === "indexing" ? "active primary" : ""}
          aria-pressed={subTab === "indexing"}
          onClick={() => setSubTab("indexing")}
        >
          문서 인덱싱 관리 (색인·교체·삭제)
        </button>
        <button
          className={subTab === "search" ? "active primary" : ""}
          aria-pressed={subTab === "search"}
          onClick={() => setSubTab("search")}
        >
          실시간 벡터 검색 (RAG Query)
        </button>
        <button
          className={subTab === "embedding" ? "active primary" : ""}
          aria-pressed={subTab === "embedding"}
          onClick={() => setSubTab("embedding")}
        >
          단일 텍스트 임베딩 테스트
        </button>
        {isAdmin && (
          <button
            className={subTab === "raya" ? "active primary" : ""}
            aria-pressed={subTab === "raya"}
            onClick={() => setSubTab("raya")}
          >
            Raya 난이도 라우터 상태
          </button>
        )}
      </div>

      {subTab === "indexing" && (
        <section>
          <h3>벡터 컬렉션 현황</h3>
          {loadingOverview && collections.length === 0 ? (
            <p>컬렉션 현황 조회 중…</p>
          ) : collections.length === 0 ? (
            <p className="muted">아직 등록된 문서 인덱스가 없습니다.</p>
          ) : (
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
                gap: "12px",
                marginBottom: "20px",
              }}
            >
              {collections.map((col) => (
                <div
                  key={`${col.owner_id}-${col.collection}-${col.project}-${col.environment}`}
                  style={{
                    padding: "16px",
                    background: "var(--paper)",
                    border: "1px solid var(--line)",
                    borderRadius: "8px",
                  }}
                >
                  <div style={{ fontSize: "14px", color: "var(--muted)" }}>
                    컬렉션: <strong>{col.collection}</strong> ({col.project} ·{" "}
                    {col.environment})
                  </div>
                  <div
                    style={{
                      fontSize: "24px",
                      fontWeight: "bold",
                      marginTop: "4px",
                    }}
                  >
                    {col.document_count.toLocaleString()}건
                  </div>
                  <div
                    style={{
                      fontSize: "12px",
                      color: "var(--muted)",
                      marginTop: "4px",
                    }}
                  >
                    저장 문서 추정 토큰: {col.total_tokens.toLocaleString()} ·
                    갱신: {formatDate(col.last_updated_at)}
                  </div>
                </div>
              ))}
            </div>
          )}

          <div
            style={{
              padding: "20px",
              background: "var(--paper)",
              border: "1px solid var(--line)",
              borderRadius: "8px",
              marginBottom: "24px",
            }}
          >
            <h3>새 문서 인덱싱 테스트 실행</h3>
            <p style={{ margin: "4px 0 16px" }}>
              현재 로그인한 소유자의 인덱스에 반영합니다. 대기 작업은 취소할 수
              있습니다. 같은 요청 ID로 재전송하면 다시 실행하지 않습니다.
            </p>

            {idxNotice && (
              <p
                className="notice"
                role="status"
                style={{ color: "var(--green)", fontWeight: "bold" }}
              >
                {idxNotice}
              </p>
            )}
            {idxError && (
              <p className="error" role="alert">
                {idxError}
              </p>
            )}

            <form onSubmit={handleExecuteIndexing}>
              <div className="indexing-fields">
                <label>
                  컬렉션명
                  <input
                    type="text"
                    value={idxCollection}
                    onChange={(e) => setIdxCollection(e.target.value)}
                    placeholder="portfolio"
                    required
                  />
                </label>
                <label>
                  프로젝트
                  <input
                    type="text"
                    value={idxProject}
                    onChange={(e) => setIdxProject(e.target.value)}
                    placeholder="default"
                    required
                  />
                </label>
                <label>
                  환경
                  <input
                    value={idxEnvironment}
                    onChange={(e) => setIdxEnvironment(e.target.value)}
                    maxLength={64}
                    required
                  />
                </label>
                <label>
                  요청 ID
                  <input
                    value={idxRequestId}
                    onChange={(e) => setIdxRequestId(e.target.value)}
                    maxLength={128}
                    required
                  />
                </label>
                <label>
                  인덱싱 모드
                  <select
                    value={idxMode}
                    onChange={(e) => {
                      const parsed = modeSchema.safeParse(e.target.value);

                      if (parsed.success) {
                        setIdxMode(parsed.data);
                        setReplaceConfirmed(false);
                      }
                    }}
                  >
                    <option value="upsert">추가 · 갱신 (Upsert)</option>
                    <option value="replace_all">전체 교체 (Replace All)</option>
                    <option value="delete">문서 삭제 (Delete)</option>
                  </select>
                </label>
              </div>

              {(batchDocuments === null || idxMode === "delete") && (
                <div className="indexing-fields">
                  <label>
                    문서 ID (식별자)
                    <input
                      type="text"
                      value={idxDocId}
                      onChange={(e) => setIdxDocId(e.target.value)}
                      placeholder="예: career_001, doc_intro"
                      required
                    />
                  </label>
                  <label>
                    문서 제목 (선택)
                    <input
                      type="text"
                      value={idxTitle}
                      onChange={(e) => setIdxTitle(e.target.value)}
                      placeholder="예: 백엔드 아키텍처 및 파이프라인 설계"
                    />
                  </label>
                </div>
              )}

              {idxMode !== "delete" && batchDocuments === null && (
                <div style={{ marginBottom: "16px" }}>
                  <label
                    htmlFor="index-document-content"
                    style={{
                      display: "block",
                      marginBottom: "4px",
                      fontWeight: "bold",
                    }}
                  >
                    문서 본문 내용 (임베딩 대상 텍스트)
                  </label>
                  <textarea
                    id="index-document-content"
                    maxLength={16000}
                    rows={4}
                    value={idxContent}
                    onChange={(e) => setIdxContent(e.target.value)}
                    placeholder="검색 인덱스에 반영할 구체적인 텍스트를 입력하세요…"
                    style={{
                      width: "100%",
                      padding: "10px",
                      borderRadius: "6px",
                      border: "1px solid var(--line)",
                    }}
                    required
                  />
                </div>
              )}

              {idxMode !== "delete" && (
                <label className="indexing-upload">
                  일괄 문서 JSON 업로드 (최대 100건 · 1 MiB)
                  <input
                    type="file"
                    accept="application/json,.json"
                    onChange={(e) => {
                      const file = e.target.files?.[0];

                      if (!file) return;

                      setBatchDocuments(null);
                      setReplaceConfirmed(false);

                      const documentsSchema = z.array(documentSchema).max(100);

                      if (file.size > 1048576) {
                        setIdxError(
                          "문서 JSON은 1 MiB 이하로 업로드해 주세요.",
                        );

                        return;
                      }

                      void file
                        .text()
                        .then((text) => {
                          setBatchDocuments(
                            documentsSchema.parse(JSON.parse(text)),
                          );
                          setIdxError("");
                        })
                        .catch(() =>
                          setIdxError(
                            "JSON 배열의 각 문서에 id와 content를 입력해 주세요.",
                          ),
                        );
                    }}
                  />
                  <span className="muted">
                    형식: [{'{"id":"doc-1","content":"문서 본문"}'}]
                  </span>
                </label>
              )}
              {batchDocuments !== null && idxMode !== "delete" && (
                <p>
                  일괄 문서 {batchDocuments.length}건 선택됨{" "}
                  <button type="button" onClick={() => setBatchDocuments(null)}>
                    단일 문서 입력으로 전환
                  </button>
                </p>
              )}
              {idxMode === "replace_all" && (
                <label className="checkbox-field">
                  <input
                    type="checkbox"
                    checked={replaceConfirmed}
                    onChange={(e) => setReplaceConfirmed(e.target.checked)}
                  />
                  이 소유자·프로젝트·환경·컬렉션의 기존 문서를 모두 교체합니다.
                </label>
              )}
              <div className="indexing-actions">
                <button
                  className="primary"
                  disabled={
                    idxSubmitting ||
                    (idxMode === "replace_all" && !replaceConfirmed)
                  }
                >
                  {idxSubmitting
                    ? "작업 접수 중…"
                    : `${modeLabels.get(idxMode)} 실행`}
                </button>
                <button
                  type="button"
                  disabled={idxSubmitting}
                  onClick={() => {
                    setIdxRequestId(crypto.randomUUID());
                    setIdxNotice("");
                  }}
                >
                  새 요청 ID 생성
                </button>
              </div>
            </form>
          </div>

          {selectedJob && (
            <section className="indexing-detail" aria-label="인덱싱 작업 상세">
              <h3>작업 상세: {selectedJob.request_id}</h3>
              <p>
                상태:{" "}
                {statusLabels.get(selectedJob.status) ?? selectedJob.status} ·
                소유자: <code>{selectedJob.owner_id}</code>
              </p>
              <p>
                문서 {selectedJob.document_count}건 요청 · 색인{" "}
                {selectedJob.indexed_count}건 · 삭제 {selectedJob.deleted_count}
                건
              </p>
              {selectedJob.error_code && (
                <p className="error" role="alert">
                  실패 원인:{" "}
                  {selectedJob.error_message || selectedJob.error_code}. 수정 후
                  새 요청 ID로 실행하세요.
                </p>
              )}
              {selectedJob.status === "pending" && (
                <button
                  onClick={() => {
                    void request(
                      `/api/ai/indexing/${selectedJob.id}/cancel`,
                      mutation(csrf),
                    )
                      .then((res) => res.json())
                      .then((data) => {
                        setSelectedJob(indexingJobSchema.parse(data));
                        void refreshCollectionsAndJobs();
                      })
                      .catch(() =>
                        setIdxError(
                          "작업을 취소하지 못했습니다. 실행 상태를 새로고침해 주세요.",
                        ),
                      );
                  }}
                >
                  대기 작업 취소
                </button>
              )}
              {selectedJob.result_state === "expired" ? (
                <p>
                  결과 요약이 만료되었습니다. 작업 이력과 색인 문서는
                  유지됩니다.
                </p>
              ) : (
                selectedJob.result && (
                  <>
                    <pre>{JSON.stringify(selectedJob.result, null, 2)}</pre>
                    <a
                      className="button"
                      download={`indexing-${selectedJob.id}.json`}
                      href={`data:application/json;charset=utf-8,${encodeURIComponent(JSON.stringify(selectedJob.result, null, 2))}`}
                    >
                      결과 JSON 다운로드
                    </a>
                  </>
                )
              )}
              <p className="muted">
                결과 삭제 예정: {formatDate(selectedJob.expires_at)} · 문서는
                삭제 또는 전체 교체할 때까지 유지됩니다.
              </p>
            </section>
          )}
          <h3>최근 인덱싱 작업 이력</h3>
          {indexingJobs.length === 0 ? (
            <p className="muted">최근 인덱싱 작업 내역이 없습니다.</p>
          ) : (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>요청 ID</th>
                    <th>프로젝트 · 환경</th>
                    <th>컬렉션</th>
                    <th>모드</th>
                    <th>상태</th>
                    <th>색인 건수</th>
                    <th>삭제 건수</th>
                    <th>추정 토큰</th>
                    <th>생성 시각</th>
                    <th>결과 보관</th>
                  </tr>
                </thead>
                <tbody>
                  {indexingJobs.map((job) => (
                    <tr key={job.id}>
                      <td>
                        <button
                          onClick={() => setSelectedJob(job)}
                          aria-expanded={selectedJob?.id === job.id}
                        >
                          {job.request_id}
                        </button>
                      </td>
                      <td>
                        {job.project} · {job.environment}
                      </td>
                      <td>
                        <strong>{job.collection}</strong>
                      </td>
                      <td>{modeLabels.get(job.mode) ?? job.mode}</td>
                      <td>
                        <span className={`status ${job.status}`}>
                          {statusLabels.get(job.status) ?? job.status}
                        </span>
                      </td>
                      <td>{job.indexed_count}건</td>
                      <td>{job.deleted_count}건</td>
                      <td>{job.total_tokens.toLocaleString()}</td>
                      <td>{formatDate(job.created_at)}</td>
                      <td>
                        {job.result_state === "expired"
                          ? "결과 만료"
                          : formatDate(job.expires_at)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      {subTab === "search" && (
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
            <h3>실시간 벡터 검색 콘솔 (Cosine Similarity)</h3>
            <p style={{ margin: "4px 0 0" }}>
              선택한 소유자의 지정 프로젝트·환경·컬렉션에서 입력된 질의어를
              768차원으로 임베딩하여 색인된 문서 벡터들과 코사인 유사도를
              계산하고, 가장 일치하는 문서를 반환합니다.
            </p>
          </div>

          <form onSubmit={handleSearch} style={{ marginBottom: "20px" }}>
            <label className="indexing-upload">
              검색 대상 컬렉션
              <select
                value={searchTarget}
                onChange={(e) => {
                  const target = collections.find(
                    (col) =>
                      `${col.owner_id}/${col.project}/${col.environment}/${col.collection}` ===
                      e.target.value,
                  );

                  setSearchTarget(e.target.value);
                  setSearchOwner(target?.owner_id ?? "");

                  if (target) {
                    setSearchProject(target.project);
                    setSearchEnvironment(target.environment);
                    setSearchCollection(target.collection);
                  }

                  setSearchResult(null);
                }}
              >
                <option value="">내 색인 · 직접 입력</option>
                {collections.map((col) => {
                  const value = `${col.owner_id}/${col.project}/${col.environment}/${col.collection}`;

                  return (
                    <option key={value} value={value}>
                      {col.collection} · {col.project} · {col.environment} ·{" "}
                      {col.owner_id}
                    </option>
                  );
                })}
              </select>
              <span className="muted">
                관리자는 플랫폼과 다른 사용자의 컬렉션도 선택해 검색할 수
                있습니다.
              </span>
            </label>
            <div className="indexing-fields">
              <label>
                컬렉션
                <input
                  type="text"
                  value={searchCollection}
                  onChange={(e) => setSearchCollection(e.target.value)}
                  placeholder="portfolio"
                  required
                />
              </label>
              <label>
                검색 프로젝트
                <input
                  value={searchProject}
                  onChange={(e) => setSearchProject(e.target.value)}
                  maxLength={64}
                  required
                />
              </label>
              <label>
                검색 환경
                <input
                  value={searchEnvironment}
                  onChange={(e) => setSearchEnvironment(e.target.value)}
                  maxLength={64}
                  required
                />
              </label>
              <label>
                검색 질의어 (Query)
                <input
                  type="text"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder="예: 파이썬 고성능 백엔드 설계 및 임베딩 처리"
                  required
                />
              </label>
            </div>
            <button
              className="primary"
              disabled={searchBusy || !searchQuery.trim()}
            >
              {searchBusy ? "유사도 검색 중…" : "벡터 유사도 검색"}
            </button>
          </form>

          {searchError && (
            <p className="error" role="alert">
              {searchError}
            </p>
          )}

          {searchResult && (
            <div>
              <h3>
                검색 결과 (후보: {searchResult.total_candidates}건 중{" "}
                {searchResult.matched_count}건 매칭)
              </h3>
              {searchResult.results.length === 0 ? (
                <p className="muted">일치하는 문서가 없습니다.</p>
              ) : (
                <div
                  style={{
                    display: "flex",
                    flexDirection: "column",
                    gap: "12px",
                  }}
                >
                  {searchResult.results.map((item, idx) => (
                    <div
                      key={`${item.document_id}-${idx}`}
                      style={{
                        padding: "16px",
                        background: "var(--paper)",
                        border: "1px solid var(--line)",
                        borderRadius: "8px",
                      }}
                    >
                      <div
                        style={{
                          display: "flex",
                          justifyContent: "space-between",
                          alignItems: "center",
                        }}
                      >
                        <div>
                          <strong>{item.title || item.document_id}</strong>
                          <span
                            style={{
                              marginLeft: "8px",
                              fontSize: "12px",
                              color: "var(--muted)",
                            }}
                          >
                            ID: <code>{item.document_id}</code>
                          </span>
                        </div>
                        <span
                          style={{
                            background: "var(--selected)",
                            color: "var(--green)",
                            padding: "4px 8px",
                            borderRadius: "4px",
                            fontWeight: "bold",
                            fontSize: "13px",
                          }}
                        >
                          유사도: {(item.similarity * 100).toFixed(1)}%
                        </span>
                      </div>
                      <p
                        style={{
                          margin: "8px 0 0",
                          whiteSpace: "pre-wrap",
                          lineHeight: "1.5",
                        }}
                      >
                        {item.content}
                      </p>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </section>
      )}

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
            <h3>단일 텍스트 임베딩 생성기</h3>
            <p style={{ margin: "4px 0 0" }}>
              <code>models/gemini-embedding-001</code> 고정 모델을 통해 768차원
              표준 벡터를 추출합니다.
            </p>
          </div>

          <form onSubmit={handleEmbeddingTest} style={{ marginBottom: "20px" }}>
            <label
              htmlFor="embedding-text-input"
              style={{
                display: "block",
                marginBottom: "8px",
                fontWeight: "bold",
              }}
            >
              임베딩할 텍스트 입력
            </label>
            <textarea
              id="embedding-text-input"
              maxLength={16000}
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
            <button
              className="primary"
              disabled={embeddingBusy || !inputText.trim()}
            >
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
                <div>
                  <strong>차원 수:</strong>{" "}
                  {embeddingResult.data[0]?.embedding.length} dims
                </div>
                <div>
                  <strong>추정 토큰:</strong>{" "}
                  {embeddingResult.usage.total_tokens} tokens
                </div>
                <div>
                  <strong>소요 시간:</strong> {embeddingDuration} ms
                </div>
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

                    const head = vec
                      .slice(0, 5)
                      .map((n) => n.toFixed(6))
                      .join(", ");

                    const tail = vec
                      .slice(-5)
                      .map((n) => n.toFixed(6))
                      .join(", ");

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

      {subTab === "raya" && (
        <section>
          <RayaPanel csrf={csrf} />
        </section>
      )}
    </div>
  );
}
