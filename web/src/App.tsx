import { Fragment, useEffect, useState } from "react";
import type { FormEvent } from "react";
import { z } from "zod";
import { VideoResult } from "./VideoResult";
import {
  jobSchema,
  memberSchema,
  mutation,
  request,
  serviceSchema,
  userSchema,
  workerSchema,
  errorLabel,
} from "./api";
import type { Job, Member, Service, User, Worker } from "./api";

const statuses = new Map(
  Object.entries({
    uploading: "입력 대기",
    queued: "대기",
    running: "실행 중",
    succeeded: "완료",
    failed: "실패",
    cancelled: "취소됨",
    interrupted: "실행 확인 필요",
    pending: "승인 대기",
    approved: "승인됨",
    rejected: "거부됨",
    revoked: "접근 철회",
  }),
);

function date(value: string | null) {
  return value
    ? new Date(value).toLocaleString("ko-KR", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "—";
}

function Status({ value }: { value: string }) {
  return (
    <span className={`status ${value}`}>{statuses.get(value) ?? value}</span>
  );
}

function Details({ job, cancel }: { job: Job; cancel: () => void }) {
  const videoPackage = z
    .object({
      type: z.literal("video_package"),
      variants: z.array(z.object({ label: z.string(), playlist: z.string() })),
    })
    .safeParse(job.result);

  const stageLabel = job.stage.startsWith("encoding_")
    ? `${job.stage.slice(9)} 영상 변환 중`
    : new Map([
        ["thumbnail", "썸네일 생성 중"],
        ["packaging", "결과 묶음 생성 중"],
      ]).get(job.stage);

  const available =
    job.result_state === "available" &&
    job.expires_at !== null &&
    Date.parse(job.expires_at) > Date.now();

  const artifact = z
    .object({ type: z.literal("artifact"), media_type: z.string() })
    .safeParse(job.result);

  const imageResult =
    artifact.success &&
    ["image/jpeg", "image/png", "image/webp"].includes(
      artifact.data.media_type,
    );

  const terminalMessage = new Map([
    [
      "failed",
      "작업이 실패하여 결과가 없습니다. 입력을 확인하고 새 작업으로 요청해 주세요.",
    ],
    ["cancelled", "작업이 취소되어 결과가 없습니다."],
    [
      "interrupted",
      "워커의 실행 상태를 확인해야 합니다. 현재 결과를 제공할 수 없습니다.",
    ],
    ["succeeded", "작업은 종료됐지만 제공할 결과가 없습니다."],
    ["uploading", "입력을 받은 뒤 작업을 시작합니다."],
  ]);

  return (
    <div className="detail">
      <section>
        <h3>실행 상태</h3>
        <ol className="steps" aria-label="작업 단계">
          <li
            className={
              job.status === "queued"
                ? "current"
                : job.status === "uploading"
                  ? ""
                  : "done"
            }
          >
            대기
          </li>
          <li
            className={
              job.status === "running"
                ? "current"
                : job.finished_at && job.worker_id
                  ? "done"
                  : ""
            }
          >
            실행
          </li>
          <li
            className={
              job.status === "succeeded"
                ? "done"
                : job.finished_at
                  ? "terminal"
                  : ""
            }
          >
            종료
          </li>
        </ol>
        <p>
          {job.cancel_requested && !job.finished_at
            ? "취소 요청을 전달했습니다. 워커가 종료를 확인할 때까지 기다려 주세요."
            : job.error_code
              ? errorLabel(job.error_code)
              : (job.status === "running" && stageLabel) ||
                statuses.get(job.status)}
        </p>
        <div className="detail-actions">
          <span className="muted">
            {job.finished_at ? `종료 ${date(job.finished_at)}` : job.kind}
          </span>
          {["queued", "running"].includes(job.status) && (
            <button
              className="danger"
              disabled={job.cancel_requested}
              onClick={cancel}
            >
              작업 취소
            </button>
          )}
        </div>
        <p className="mobile-timestamp">
          요청 {date(job.created_at)}
          <br />
          요청자 {job.owner_id.slice(0, 8)} · 워커{" "}
          {job.worker_id?.slice(0, 8) ?? "배정 대기"}
        </p>
      </section>
      <section>
        <h3>결과</h3>
        {available ? (
          <>
            {videoPackage.success && (
              <VideoResult
                jobId={job.id}
                variants={videoPackage.data.variants}
              />
            )}
            {imageResult && (
              <a className="result-preview" href={`/api/jobs/${job.id}/result`}>
                <img
                  src={`/api/jobs/${job.id}/result`}
                  alt="생성된 작업 결과 이미지"
                />
              </a>
            )}
            {!artifact.success &&
              !videoPackage.success &&
              job.result !== null &&
              job.result !== undefined && (
                <pre className="json-result">
                  {JSON.stringify(job.result, null, 2)}
                </pre>
              )}
            <a className="button" href={`/api/jobs/${job.id}/result`} download>
              결과 다운로드
            </a>
            <p>삭제 예정 {date(job.expires_at)}</p>
          </>
        ) : (
          <p className="result-placeholder">
            {job.result_state === "expired" ||
            job.result_state === "cleanup_failed"
              ? "보관 기간이 만료되었습니다."
              : (terminalMessage.get(job.status) ??
                "작업이 완료되면 결과를 확인할 수 있습니다.")}
          </p>
        )}
        {job.cleanup_state === "failed" && (
          <p role="status">임시 파일 정리를 재시도하고 있습니다.</p>
        )}
      </section>
    </div>
  );
}

function NewTask({
  services,
  user,
  done,
}: {
  services: Service[];
  user: User;
  done: () => void;
}) {
  const [kind, setKind] = useState(services[0]?.kind ?? "");
  const [title, setTitle] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [seconds, setSeconds] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [key] = useState(crypto.randomUUID());
  const [submitted, setSubmitted] = useState(false);
  const service = services.find((item) => item.kind === kind);

  async function submit(event: FormEvent) {
    event.preventDefault();

    if (!file || !service) {
      setError("작업 종류와 입력을 선택해 주세요.");

      return;
    }

    setSubmitted(true);
    setBusy(true);
    setError("");

    try {
      const response = await request(
        "/api/jobs",
        mutation(
          user.csrf,
          JSON.stringify({
            kind,
            title,
            idempotency_key: key,
            input: { type: service.input_type },
            options: { seconds },
          }),
        ),
      );

      const job = jobSchema.parse(await response.json());

      if (job.status === "uploading") {
        await request(`/api/jobs/${job.id}/input`, {
          method: "PUT",
          headers: {
            "Content-Type": "application/octet-stream",
            "X-CSRF-Token": user.csrf,
          },
          body: file,
        });
      }

      done();
    } catch (failure) {
      setError(
        failure instanceof Error
          ? failure.message
          : "작업을 등록하지 못했습니다.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="new-task" onSubmit={submit}>
      <div className="section-heading">
        <h2>새 작업</h2>
        <button type="button" disabled={busy} onClick={done}>
          닫기
        </button>
      </div>
      <div className="form-fields">
        <label>
          작업 종류
          <select
            disabled={submitted}
            value={kind}
            onChange={(event) => setKind(event.target.value)}
          >
            {services.map((item) => (
              <option key={item.kind} value={item.kind}>
                {item.service} · {item.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          작업명
          <input
            disabled={submitted}
            required
            maxLength={120}
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            placeholder="예: 소개 영상 미리보기 생성"
          />
        </label>
        {service?.input_type === "upload" && (
          <label>
            입력 영상
            <input
              disabled={submitted}
              type="file"
              required
              accept="video/mp4,video/quicktime,video/webm,video/x-matroska"
              onChange={(event) => setFile(event.target.files?.item(0) ?? null)}
            />
          </label>
        )}
        {kind === "video.thumbnail" && (
          <label>
            추출 시점 (초)
            <input
              disabled={submitted}
              type="number"
              min="0"
              max="3600"
              step="0.1"
              value={seconds}
              onChange={(event) => setSeconds(Number(event.target.value))}
            />
          </label>
        )}
      </div>
      <p>웹 테스트 결과는 생성 완료 후 24시간 보관됩니다.</p>
      {submitted && (
        <p>
          재시도는 같은 입력으로 진행합니다. 내용을 바꾸려면 닫은 뒤 새 작업을
          만들어 주세요.
        </p>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <button className="primary" disabled={busy || !service}>
        {busy
          ? "입력 전송 중…"
          : submitted
            ? "같은 입력으로 다시 시도"
            : "작업 시작"}
      </button>
    </form>
  );
}

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [checking, setChecking] = useState(true);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [services, setServices] = useState<Service[]>([]);
  const [workers, setWorkers] = useState<Worker[]>([]);
  const [members, setMembers] = useState<Member[]>([]);
  const [tab, setTab] = useState("작업");
  const [expanded, setExpanded] = useState("");
  const [filter, setFilter] = useState("all");
  const [serviceFilter, setServiceFilter] = useState("all");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");
  const [refreshed, setRefreshed] = useState<string | null>(null);

  async function refresh() {
    try {
      const me = await fetch("/api/me");

      if (me.status === 401) {
        setUser(null);
        setJobs([]);

        return;
      }

      if (!me.ok) throw new Error("사용자 상태를 확인하지 못했습니다.");

      const currentUser = userSchema.parse(await me.json());
      setUser(currentUser);

      if (currentUser.status !== "approved") {
        setJobs([]);
        setMembers([]);
        setWorkers([]);

        return;
      }

      const responses = await Promise.all([
        request("/api/jobs"),
        request("/api/services"),
      ]);

      setJobs(z.array(jobSchema).parse(await responses[0].json()));
      setServices(z.array(serviceSchema).parse(await responses[1].json()));

      if (currentUser.role === "admin") {
        const adminResponses = await Promise.all([
          request("/api/admin/users"),
          request("/api/admin/workers"),
        ]);

        setMembers(z.array(memberSchema).parse(await adminResponses[0].json()));
        setWorkers(z.array(workerSchema).parse(await adminResponses[1].json()));
      }

      setRefreshed(new Date().toISOString());
      setError("");
    } catch (failure) {
      setError(
        failure instanceof Error
          ? failure.message
          : "상태를 불러오지 못했습니다.",
      );
    } finally {
      setChecking(false);
    }
  }

  useEffect(() => {
    void refresh();

    const timer = window.setInterval(() => {
      void refresh();
    }, 5000);

    return () => window.clearInterval(timer);
  }, []);

  async function cancel(job: Job) {
    if (!user) return;

    try {
      await request(`/api/jobs/${job.id}/cancel`, mutation(user.csrf));
      await refresh();
    } catch (failure) {
      setError(
        failure instanceof Error
          ? failure.message
          : "취소 요청에 실패했습니다.",
      );
    }
  }

  async function approve(member: Member, status: string) {
    if (!user) return;

    try {
      await request(`/api/admin/users/${member.id}`, {
        ...mutation(user.csrf, JSON.stringify({ status })),
        method: "PATCH",
      });
      await refresh();
    } catch (failure) {
      setError(
        failure instanceof Error
          ? failure.message
          : "승인 상태를 변경하지 못했습니다.",
      );
    }
  }

  async function logout() {
    if (!user) return;

    try {
      const response = await request("/auth/logout", mutation(user.csrf));

      const result = z
        .object({ logout_url: z.string().nullable() })
        .parse(await response.json());

      setUser(null);
      setJobs([]);

      if (result.logout_url) {
        window.location.assign(result.logout_url);
      } else {
        setError(
          "뇌대리에서 로그아웃했습니다. 플랫폼 로그아웃 연결에 실패해 플랫폼 로그인은 유지될 수 있습니다.",
        );
      }
    } catch {
      setError("로그아웃하지 못했습니다. 다시 시도해 주세요.");
    }
  }

  const visible = jobs.filter(
    (job) =>
      (filter === "all" || job.status === filter) &&
      (serviceFilter === "all" || job.service === serviceFilter),
  );

  const tabs =
    user?.role === "admin"
      ? ["작업", "서비스", "워커", "사용자"]
      : ["작업", "서비스"];

  return (
    <>
      <a className="skip" href="#main">
        본문으로 이동
      </a>
      <header>
        <a className="brand" href="/">
          뇌대리
        </a>
        {user?.status === "approved" && (
          <nav aria-label="주 메뉴">
            {tabs.map((item) => (
              <button
                key={item}
                className={tab === item ? "active" : ""}
                onClick={() => setTab(item)}
              >
                {item}
              </button>
            ))}
          </nav>
        )}
        <div className="account">
          {user ? (
            <>
              <span>{user.role === "admin" ? "관리자" : "사용자"}</span>
              <button
                onClick={() => {
                  void logout();
                }}
              >
                로그아웃
              </button>
            </>
          ) : (
            <span>연산 작업 관리</span>
          )}
        </div>
      </header>
      <main id="main">
        {error && (
          <div className="error-banner" role="alert">
            {error}
            <button
              onClick={() => {
                void refresh();
              }}
            >
              다시 연결
            </button>
          </div>
        )}
        {checking ? (
          <div className="empty" role="status">
            접근 권한을 확인하고 있습니다…
          </div>
        ) : !user ? (
          <section className="access">
            <h1>
              작업을 맡기고,
              <br />
              진행을 확인하세요.
            </h1>
            <p>
              플랫폼 계정으로 로그인하면 접근 승인 상태를 확인할 수 있습니다.
            </p>
            <a className="button primary" href="/auth/login">
              플랫폼으로 로그인
            </a>
            <p className="muted">사용하려면 관리자의 승인이 필요합니다.</p>
          </section>
        ) : user.status !== "approved" ? (
          <section className="access">
            <Status value={user.status} />
            <h1>
              {user.status === "pending"
                ? "관리자 승인을 기다리고 있습니다."
                : "현재 접근이 허용되지 않았습니다."}
            </h1>
            <p>
              승인 상태는 자동으로 갱신됩니다. 작업과 운영 정보는 승인 후 확인할
              수 있습니다.
            </p>
            <button
              onClick={() => {
                void refresh();
              }}
            >
              승인 상태 확인
            </button>
          </section>
        ) : (
          <>
            <div className="toolbar">
              <div>
                <h1>{tab}</h1>
                <p>
                  {tab === "작업"
                    ? "요청한 작업의 대기, 실행, 결과를 한곳에서 확인합니다."
                    : tab === "서비스"
                      ? "현재 실행할 수 있는 작업 종류입니다."
                      : tab === "워커"
                        ? "워커의 마지막 연결 상태를 확인합니다."
                        : "플랫폼으로 로그인한 사용자의 접근을 관리합니다."}
                </p>
              </div>
              {tab === "작업" && (
                <button className="primary" onClick={() => setCreating(true)}>
                  새 작업
                </button>
              )}
            </div>
            {creating && (
              <NewTask
                services={services}
                user={user}
                done={() => {
                  setCreating(false);
                  void refresh();
                }}
              />
            )}
            {tab === "작업" && (
              <>
                <div className="filters">
                  <label>
                    서비스
                    <select
                      value={serviceFilter}
                      onChange={(event) => setServiceFilter(event.target.value)}
                    >
                      <option value="all">모든 서비스</option>
                      {[...new Set(services.map((item) => item.service))].map(
                        (item) => (
                          <option key={item} value={item}>
                            {item}
                          </option>
                        ),
                      )}
                    </select>
                  </label>
                  <label>
                    상태
                    <select
                      value={filter}
                      onChange={(event) => setFilter(event.target.value)}
                    >
                      <option value="all">모든 상태</option>
                      {[
                        "uploading",
                        "queued",
                        "running",
                        "succeeded",
                        "failed",
                        "cancelled",
                        "interrupted",
                      ].map((item) => (
                        <option key={item} value={item}>
                          {statuses.get(item)}
                        </option>
                      ))}
                    </select>
                  </label>
                  <button
                    onClick={() => {
                      void refresh();
                    }}
                  >
                    새로고침
                  </button>
                </div>
                {visible.length ? (
                  <div className="table-scroll">
                    <table className="jobs-table">
                      <thead>
                        <tr>
                          <th scope="col">작업명</th>
                          <th scope="col">서비스</th>
                          <th scope="col">상태</th>
                          <th scope="col">요청자</th>
                          <th scope="col">워커</th>
                          <th scope="col">요청 시각</th>
                        </tr>
                      </thead>
                      <tbody>
                        {visible.map((job) => (
                          <Fragment key={job.id}>
                            <tr
                              className={expanded === job.id ? "selected" : ""}
                            >
                              <td>
                                <button
                                  className="job-title"
                                  aria-expanded={expanded === job.id}
                                  onClick={() =>
                                    setExpanded(
                                      expanded === job.id ? "" : job.id,
                                    )
                                  }
                                >
                                  <svg
                                    width="16"
                                    height="16"
                                    viewBox="0 0 16 16"
                                    aria-hidden="true"
                                    className={
                                      expanded === job.id ? "rotated" : ""
                                    }
                                  >
                                    <path
                                      d="m6 3 5 5-5 5"
                                      fill="none"
                                      stroke="currentColor"
                                      strokeWidth="1.5"
                                    />
                                  </svg>
                                  {job.title}
                                </button>
                              </td>
                              <td>
                                {job.service}
                                <small>
                                  {services.find(
                                    (item) => item.kind === job.kind,
                                  )?.label ?? job.kind}
                                </small>
                              </td>
                              <td>
                                <Status value={job.status} />
                              </td>
                              <td>
                                {job.owner_id === user.id
                                  ? "나"
                                  : job.owner_id.slice(0, 8)}
                              </td>
                              <td>
                                {job.worker_id
                                  ? job.worker_id.slice(0, 8)
                                  : "배정 대기"}
                              </td>
                              <td>{date(job.created_at)}</td>
                            </tr>
                            {expanded === job.id && (
                              <tr className="detail-row">
                                <td colSpan={6}>
                                  <Details
                                    job={job}
                                    cancel={() => {
                                      void cancel(job);
                                    }}
                                  />
                                </td>
                              </tr>
                            )}
                          </Fragment>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <section className="empty">
                    <h2>
                      {jobs.length
                        ? "조건에 맞는 작업이 없습니다."
                        : "아직 요청한 작업이 없습니다."}
                    </h2>
                    <p>
                      {jobs.length
                        ? "필터를 변경해 다른 작업을 확인하세요."
                        : "새 작업에서 서비스를 선택하고 첫 연산을 요청하세요."}
                    </p>
                    {!jobs.length && (
                      <button
                        className="primary"
                        onClick={() => setCreating(true)}
                      >
                        첫 작업 만들기
                      </button>
                    )}
                  </section>
                )}
                <footer>
                  <span>{visible.length}개 표시 · 최근 최대 100개 조회</span>
                  <span>
                    {refreshed
                      ? `최근 갱신 ${date(refreshed)}`
                      : "연결 확인 중"}
                  </span>
                </footer>
              </>
            )}
            {tab === "서비스" && (
              <div className="service-list">
                {services.map((service) => (
                  <section key={service.kind}>
                    <h2>{service.label}</h2>
                    <p>
                      {service.service} · {service.kind}
                    </p>
                    <p>
                      입력:{" "}
                      {service.input_type === "upload"
                        ? "영상 업로드"
                        : service.input_type}
                    </p>
                    <button
                      onClick={() => {
                        setTab("작업");
                        setCreating(true);
                      }}
                    >
                      작업 만들기
                    </button>
                  </section>
                ))}
                <p className="muted">
                  다른 연산 서비스는 구현 후 이 목록에 추가됩니다.
                </p>
              </div>
            )}
            {tab === "워커" && (
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>워커</th>
                      <th>연결 상태</th>
                      <th>마지막 응답</th>
                    </tr>
                  </thead>
                  <tbody>
                    {workers.map((item) => (
                      <tr key={item.id}>
                        <td>{item.id.slice(0, 8)}</td>
                        <td>{item.online ? "연결됨" : "응답 없음"}</td>
                        <td>{date(item.last_seen)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {!workers.length && (
                  <p className="empty">연결된 워커가 없습니다.</p>
                )}
              </div>
            )}
            {tab === "사용자" && (
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>사용자 식별자</th>
                      <th>상태</th>
                      <th>역할</th>
                      <th>접근 관리</th>
                    </tr>
                  </thead>
                  <tbody>
                    {members.map((member) => (
                      <tr key={member.id}>
                        <td>{member.subject}</td>
                        <td>
                          <Status value={member.status} />
                        </td>
                        <td>{member.role === "admin" ? "관리자" : "사용자"}</td>
                        <td>
                          {member.role !== "admin" && (
                            <div className="actions">
                              <button
                                onClick={() => {
                                  void approve(member, "approved");
                                }}
                                disabled={member.status === "approved"}
                              >
                                승인
                              </button>
                              <button
                                onClick={() => {
                                  void approve(member, "rejected");
                                }}
                                disabled={member.status !== "pending"}
                              >
                                거부
                              </button>
                              <button
                                className="danger"
                                onClick={() => {
                                  void approve(member, "revoked");
                                }}
                                disabled={member.status !== "approved"}
                              >
                                접근 철회
                              </button>
                            </div>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </main>
    </>
  );
}
