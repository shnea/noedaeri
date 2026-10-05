import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { z } from "zod";
import {
  aiJobSchema,
  aiUsageResponseSchema,
  errorLabel,
  mutation,
  request,
} from "./api";
import type { AiJob, AiJobResult, AiUsageResponse } from "./api";

const taskTypeLabels = new Map(
  Object.entries({
    blog_summary: "블로그 요약",
    blog_tag: "블로그 태그 생성",
    portfolio_search: "포트폴리오 검색 (RAG)",
    ui_generation: "UI 생성",
    comment_generation: "댓글 생성",
    doc_analysis: "문서 분석",
    code_analysis: "코드 분석",
    qa: "일반 질답",
  }),
);

const statusLabels = new Map(
  Object.entries({
    running: "실행 중",
    succeeded: "완료",
    failed: "실패",
    cancelled: "취소됨",
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

function formatResult(value: AiJobResult): string {
  const stringParsed = z.string().safeParse(value);

  if (stringParsed.success) {
    return stringParsed.data;
  }

  return JSON.stringify(value, null, 2);
}

export function AiJobsPanel({ csrf }: { csrf: string }) {
  const [jobs, setJobs] = useState<AiJob[]>([]);
  const [usage, setUsage] = useState<AiUsageResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const [statusFilter, setStatusFilter] = useState("all");
  const [taskFilter, setTaskFilter] = useState("all");
  const [searchQuery, setSearchQuery] = useState("");

  const [selectedJob, setSelectedJob] = useState<AiJob | null>(null);

  const [showCreate, setShowCreate] = useState(false);
  const [newTaskType, setNewTaskType] = useState("qa");
  const [newPrompt, setNewPrompt] = useState("");
  const [newSync, setNewSync] = useState(true);
  const [submitting, setSubmitting] = useState(false);

  async function refresh() {
    try {
      const [jobsRes, usageRes] = await Promise.all([
        request("/api/ai/jobs?limit=100"),
        request("/api/ai/usage?limit=50"),
      ]);

      const jobsData = z.array(aiJobSchema).parse(await jobsRes.json());
      const usageData = aiUsageResponseSchema.parse(await usageRes.json());

      setJobs(jobsData);
      setUsage(usageData);
      setError("");
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "AI 작업 목록을 불러오지 못했습니다.",
      );
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();

    const interval = window.setInterval(() => {
      void refresh();
    }, 5000);

    return () => window.clearInterval(interval);
  }, []);

  async function handleCancel(jobId: string) {
    if (!confirm("해당 AI 작업을 취소하시겠습니까?")) return;

    try {
      await request(`/api/ai/jobs/${jobId}/cancel`, mutation(csrf));
      setNotice("작업이 취소되었습니다.");
      await refresh();
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "작업 취소에 실패했습니다.",
      );
    }
  }

  async function handleCreateJob(e: FormEvent) {
    e.preventDefault();

    if (!newPrompt.trim()) return;

    setSubmitting(true);
    setError("");
    setNotice("");

    const reqId = `web-test-${Date.now()}-${Math.random().toString(36).substring(2, 7)}`;

    try {
      const payload = {
        request_id: reqId,
        task_type: newTaskType,
        prompt: newPrompt.trim(),
        project: "web-test",
        environment: "production",
        sync: newSync,
      };

      const res = await request(
        "/api/ai/jobs",
        mutation(csrf, JSON.stringify(payload)),
      );

      const created = aiJobSchema.parse(await res.json());

      setNotice(
        newSync
          ? "AI 작업이 성공적으로 실행 완료되었습니다."
          : "AI 비동기 작업이 등록되었습니다. 상태를 추적합니다.",
      );
      setNewPrompt("");
      setShowCreate(false);
      setSelectedJob(created);
      await refresh();
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "AI 작업 실행에 실패했습니다.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  const filteredJobs = jobs.filter((job) => {
    if (statusFilter !== "all" && job.status !== statusFilter) return false;

    if (taskFilter !== "all" && job.task_type !== taskFilter) return false;

    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();

      return (
        job.request_id.toLowerCase().includes(q) ||
        job.project.toLowerCase().includes(q) ||
        job.task_type.toLowerCase().includes(q)
      );
    }

    return true;
  });

  const totalTokens =
    usage?.summary.reduce((acc, curr) => acc + curr.total_tokens, 0) ?? 0;

  const totalCalls =
    usage?.summary.reduce((acc, curr) => acc + curr.call_count, 0) ?? 0;

  return (
    <div className="tab-panel">
      <div className="section-head">
        <div>
          <h2>AI 작업 관리 및 사용량 통계</h2>
          <p>
            플랫폼 및 웹에서 n8n 워크플로를 통해 실행된 8대 AI 작업의 상태, 결과,
            소모 토큰을 관리합니다.
          </p>
        </div>
        <div style={{ display: "flex", gap: "8px" }}>
          <button className="primary" onClick={() => setShowCreate(true)}>
            + 새 AI 작업 테스트
          </button>
          <button onClick={() => void refresh()}>새로고침</button>
        </div>
      </div>

      {notice && (
        <p className="notice" role="status" style={{ color: "var(--green)", fontWeight: "bold" }}>
          {notice}
        </p>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}

      <section style={{ marginBottom: "24px" }}>
        <h3>모델별 누적 사용량 요약</h3>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
            gap: "12px",
            marginBottom: "16px",
          }}
        >
          <div
            style={{
              padding: "16px",
              background: "var(--paper)",
              border: "1px solid var(--line)",
              borderRadius: "8px",
            }}
          >
            <div style={{ fontSize: "14px", color: "var(--muted)" }}>총 호출 횟수</div>
            <div style={{ fontSize: "28px", fontWeight: "bold", marginTop: "4px" }}>
              {totalCalls.toLocaleString()}회
            </div>
          </div>
          <div
            style={{
              padding: "16px",
              background: "var(--paper)",
              border: "1px solid var(--line)",
              borderRadius: "8px",
            }}
          >
            <div style={{ fontSize: "14px", color: "var(--muted)" }}>총 토큰 소모</div>
            <div
              style={{
                fontSize: "28px",
                fontWeight: "bold",
                color: "var(--green)",
                marginTop: "4px",
              }}
            >
              {totalTokens.toLocaleString()} tokens
            </div>
          </div>
        </div>

        {usage && usage.summary.length > 0 && (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>제공자 (Provider)</th>
                  <th>모델명</th>
                  <th>작업 유형</th>
                  <th>호출 수</th>
                  <th>입력 토큰</th>
                  <th>완성 토큰</th>
                  <th>총 토큰</th>
                </tr>
              </thead>
              <tbody>
                {usage.summary.map((row, idx) => (
                  <tr key={`${row.provider}-${row.model}-${row.task_type}-${idx}`}>
                    <td><strong>{row.provider}</strong></td>
                    <td><code>{row.model}</code></td>
                    <td>{taskTypeLabels.get(row.task_type) ?? row.task_type}</td>
                    <td>{row.call_count.toLocaleString()}</td>
                    <td>{row.total_prompt_tokens.toLocaleString()}</td>
                    <td>{row.total_completion_tokens.toLocaleString()}</td>
                    <td><strong>{row.total_tokens.toLocaleString()}</strong></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section style={{ marginBottom: "16px" }}>
        <div
          style={{
            display: "flex",
            gap: "12px",
            flexWrap: "wrap",
            alignItems: "center",
          }}
        >
          <label style={{ display: "flex", alignItems: "center", gap: "6px" }}>
            상태:
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
            >
              <option value="all">전체 상태</option>
              <option value="running">실행 중</option>
              <option value="succeeded">완료</option>
              <option value="failed">실패</option>
              <option value="cancelled">취소됨</option>
            </select>
          </label>

          <label style={{ display: "flex", alignItems: "center", gap: "6px" }}>
            작업 유형:
            <select
              value={taskFilter}
              onChange={(e) => setTaskFilter(e.target.value)}
            >
              <option value="all">전체 작업</option>
              {Array.from(taskTypeLabels.entries()).map(([key, label]) => (
                <option key={key} value={key}>
                  {label}
                </option>
              ))}
            </select>
          </label>

          <label style={{ display: "flex", alignItems: "center", gap: "6px" }}>
            검색:
            <input
              type="text"
              placeholder="요청 ID, 프로젝트 검색…"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              style={{ width: "220px" }}
            />
          </label>
        </div>
      </section>

      <section>
        <h3>AI 작업 목록 ({filteredJobs.length}건)</h3>
        {loading && jobs.length === 0 ? (
          <p>작업 목록을 불러오는 중…</p>
        ) : filteredJobs.length === 0 ? (
          <p className="muted">조건에 일치하는 AI 작업이 없습니다.</p>
        ) : (
          <div className="table-scroll">
            <table className="jobs-table">
              <thead>
                <tr>
                  <th>요청 ID</th>
                  <th>작업 유형</th>
                  <th>상태</th>
                  <th>프로젝트 / 환경</th>
                  <th>생성 시각</th>
                  <th>만료 예정 시각</th>
                  <th>관리</th>
                </tr>
              </thead>
              <tbody>
                {filteredJobs.map((job) => (
                  <tr key={job.id}>
                    <td>
                      <code>{job.request_id}</code>
                    </td>
                    <td>{taskTypeLabels.get(job.task_type) ?? job.task_type}</td>
                    <td>
                      <span className={`status ${job.status}`}>
                        {statusLabels.get(job.status) ?? job.status}
                      </span>
                    </td>
                    <td>
                      {job.project} / <code>{job.environment}</code>
                    </td>
                    <td>{formatDate(job.created_at)}</td>
                    <td>{formatDate(job.expires_at)}</td>
                    <td>
                      <div style={{ display: "flex", gap: "6px" }}>
                        <button onClick={() => setSelectedJob(job)}>
                          상세 보기
                        </button>
                        {job.status === "running" && (
                          <button
                            className="danger"
                            onClick={() => void handleCancel(job.id)}
                          >
                            취소
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {selectedJob && (
        <div
          role="dialog"
          aria-modal="true"
          style={{
            position: "fixed",
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            backgroundColor: "rgba(0,0,0,0.5)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 1000,
            padding: "20px",
          }}
          onClick={() => setSelectedJob(null)}
        >
          <div
            style={{
              background: "var(--paper)",
              padding: "24px",
              borderRadius: "8px",
              maxWidth: "800px",
              width: "100%",
              maxHeight: "85vh",
              overflowY: "auto",
              boxShadow: "0 8px 30px rgba(0,0,0,0.2)",
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                marginBottom: "16px",
              }}
            >
              <h3>작업 상세: {taskTypeLabels.get(selectedJob.task_type) ?? selectedJob.task_type}</h3>
              <button onClick={() => setSelectedJob(null)}>닫기</button>
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "10px", marginBottom: "16px" }}>
              <div><strong>작업 ID:</strong> <code>{selectedJob.id}</code></div>
              <div><strong>요청 ID:</strong> <code>{selectedJob.request_id}</code></div>
              <div><strong>상태:</strong> <span className={`status ${selectedJob.status}`}>{statusLabels.get(selectedJob.status) ?? selectedJob.status}</span></div>
              <div><strong>프로젝트 / 환경:</strong> {selectedJob.project} / {selectedJob.environment}</div>
              <div><strong>생성 시각:</strong> {formatDate(selectedJob.created_at)}</div>
              <div><strong>종료 시각:</strong> {formatDate(selectedJob.finished_at)}</div>
              <div><strong>만료 예정 시각:</strong> {formatDate(selectedJob.expires_at)} (24시간 정책)</div>
            </div>

            {selectedJob.error_code && (
              <div
                style={{
                  padding: "12px",
                  background: "#fff0ef",
                  border: "1px solid #cb9494",
                  borderRadius: "6px",
                  color: "#9d2929",
                  marginBottom: "16px",
                }}
              >
                <div><strong>오류 코드:</strong> {selectedJob.error_code}</div>
                <div><strong>오류 내용:</strong> {selectedJob.error_message ?? errorLabel(selectedJob.error_code)}</div>
              </div>
            )}

            <div>
              <h4>결과 데이터 (Result)</h4>
              {selectedJob.result ? (
                <pre
                  style={{
                    background: "#f4f6f5",
                    padding: "12px",
                    borderRadius: "6px",
                    overflowX: "auto",
                    maxHeight: "350px",
                    fontSize: "13px",
                  }}
                >
                  {formatResult(selectedJob.result)}
                </pre>
              ) : (
                <p className="muted">반환된 결과가 아직 없습니다.</p>
              )}
            </div>
          </div>
        </div>
      )}

      {showCreate && (
        <div
          role="dialog"
          aria-modal="true"
          style={{
            position: "fixed",
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            backgroundColor: "rgba(0,0,0,0.5)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 1000,
            padding: "20px",
          }}
          onClick={() => setShowCreate(false)}
        >
          <div
            style={{
              background: "var(--paper)",
              padding: "24px",
              borderRadius: "8px",
              maxWidth: "600px",
              width: "100%",
              boxShadow: "0 8px 30px rgba(0,0,0,0.2)",
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                marginBottom: "16px",
              }}
            >
              <h3>새 AI 작업 테스트 실행</h3>
              <button onClick={() => setShowCreate(false)}>닫기</button>
            </div>

            <form onSubmit={handleCreateJob}>
              <div style={{ marginBottom: "12px" }}>
                <label style={{ display: "block", marginBottom: "4px", fontWeight: "bold" }}>
                  작업 유형
                </label>
                <select
                  value={newTaskType}
                  onChange={(e) => setNewTaskType(e.target.value)}
                  style={{ width: "100%" }}
                >
                  {Array.from(taskTypeLabels.entries()).map(([key, label]) => (
                    <option key={key} value={key}>
                      {label} ({key})
                    </option>
                  ))}
                </select>
              </div>

              <div style={{ marginBottom: "12px" }}>
                <label style={{ display: "block", marginBottom: "4px", fontWeight: "bold" }}>
                  프롬프트 (입력 내용)
                </label>
                <textarea
                  rows={5}
                  value={newPrompt}
                  onChange={(e) => setNewPrompt(e.target.value)}
                  placeholder="AI 모델에 전달할 내용을 입력하세요…"
                  style={{ width: "100%", padding: "8px", borderRadius: "6px", border: "1px solid var(--line)" }}
                  required
                />
              </div>

              <div style={{ marginBottom: "16px" }}>
                <label style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                  <input
                    type="checkbox"
                    checked={newSync}
                    onChange={(e) => setNewSync(e.target.checked)}
                  />
                  동기식 즉시 실행 (결과를 기다려 바로 수령)
                </label>
              </div>

              <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px" }}>
                <button type="button" onClick={() => setShowCreate(false)}>
                  취소
                </button>
                <button type="submit" className="primary" disabled={submitting}>
                  {submitting ? "실행 중…" : "작업 실행"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
