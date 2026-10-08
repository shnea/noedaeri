import { useEffect, useState } from "react";
import { z } from "zod";
import { mutation, request } from "./api";

const snapshotSchema = z.object({
  concurrency: z.number(),
  requests: z.array(
    z.object({
      id: z.string(),
      kind: z.string(),
      state: z.string(),
      job_id: z.string(),
      parent_id: z.string().nullable(),
    }),
  ),
});

export function ComputePanel({
  csrf,
  admin,
}: {
  csrf: string;
  admin: boolean;
}) {
  const [snapshot, setSnapshot] = useState<z.infer<
    typeof snapshotSchema
  > | null>(null);

  const [error, setError] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);

  async function refresh() {
    try {
      const response = await request("/api/compute");
      setSnapshot(snapshotSchema.parse(await response.json()));
      setError("");
    } catch (failure) {
      setError(
        failure instanceof Error
          ? failure.message
          : "자원 상태를 불러오지 못했습니다.",
      );
    }
  }

  useEffect(() => {
    void refresh();

    const timer = window.setInterval(() => {
      void refresh();
    }, 5000);

    return () => window.clearInterval(timer);
  }, []);

  async function acknowledge(id: string) {
    setBusy(true);

    try {
      await request(
        `/api/admin/compute/${id}/acknowledge-stopped`,
        mutation(csrf, JSON.stringify({ execution_stopped: true })),
      );
      setConfirm("");
      await refresh();
    } catch (failure) {
      setError(
        failure instanceof Error
          ? failure.message
          : "자원 예약을 해제하지 못했습니다.",
      );
    } finally {
      setBusy(false);
    }
  }

  const blocked =
    snapshot?.requests.filter((item) => item.state === "interrupted") ?? [];

  const roots =
    snapshot?.requests.filter((item) => item.parent_id === null) ?? [];

  return (
    <section className="compute-panel" aria-label="공통 실행 자원">
      <h2>공통 실행 자원</h2>
      {!admin && (
        <p className="muted">
          내 작업의 자원 예약 상태입니다. 실행 슬롯은 모든 서비스가 공유합니다.
        </p>
      )}
      {snapshot && (
        <p>
          동시 실행 {snapshot.concurrency}개 · 실행{" "}
          {roots.filter((item) => item.state === "running").length}개 · 자원
          대기 {roots.filter((item) => item.state === "queued").length}개
        </p>
      )}
      {error && (
        <p className="error" role="alert">
          {error}{" "}
          <button
            onClick={() => {
              void refresh();
            }}
          >
            다시 확인
          </button>
        </p>
      )}
      {blocked.length > 0 && (
        <>
          <p role="status">
            이전 실행의 종료 여부를 확인해야 합니다. 새 연산은 자원을 배정받을
            때까지 기다립니다.
          </p>
          {blocked.map((item) => (
            <div key={item.id}>
              <p>
                {item.kind} · 작업 <code>{item.job_id}</code>
              </p>
              {admin &&
                (confirm === item.id ? (
                  <>
                    <p>
                      로컬 실행 프로세스와 n8n·외부 모델의 실행이 끝났는지
                      확인한 뒤 해제하세요. 해제해도 이전 작업을 다시 실행하지
                      않습니다.
                    </p>
                    <button
                      className="danger"
                      disabled={busy}
                      onClick={() => {
                        void acknowledge(item.id);
                      }}
                    >
                      종료 확인 후 예약 해제
                    </button>
                    <button disabled={busy} onClick={() => setConfirm("")}>
                      돌아가기
                    </button>
                  </>
                ) : (
                  <button onClick={() => setConfirm(item.id)}>
                    실행 종료 확인
                  </button>
                ))}
            </div>
          ))}
        </>
      )}
      {!snapshot && !error && (
        <p role="status">자원 배정 상태를 확인하고 있습니다.</p>
      )}
    </section>
  );
}
