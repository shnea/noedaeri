import { useEffect, useState } from "react";
import { aiUsageResponseSchema, request } from "./api";
import type { AiUsageResponse } from "./api";

export function AiUsagePanel() {
  const [usage, setUsage] = useState<AiUsageResponse | null>(null);
  const [error, setError] = useState("");

  async function refresh() {
    try {
      const response = await request("/api/ai/usage?limit=50");
      setUsage(aiUsageResponseSchema.parse(await response.json()));
      setError("");
    } catch (failure) {
      setError(
        failure instanceof Error
          ? failure.message
          : "사용량을 불러오지 못했습니다.",
      );
    }
  }

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 5000);

    return () => window.clearInterval(timer);
  }, []);

  return (
    <section className="usage-panel">
      <div className="section-heading">
        <h2>모델별 토큰 사용량</h2>
        <button onClick={() => void refresh()}>새로고침</button>
      </div>
      <p>
        작업 상태와 결과는 작업 메뉴에서 확인합니다. 이 화면은 모델·공급자별
        사용량을 보여줍니다.
      </p>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {!usage ? (
        <p role="status">사용량을 불러오는 중…</p>
      ) : (
        <>
          <p>
            총 호출{" "}
            {usage.summary
              .reduce((sum, row) => sum + row.call_count, 0)
              .toLocaleString()}
            회 · 총 토큰{" "}
            {usage.summary
              .reduce((sum, row) => sum + row.total_tokens, 0)
              .toLocaleString()}
          </p>
          {usage.summary.length ? (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th scope="col">공급자</th>
                    <th scope="col">모델</th>
                    <th scope="col">작업 유형</th>
                    <th scope="col">호출 수</th>
                    <th scope="col">입력 토큰</th>
                    <th scope="col">출력 토큰</th>
                    <th scope="col">전체 토큰</th>
                  </tr>
                </thead>
                <tbody>
                  {usage.summary.map((row) => (
                    <tr key={`${row.provider}:${row.model}:${row.task_type}`}>
                      <td>{row.provider}</td>
                      <td>{row.model}</td>
                      <td>{row.task_type}</td>
                      <td>{row.call_count.toLocaleString()}</td>
                      <td>{row.total_prompt_tokens.toLocaleString()}</td>
                      <td>{row.total_completion_tokens.toLocaleString()}</td>
                      <td>{row.total_tokens.toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p>아직 기록된 사용량이 없습니다.</p>
          )}
          <h3>최근 호출 내역</h3>
          {usage.records.length ? (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th scope="col">요청 ID</th>
                    <th scope="col">프로젝트 · 환경</th>
                    <th scope="col">공급자 · 모델</th>
                    <th scope="col">전체 토큰</th>
                    <th scope="col">시각</th>
                  </tr>
                </thead>
                <tbody>
                  {usage.records.map((row) => (
                    <tr key={row.id}>
                      <td>{row.request_id}</td>
                      <td>
                        {row.project} · {row.environment}
                      </td>
                      <td>
                        {row.provider} · {row.model}
                      </td>
                      <td>{row.total_tokens.toLocaleString()}</td>
                      <td>
                        {new Date(row.created_at).toLocaleString("ko-KR")}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p>아직 기록된 호출이 없습니다.</p>
          )}
        </>
      )}
    </section>
  );
}
