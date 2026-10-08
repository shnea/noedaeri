import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { z } from "zod";
import { jobSchema, mutation, request, voiceSchema, errorLabel } from "./api";
import type { User, Voice } from "./api";

const speakers = [
  "Sohee",
  "Vivian",
  "Serena",
  "Uncle_Fu",
  "Dylan",
  "Eric",
  "Ryan",
  "Aiden",
  "Ono_Anna",
];

const languages = [
  "Korean",
  "English",
  "Japanese",
  "Chinese",
  "German",
  "French",
  "Russian",
  "Portuguese",
  "Spanish",
  "Italian",
];

const voiceStatus = new Map(
  Object.entries({
    ready: "사용 가능",
    uploading: "음성 전송 대기",
    queued: "검증 대기",
    running: "참조 음성 검증 중",
    failed: "등록 실패",
    cancelled: "등록 취소",
    interrupted: "종료 확인 중",
    cleanup_failed: "삭제 재시도 필요",
    pending: "등록 중",
  }),
);

export function VoicePanel({
  user,
  enabled,
  showJobs,
}: {
  user: User;
  enabled: boolean;
  showJobs: () => void;
}) {
  const [voices, setVoices] = useState<Voice[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [registration, setRegistration] = useState(false);

  async function refresh() {
    try {
      const response = await request("/api/voices");
      setVoices(z.array(voiceSchema).parse(await response.json()));
    } catch (failure) {
      setError(
        failure instanceof Error
          ? failure.message
          : "목소리 목록을 읽지 못했습니다.",
      );
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();

    const timer = setInterval(() => {
      void refresh();
    }, 5000);

    return () => clearInterval(timer);
  }, []);

  async function change(voice: Voice, action: "rename" | "delete" | "cancel") {
    setBusy(true);
    setError("");

    try {
      const response =
        action === "cancel"
          ? await request(
              `/api/jobs/${voice.registration_job_id}/cancel`,
              mutation(user.csrf),
            )
          : await request(`/api/voices/${voice.id}`, {
              ...mutation(
                user.csrf,
                action === "rename" ? JSON.stringify({ name }) : undefined,
              ),
              method: action === "rename" ? "PATCH" : "DELETE",
            });

      if (action === "delete" && !(await response.json()).deleted)
        throw new Error(
          "참조 음성을 삭제하지 못했습니다. 삭제를 다시 시도해 주세요.",
        );
      setEditing("");
      await refresh();
    } catch (failure) {
      setError(
        failure instanceof Error
          ? failure.message
          : "목소리를 변경하지 못했습니다.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="voice-panel">
      {!enabled && (
        <p role="status">
          TTS 설치와 연결 설정이 필요합니다. 등록된 목소리는 조회·관리할 수
          있습니다.
        </p>
      )}
      <section className="voice-directory">
        <div className="section-heading">
          <h2>등록한 목소리</h2>
          <div className="actions">
            <button
              onClick={() => setRegistration(!registration)}
              disabled={!enabled || busy}
            >
              {registration ? "등록 닫기" : "목소리 등록"}
            </button>
            <button onClick={showJobs}>전체 작업 보기</button>
          </div>
        </div>
        <p className="muted">
          목소리는 삭제할 때까지 유지됩니다. 생성한 웹 테스트 음성은 24시간
          보관합니다.
        </p>
        {registration && (
          <RegisterVoice
            user={user}
            done={() => {
              setRegistration(false);
              void refresh();
            }}
          />
        )}
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
        {loading ? (
          <p role="status">목소리 목록을 불러오는 중…</p>
        ) : voices.length === 0 ? (
          <p>
            등록된 목소리가 없습니다. 기본 목소리를 선택하거나 참조 음성을
            등록하세요.
          </p>
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>목소리</th>
                  <th>유형·범위</th>
                  <th>상태</th>
                  <th>관리</th>
                </tr>
              </thead>
              <tbody>
                {voices.map((voice) => (
                  <tr key={voice.id}>
                    <td>
                      {editing === voice.id ? (
                        <div className="actions">
                          <input
                            aria-label="목소리 이름 수정"
                            value={name}
                            maxLength={120}
                            onChange={(event) => setName(event.target.value)}
                          />
                          <button
                            disabled={busy || !name.trim()}
                            onClick={() => {
                              void change(voice, "rename");
                            }}
                          >
                            저장
                          </button>
                          <button onClick={() => setEditing("")}>취소</button>
                        </div>
                      ) : (
                        voice.name
                      )}
                    </td>
                    <td>
                      {voice.kind === "preset"
                        ? `기본 · ${voice.speaker}`
                        : "참조 음성"}
                      <small>
                        {voice.requester_id} · {voice.project} ·{" "}
                        {voice.environment}
                      </small>
                    </td>
                    <td>
                      <span>
                        {voiceStatus.get(voice.status) ?? voice.status}
                      </span>
                      {voice.error_code && (
                        <small className="error">
                          {errorLabel(voice.error_code)}
                        </small>
                      )}
                    </td>
                    <td>
                      <div className="actions">
                        <button
                          disabled={busy}
                          onClick={() => {
                            setEditing(voice.id);
                            setName(voice.name);
                          }}
                        >
                          이름 수정
                        </button>
                        {["uploading", "queued", "running"].includes(
                          voice.status,
                        ) ? (
                          <button
                            disabled={busy}
                            onClick={() => {
                              void change(voice, "cancel");
                            }}
                          >
                            등록 취소
                          </button>
                        ) : (
                          <button
                            className="danger"
                            disabled={busy || voice.status === "interrupted"}
                            onClick={() => {
                              void change(voice, "delete");
                            }}
                          >
                            삭제
                          </button>
                        )}
                      </div>
                      {voice.kind === "clone" &&
                        voice.status === "ready" &&
                        voice.sample_available && (
                          <audio
                            className="voice-sample"
                            aria-label={`${voice.name} 참조 음성`}
                            controls
                            preload="none"
                            src={`/api/voices/${voice.id}/sample`}
                          />
                        )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <SpeechForm
        user={user}
        voices={voices.filter(
          (voice) => voice.status === "ready" && voice.sample_available,
        )}
        enabled={enabled}
        done={showJobs}
      />
    </div>
  );
}

function RegisterVoice({ user, done }: { user: User; done: () => void }) {
  const [kind, setKind] = useState("preset");
  const [name, setName] = useState("");
  const [speaker, setSpeaker] = useState("Sohee");
  const [referenceText, setReferenceText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [key] = useState(crypto.randomUUID());
  const [locked, setLocked] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();

    if (kind === "clone" && (!file || file.size > 64 * 1024 * 1024)) {
      setError("3~30초 음성 파일을 선택해 주세요. 최대 64MiB입니다.");

      return;
    }

    setBusy(true);
    setLocked(true);
    setError("");

    try {
      const response = await request(
        "/api/voices",
        mutation(
          user.csrf,
          JSON.stringify({
            idempotency_key: key,
            name,
            kind,
            ...(kind === "preset"
              ? { speaker }
              : { reference_text: referenceText }),
          }),
        ),
      );

      const voice = voiceSchema.parse(await response.json());

      if (voice.registration_job_id && voice.status === "uploading") {
        await request(`/api/jobs/${voice.registration_job_id}/input`, {
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
          : "목소리를 등록하지 못했습니다.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="voice-register" onSubmit={submit}>
      <h3>목소리 등록</h3>
      <div className="form-fields">
        <label>
          목소리 이름
          <input
            required
            maxLength={120}
            disabled={locked}
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="예: 한국어 안내 목소리"
          />
        </label>
        <label>
          등록 방식
          <select
            aria-label="등록 방식"
            disabled={locked}
            value={kind}
            onChange={(event) => setKind(event.target.value)}
          >
            <option value="preset">기본 목소리 선택</option>
            <option value="clone">참조 음성으로 등록</option>
          </select>
        </label>
        {kind === "preset" ? (
          <label>
            기본 목소리
            <select
              aria-label="기본 목소리"
              disabled={locked}
              value={speaker}
              onChange={(event) => setSpeaker(event.target.value)}
            >
              {speakers.map((value) => (
                <option key={value}>{value}</option>
              ))}
            </select>
          </label>
        ) : (
          <>
            <label>
              참조 음성 파일
              <input
                type="file"
                required
                disabled={locked}
                accept=".wav,.mp3,.flac,.ogg,.m4a,.aac"
                onChange={(event) =>
                  setFile(event.target.files?.item(0) ?? null)
                }
              />
            </label>
            <label className="wide-field">
              참조 음성의 대본
              <textarea
                required
                rows={3}
                maxLength={1000}
                disabled={locked}
                value={referenceText}
                onChange={(event) => setReferenceText(event.target.value)}
                placeholder="파일에서 실제로 말한 문장을 그대로 입력하세요."
              />
            </label>
          </>
        )}
      </div>
      {kind === "clone" && (
        <p className="muted">
          3~30초·최대 64MiB. 한 사람의 목소리가 또렷한 음성을 사용하세요. 참조
          음성은 정규화 후 별도로 보관합니다.
        </p>
      )}
      {error && (
        <p className="error" role="alert">
          {error}{" "}
          {locked &&
            "같은 입력으로 다시 시도하거나 목록에서 등록을 취소해 주세요."}
        </p>
      )}
      <button className="primary" disabled={busy}>
        {busy
          ? "등록 요청 중…"
          : locked
            ? "같은 입력으로 재시도"
            : "목소리 저장"}
      </button>
    </form>
  );
}

function SpeechForm({
  user,
  voices,
  enabled,
  done,
}: {
  user: User;
  voices: Voice[];
  enabled: boolean;
  done: () => void;
}) {
  const [selected, setSelected] = useState("");
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [language, setLanguage] = useState("Korean");
  const [instruct, setInstruct] = useState("");
  const [key] = useState(crypto.randomUUID());
  const [busy, setBusy] = useState(false);
  const [locked, setLocked] = useState(false);
  const [error, setError] = useState("");
  const voice = voices.find((item) => item.id === selected);

  async function submit(event: FormEvent) {
    event.preventDefault();

    setBusy(true);
    setLocked(true);
    setError("");

    try {
      const response = await request(
        "/api/jobs",
        mutation(
          user.csrf,
          JSON.stringify({
            kind: "tts.synthesize",
            title: title.trim() || `${voice?.name ?? "기본 목소리"} 음성 생성`,
            idempotency_key: key,
            input: {
              type: "text",
              text,
              language,
              requester_id: voice?.requester_id ?? user.id,
              project: voice?.project ?? "default",
              environment: voice?.environment ?? "production",
            },
            options: {
              voice_id: voice?.id ?? null,
              instruct: voice?.kind === "clone" ? "" : instruct,
            },
          }),
        ),
      );

      jobSchema.parse(await response.json());
      done();
    } catch (failure) {
      setError(
        failure instanceof Error
          ? failure.message
          : "음성 생성 작업을 등록하지 못했습니다.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="speech-test">
      <h2>선택한 목소리로 말하기</h2>
      <p className="muted">
        생성 상태와 결과 재생·다운로드는 작업에서 확인합니다. 모델은 요청 시
        로딩하고 작업 종료 시 해제합니다.
      </p>
      <p className="muted">
        목소리를 선택하지 않으면 기본 목소리(Sohee)를 사용합니다. 목소리 등록 없이도
        바로 음성을 만들 수 있습니다.
      </p>
      <form onSubmit={submit}>
        <div className="form-fields">
          <label>
            사용할 목소리
            <select
              aria-label="사용할 목소리"
              disabled={locked}
              value={voice?.id ?? ""}
              onChange={(event) => setSelected(event.target.value)}
            >
              <option value="">기본 목소리 · Sohee</option>
              {voices.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name} · {item.requester_id} · {item.project} · {item.environment}
                </option>
              ))}
            </select>
          </label>
          <label>
            음성 언어
            <select
              aria-label="음성 언어"
              disabled={locked}
              value={language}
              onChange={(event) => setLanguage(event.target.value)}
            >
              {languages.map((value) => (
                <option key={value}>{value}</option>
              ))}
            </select>
          </label>
          <label>
            음성 작업명
            <input
              disabled={locked}
              maxLength={120}
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="예: 첫 안내 음성"
            />
          </label>
          {voice?.kind !== "clone" && (
            <label>
              말투 지시 (선택)
              <input
                disabled={locked}
                maxLength={300}
                value={instruct}
                onChange={(event) => setInstruct(event.target.value)}
                placeholder="예: Calm and friendly"
              />
            </label>
          )}
          <label className="wide-field">
            말할 내용
            <textarea
              aria-label="말할 내용"
              required
              rows={5}
              maxLength={4000}
              disabled={locked}
              value={text}
              onChange={(event) => setText(event.target.value)}
              placeholder="음성으로 만들 문장을 입력하세요."
            />
            <span className="muted">
              {text.length.toLocaleString()} / 4,000자
            </span>
          </label>
        </div>
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
        <button
          className="primary"
          disabled={busy || !enabled || !text.trim()}
        >
          {busy
            ? "작업 등록 중…"
            : locked
              ? "같은 요청으로 재시도"
              : "음성 생성 작업 등록"}
        </button>
      </form>
    </section>
  );
}
