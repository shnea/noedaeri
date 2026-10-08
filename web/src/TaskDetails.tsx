import type { Task } from "./api";

export function TaskDetails({
  task,
  currentUserId,
  cancel,
}: {
  task: Exclude<Task, { source: "media" }>;
  currentUserId: string;
  cancel: () => void;
}) {
  const data = task.data;

  const cancellable =
    (task.source === "ai" && ["queued", "running"].includes(task.status)) ||
    (task.source === "indexing" && task.status === "queued");

  const expired = Boolean(
    data.expires_at && Date.parse(data.expires_at) <= Date.now(),
  );

  return (
    <div className="detail">
      <section>
        <h3>실행 정보</h3>
        <p>
          {task.label} · {task.executor}
        </p>
        {task.compute?.state === "queued" && (
          <p role="status">공통 실행 자원 배정을 기다리고 있습니다.</p>
        )}
        {task.compute?.parent_id && (
          <p>n8n 부모 작업의 실행 권한을 이어받은 단계입니다.</p>
        )}
        {task.compute?.state === "interrupted" && (
          <p className="error">
            실행 종료 여부가 불확실하여 자원 예약을 유지합니다.
          </p>
        )}
        <p>
          요청자{" "}
          {task.origin === "platform"
            ? "플랫폼"
            : task.owner_id === currentUserId
              ? "나"
              : task.owner_id.slice(0, 8)}
        </p>
        <p>
          작업 ID <code>{task.id}</code>
        </p>
        {task.source !== "operation" && (
          <>
            <p>요청 ID {task.data.request_id}</p>
            <p>
              프로젝트 {task.data.project} · 환경 {task.data.environment}
            </p>
          </>
        )}
        {task.source === "indexing" && (
          <>
            <p>컬렉션 {task.data.collection}</p>
            <p>
              요청 문서 {task.data.document_count}건 · 색인{" "}
              {task.data.indexed_count}건 · 삭제 {task.data.deleted_count}건
            </p>
            <p>사용 토큰 {task.data.total_tokens.toLocaleString()}</p>
          </>
        )}
        {data.error_code && (
          <p className="error">
            오류 코드: {data.error_code}
            {task.source !== "operation" && task.data.error_message
              ? ` · ${task.data.error_message}`
              : ""}
          </p>
        )}
        <p>요청 {new Date(task.created_at).toLocaleString("ko-KR")}</p>
        {data.finished_at && (
          <p>종료 {new Date(data.finished_at).toLocaleString("ko-KR")}</p>
        )}
        {cancellable && (
          <button className="danger" onClick={cancel}>
            작업 취소
          </button>
        )}
        {task.source === "ai" && task.status === "running" && (
          <p className="muted">취소 표시와 n8n 내부 실행 중단은 별개입니다.</p>
        )}
        {task.source === "indexing" && task.status === "running" && (
          <p className="muted">반영 중인 색인 작업은 완료까지 기다려 주세요.</p>
        )}
      </section>
      <section>
        <h3>{task.source === "operation" ? "실행 요약" : "결과"}</h3>
        {expired ? (
          <p>결과 보관 기간이 만료되었습니다.</p>
        ) : data.result !== null && data.result !== undefined ? (
          <pre className="json-result">
            {JSON.stringify(data.result, null, 2)}
          </pre>
        ) : (
          <p>
            {task.status === "failed"
              ? "실패한 작업입니다. 원인을 확인한 뒤 새 작업으로 요청하세요."
              : task.status === "cancelled"
                ? "취소된 작업입니다."
                : task.status === "interrupted"
                  ? "실행 종료 여부를 확인해야 합니다."
                  : "아직 제공할 결과가 없습니다."}
          </p>
        )}
        {data.expires_at && !expired && (
          <p>
            결과 보관 기한 {new Date(data.expires_at).toLocaleString("ko-KR")}
          </p>
        )}
        {task.source === "operation" && (
          <p className="muted">
            직접 호출의 실행 이력입니다. 임베딩 벡터와 검색 본문은 호출 응답으로
            반환하며 이력에는 요약만 보관합니다.
          </p>
        )}
      </section>
    </div>
  );
}
