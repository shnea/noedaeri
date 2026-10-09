import { Fragment, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { z } from "zod";
import { IntegrationGuide } from "./IntegrationGuide";
import { RayaPanel } from "./RayaPanel";
import { VideoResult } from "./VideoResult";
import { AiUsagePanel } from "./AiUsagePanel";
import { TaskDetails } from "./TaskDetails";
import { EmbeddingRagPanel } from "./EmbeddingRagPanel";
import { VoicePanel } from "./VoicePanel";
import { ComputePanel } from "./ComputePanel";
import { PdfResult } from "./PdfResult";
import { OcrResult } from "./OcrResult";
import { TranscriptResult } from "./TranscriptResult";
import {
  jobSchema,
  aiJobSchema,
  taskSchema,
  memberSchema,
  mutation,
  request,
  serviceSchema,
  userSchema,
  workerSchema,
  errorLabel,
} from "./api";
import type { Job, Member, Service, Task, User, Worker } from "./api";

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

function Details({
  job,
  cancel,
  retry,
  redeliver,
}: {
  job: Job;
  cancel: () => void;
  retry: () => void;
  redeliver: () => void;
}) {
  const imagePackage = z
    .object({
      type: z.literal("image_package"),
      source: z.object({
        format: z.string(),
        width: z.number(),
        height: z.number(),
      }),
    })
    .safeParse(job.result);

  const videoPackage = z
    .object({
      type: z.literal("video_package"),
      duration_seconds: z.number().optional(),
      total_bytes: z.number().optional(),
      video_encoder: z.enum(["libx264", "h264_videotoolbox"]).optional(),
      hardware_fallback: z.boolean().optional(),
      variants: z.array(z.object({ label: z.string(), playlist: z.string() })),
    })
    .safeParse(job.result);

  const pdfProgress = /^pdf_pages_(\d+)_of_(\d+)$/.exec(job.stage);

  const stageLabel = pdfProgress
    ? `PDF ${pdfProgress[1]} / ${pdfProgress[2]}페이지 처리 완료`
    : job.stage.startsWith("encoding_")
      ? `${job.stage.slice(9)} 영상 변환 중`
      : new Map([
          ["thumbnail", "썸네일 생성 중"],
          ["probing", "영상 정보 분석 중"],
          ["reserving", "결과 저장 공간 예약 중"],
          [
            "cpu_fallback",
            "하드웨어 가속을 사용할 수 없어 CPU로 전환 중입니다.",
          ],
          [
            "waiting_hardware_encoder",
            "다른 작업이 하드웨어 인코더를 사용 중입니다. 종료 후 시작합니다.",
          ],
          ["image_processing", "이미지 검사·썸네일·미리보기 생성 중"],
          ["voice_reference_validation", "참조 음성 검증·정규화 중"],
          ["tts_loading_and_synthesis", "목소리 모델 로딩·음성 생성 중"],
          ["pdf_reading", "PDF 열기·페이지 확인 중"],
          ["ocr_normalizing", "인식용 이미지 준비 중"],
          ["ocr_recognizing", "이미지 문자 인식 중"],
          ["subtitle_exporting", "SRT·VTT 자막 생성 중"],
          ["stt_probing", "음성 정보·길이 확인 중"],
          ["stt_normalizing", "음성 인식용 입력 변환 중"],
          ["stt_transcribing", "음성 모델 로딩·텍스트 인식 중"],
          [
            "waiting_native_compute",
            "다른 연산이 자원을 사용 중입니다. 종료 확인 후 시작합니다.",
          ],
          ["waiting_compute", "공통 실행 자원 배정을 기다리고 있습니다."],
          ["packaging", "결과 묶음 생성 중"],
        ]).get(job.stage);

  const available =
    job.result_state === "available" &&
    job.expires_at !== null &&
    Date.parse(job.expires_at) > Date.now();

  const artifact = z
    .object({
      type: z.literal("artifact"),
      media_type: z.string(),
      voice_source: z.enum(["default", "registered"]).optional(),
    })
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
              : (["running", "queued"].includes(job.status) && stageLabel) ||
                statuses.get(job.status)}
        </p>
        <div className="detail-actions">
          <span className="muted">
            {job.finished_at ? `종료 ${date(job.finished_at)}` : job.kind}
          </span>
          {["uploading", "queued", "running"].includes(job.status) && (
            <button
              className="danger"
              disabled={job.cancel_requested}
              onClick={cancel}
            >
              작업 취소
            </button>
          )}
        </div>
        {Boolean(job.output_reserved) && (
          <p>
            결과 공간 예약 {((job.output_reserved ?? 0) / 1048576).toFixed(1)}{" "}
            MiB
          </p>
        )}
        {job.origin === "platform" && (
          <section className="platform-delivery" aria-label="플랫폼 전달 상태">
            <h3>플랫폼 연동</h3>
            <p>
              {job.delivery
                ? ({
                    pending: "완료 알림 전송 대기",
                    delivered: "완료 알림 전달됨",
                    failed: "완료 알림 전송 실패",
                    acknowledged: "플랫폼 저장 확인됨",
                  }[job.delivery.state] ?? job.delivery.state)
                : "작업 종료 후 완료 알림을 보냅니다."}
            </p>
            {job.delivery && (
              <p className="muted">
                전송 시도 {job.delivery.attempts}회
                {job.delivery.last_http_status
                  ? ` · HTTP ${job.delivery.last_http_status}`
                  : ""}
              </p>
            )}
            {job.delivery?.state === "pending" && (
              <p className="muted">
                다음 시도 {date(job.delivery.next_attempt_at)}
              </p>
            )}
            {job.delivery?.state === "failed" && (
              <button onClick={redeliver}>완료 알림 다시 전송</button>
            )}
            {job.status === "succeeded" && (
              <p>
                {job.received_at
                  ? `저장 확인 ${date(job.received_at)} · 결과 정리 대상`
                  : "결과는 플랫폼 저장 확인 또는 보관 기한까지 유지합니다."}
              </p>
            )}
          </section>
        )}
        {job.origin !== "platform" &&
          ["failed", "cancelled"].includes(job.status) && (
            <button onClick={retry}>
              {job.service === "tts"
                ? "목소리·입력 확인 후 재시도"
                : "입력 다시 올려 재시도"}
            </button>
          )}
        {job.retry_of && (
          <p className="muted">이전 작업 {job.retry_of.slice(0, 8)}의 재시도</p>
        )}
        <p className="mobile-timestamp">
          요청 {date(job.created_at)}
          <br />
          요청자{" "}
          {job.origin === "platform" ? "플랫폼" : job.owner_id.slice(0, 8)} ·
          워커 {job.worker_id?.slice(0, 8) ?? "배정 대기"}
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
            {videoPackage.success &&
              videoPackage.data.video_encoder !== undefined && (
                <p>
                  {videoPackage.data.video_encoder === "h264_videotoolbox"
                    ? "하드웨어 가속으로 변환한 영상"
                    : videoPackage.data.hardware_fallback
                      ? "하드웨어 가속을 사용할 수 없어 CPU로 변환한 영상"
                      : "CPU로 변환한 영상"}
                </p>
              )}
            {videoPackage.success &&
              videoPackage.data.duration_seconds !== undefined && (
                <p>
                  영상 {videoPackage.data.duration_seconds.toFixed(1)}초 · 전체{" "}
                  {((videoPackage.data.total_bytes ?? 0) / 1048576).toFixed(1)}{" "}
                  MiB
                </p>
              )}
            {imagePackage.success && (
              <>
                <a
                  className="result-preview"
                  href={`/api/jobs/${job.id}/files/preview.webp`}
                >
                  <img
                    src={`/api/jobs/${job.id}/files/preview.webp`}
                    alt="생성된 이미지 미리보기"
                  />
                </a>
                <p>
                  {imagePackage.data.source.format} ·{" "}
                  {imagePackage.data.source.width} ×{" "}
                  {imagePackage.data.source.height}
                </p>
                <div className="guide-actions">
                  <a
                    className="button"
                    href={`/api/jobs/${job.id}/files/thumbnail.jpg`}
                    download
                  >
                    JPEG 썸네일
                  </a>
                  <a
                    className="button"
                    href={`/api/jobs/${job.id}/files/preview.webp`}
                    download
                  >
                    WebP 미리보기
                  </a>
                </div>
              </>
            )}
            {imageResult && (
              <a className="result-preview" href={`/api/jobs/${job.id}/result`}>
                <img
                  src={`/api/jobs/${job.id}/result`}
                  alt="생성된 작업 결과 이미지"
                />
              </a>
            )}
            {job.service === "tts" && (
              <>
                {artifact.success &&
                  artifact.data.voice_source === "default" && (
                    <p>기본 목소리 · Sohee</p>
                  )}
                <audio
                  aria-label="생성된 음성"
                  controls
                  preload="metadata"
                  className="speech-result"
                  src={`/api/jobs/${job.id}/result`}
                />
              </>
            )}
            {job.kind === "pdf.extract" && <PdfResult jobId={job.id} />}
            {job.kind === "ocr.recognize" && <OcrResult jobId={job.id} />}
            {["stt.transcribe", "video.subtitles"].includes(job.kind) && (
              <TranscriptResult
                jobId={job.id}
                subtitles={job.kind === "video.subtitles"}
              />
            )}
            {!artifact.success &&
              !videoPackage.success &&
              !imagePackage.success &&
              job.kind !== "stt.transcribe" &&
              job.kind !== "video.subtitles" &&
              job.kind !== "ocr.recognize" &&
              job.kind !== "pdf.extract" &&
              job.result !== null &&
              job.result !== undefined && (
                <pre className="json-result">
                  {JSON.stringify(job.result, null, 2)}
                </pre>
              )}
            <a className="button" href={`/api/jobs/${job.id}/result`} download>
              {imagePackage.success ||
              videoPackage.success ||
              ["stt.transcribe", "video.subtitles"].includes(job.kind) ||
              job.kind === "ocr.recognize" ||
              job.kind === "pdf.extract"
                ? "전체 ZIP 다운로드"
                : "결과 다운로드"}
            </a>
            <p>삭제 예정 {date(job.expires_at)}</p>
          </>
        ) : (
          <p className="result-placeholder">
            {job.received_at
              ? "플랫폼이 결과 저장을 확인했습니다. 뇌대리 결과는 정리 대상입니다."
              : job.origin === "platform" &&
                  ["failed", "cancelled"].includes(job.status)
                ? "결과가 없습니다. 재실행은 플랫폼에서 새 작업으로 요청해 주세요."
                : job.result_state === "expired" ||
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
  retryJob,
  initialKind,
}: {
  services: Service[];
  user: User;
  done: () => void;
  retryJob?: Job | null;
  initialKind: string;
}) {
  const titleInput = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (retryJob) titleInput.current?.focus();
  }, [retryJob]);

  const [kind, setKind] = useState(
    retryJob?.kind ??
      (initialKind ||
        services.find((item) => item.interface === "media")?.kind ||
        ""),
  );

  const [title, setTitle] = useState(retryJob?.title ?? "");
  const [taskType, setTaskType] = useState("chat.general");
  const [prompt, setPrompt] = useState("");
  const [sourceLanguage, setSourceLanguage] = useState("auto");
  const [targetLanguage, setTargetLanguage] = useState("en");
  const [file, setFile] = useState<File | null>(null);
  const [seconds, setSeconds] = useState(retryJob?.options?.seconds ?? 0);

  const [language, setLanguage] = useState(
    retryJob?.options?.language ?? "auto",
  );

  const [correction, setCorrection] = useState(
    retryJob?.options?.language_correction ?? true,
  );

  const [pdfMode, setPdfMode] = useState<string>(
    retryJob?.options?.mode ?? "auto",
  );

  const [useItn, setUseItn] = useState(retryJob?.options?.use_itn ?? true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [key] = useState(crypto.randomUUID());
  const [submitted, setSubmitted] = useState(false);
  const service = services.find((item) => item.kind === kind);

  const imageLimit =
    service?.pdf_limits?.max_input_bytes ??
    service?.ocr_limits?.max_input_bytes ??
    service?.image_limits?.max_input_bytes ??
    200_000_000;

  const imageLimitMb = new Intl.NumberFormat("ko-KR", {
    maximumFractionDigits: 2,
  }).format(imageLimit / 1_000_000);

  async function submit(event: FormEvent) {
    event.preventDefault();

    if (service?.interface === "translation") {
      setBusy(true);
      setError("");

      try {
        const response = await request(
          "/api/translations",
          mutation(
            user.csrf,
            JSON.stringify({
              request_id: key,
              text: prompt.trim(),
              source_language: sourceLanguage,
              target_language: targetLanguage,
              project: "web-test",
              environment: "production",
            }),
          ),
        );

        aiJobSchema.parse(await response.json());
        done();
      } catch (failure) {
        setError(
          failure instanceof Error
            ? failure.message
            : "번역 작업을 등록하지 못했습니다.",
        );
      } finally {
        setBusy(false);
      }

      return;
    }

    if (service?.interface === "ai") {
      setBusy(true);
      setError("");

      try {
        const response = await request(
          "/api/ai/jobs",
          mutation(
            user.csrf,
            JSON.stringify({
              request_id: key,
              task_type: taskType,
              prompt: prompt.trim(),
              project: "web-test",
              environment: "production",
              sync: false,
            }),
          ),
        );

        aiJobSchema.parse(await response.json());
        done();
      } catch (failure) {
        setError(
          failure instanceof Error
            ? failure.message
            : "AI 작업을 등록하지 못했습니다.",
        );
      } finally {
        setBusy(false);
      }

      return;
    }

    if (!file || !service) {
      setError("작업 종류와 입력을 선택해 주세요.");

      return;
    }

    if (
      ["ocr.recognize", "image.package", "pdf.extract"].includes(kind) &&
      file.size > imageLimit
    ) {
      setError(
        `${kind === "pdf.extract" ? "PDF 파일은" : "입력 이미지는"} 최대 ${imageLimitMb}MB입니다. 크기를 줄인 뒤 다시 선택해 주세요.`,
      );

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
            retry_of: retryJob?.id ?? null,
            input: ["image.package", "ocr.recognize"].includes(kind)
              ? {
                  type: service.input_type,
                  extension: file.name.split(".").pop()?.toLowerCase(),
                }
              : { type: service.input_type },
            options:
              kind === "image.package"
                ? {}
                : kind === "pdf.extract"
                  ? { mode: pdfMode, language, language_correction: correction }
                  : kind === "ocr.recognize"
                    ? { language, language_correction: correction }
                    : ["stt.transcribe", "video.subtitles"].includes(kind)
                      ? { language, use_itn: useItn }
                      : { seconds },
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
      {retryJob && (
        <p>
          기존 작업을 덮어쓰지 않습니다. 원본을 다시 선택하면 새로운 작업으로
          실행합니다.
        </p>
      )}
      <div className="form-fields">
        <label>
          작업 종류
          <select
            aria-label="작업 종류"
            disabled={submitted || Boolean(retryJob)}
            value={kind}
            onChange={(event) => {
              setKind(event.target.value);
              setFile(null);
              setLanguage("auto");
            }}
          >
            {services
              .filter((item) =>
                ["media", "ai", "translation"].includes(item.interface),
              )
              .map((item) => (
                <option
                  key={item.kind}
                  value={item.kind}
                  disabled={!item.available}
                >
                  {item.service} · {item.label}
                </option>
              ))}
          </select>
        </label>
        {service?.interface === "media" && (
          <label>
            작업명
            <input
              disabled={submitted}
              required
              maxLength={120}
              ref={titleInput}
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder={
                kind === "pdf.extract"
                  ? "예: 스캔 문서 텍스트 추출"
                  : ["stt.transcribe", "video.subtitles"].includes(kind)
                    ? "예: 회의 음성 인식"
                    : "예: 소개 영상 미리보기 생성"
              }
            />
          </label>
        )}
        {service?.interface === "translation" && (
          <>
            <label>
              원문 언어
              <select
                aria-label="원문 언어"
                value={sourceLanguage}
                disabled={busy}
                onChange={(event) => setSourceLanguage(event.target.value)}
              >
                <option value="auto">자동 감지</option>
                {[
                  ["ko", "한국어"],
                  ["en", "영어"],
                  ["ja", "일본어"],
                  ["zh", "중국어"],
                  ["es", "스페인어"],
                  ["fr", "프랑스어"],
                  ["de", "독일어"],
                ].map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              번역 언어
              <select
                aria-label="번역 언어"
                value={targetLanguage}
                disabled={busy}
                onChange={(event) => setTargetLanguage(event.target.value)}
              >
                {[
                  ["ko", "한국어"],
                  ["en", "영어"],
                  ["ja", "일본어"],
                  ["zh", "중국어"],
                  ["es", "스페인어"],
                  ["fr", "프랑스어"],
                  ["de", "독일어"],
                ].map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <label className="wide-field">
              번역할 문장
              <textarea
                aria-label="번역할 문장"
                required
                rows={6}
                maxLength={4000}
                value={prompt}
                disabled={busy}
                onChange={(event) => setPrompt(event.target.value)}
                placeholder="번역할 원문을 입력하세요."
              />
            </label>
          </>
        )}
        {service?.interface === "ai" && (
          <>
            <label>
              AI 작업 유형
              <select
                aria-label="AI 작업 유형"
                value={taskType}
                disabled={busy}
                onChange={(event) => setTaskType(event.target.value)}
              >
                {service.task_types.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="wide-field">
              입력 내용
              <textarea
                required
                rows={5}
                maxLength={200000}
                disabled={busy}
                value={prompt}
                onChange={(event) => setPrompt(event.target.value)}
                placeholder="처리할 내용을 입력하세요."
              />
            </label>
          </>
        )}
        {service?.input_type === "upload" && (
          <label>
            {kind === "pdf.extract"
              ? "입력 PDF"
              : ["image.package", "ocr.recognize"].includes(kind)
                ? "입력 이미지"
                : ["stt.transcribe", "video.subtitles"].includes(kind)
                  ? "입력 음성·영상"
                  : "입력 영상"}
            <input
              key={kind}
              disabled={submitted}
              type="file"
              required
              accept={
                kind === "pdf.extract"
                  ? ".pdf"
                  : ["image.package", "ocr.recognize"].includes(kind)
                    ? ".png,.jpg,.jpeg,.jfif,.gif,.webp,.bmp,.ico,.tif,.tiff,.heic,.heif,.avif"
                    : ["stt.transcribe", "video.subtitles"].includes(kind)
                      ? ".wav,.mp3,.flac,.ogg,.m4a,.mp4,.mov,.webm,.mkv,.aac,.aiff"
                      : "video/mp4,video/quicktime,video/webm,video/x-matroska"
              }
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
        {["ocr.recognize", "pdf.extract"].includes(kind) && (
          <>
            <label>
              인식 언어
              <select
                aria-label="인식 언어"
                disabled={submitted}
                value={language}
                onChange={(event) => setLanguage(event.target.value)}
              >
                {[
                  ["auto", "자동 감지 · 한글·영문 우선"],
                  ["ko", "한국어"],
                  ["en", "영어"],
                  ["ja", "일본어"],
                  ["zh-Hans", "중국어 간체"],
                  ["zh-Hant", "중국어 번체"],
                ].map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              언어 보정
              <select
                aria-label="언어 보정"
                disabled={submitted}
                value={correction ? "on" : "off"}
                onChange={(event) => setCorrection(event.target.value === "on")}
              >
                <option value="on">언어 보정 사용</option>
                <option value="off">언어 보정 끄기</option>
              </select>
            </label>
          </>
        )}
        {kind === "pdf.extract" && (
          <label>
            추출 방식
            <select
              aria-label="추출 방식"
              disabled={submitted}
              value={pdfMode}
              onChange={(event) => setPdfMode(event.target.value)}
            >
              <option value="auto">자동 · 텍스트 없으면 OCR</option>
              <option value="ocr">모든 페이지 OCR</option>
              <option value="text">내장 텍스트만 추출</option>
            </select>
          </label>
        )}
        {["stt.transcribe", "video.subtitles"].includes(kind) && (
          <>
            <label>
              인식 언어
              <select
                aria-label="인식 언어"
                disabled={submitted}
                value={language}
                onChange={(event) => setLanguage(event.target.value)}
              >
                {[
                  ["auto", "자동 감지"],
                  ["ko", "한국어"],
                  ["en", "영어"],
                  ["ja", "일본어"],
                  ["zh", "중국어"],
                  ["yue", "광둥어"],
                ].map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              숫자·문장 표기
              <select
                aria-label="숫자·문장 표기"
                disabled={submitted}
                value={useItn ? "on" : "off"}
                onChange={(event) => setUseItn(event.target.value === "on")}
              >
                <option value="on">표기 정규화 사용</option>
                <option value="off">표기 정규화 끄기</option>
              </select>
            </label>
          </>
        )}
      </div>
      {kind === "pdf.extract" && (
        <p>
          PDF 최대 {imageLimitMb}MB · 최대{" "}
          {service?.pdf_limits?.max_pages ?? 100}페이지 · 처리 제한{" "}
          {(service?.pdf_limits?.timeout_seconds ?? 900) / 60}분. 암호화 PDF는
          지원하지 않습니다. 자동 모드는 페이지에 내장 텍스트가 있으면 추출하고
          없으면 OCR로 인식합니다. 표·서식은 복원하지 않으며 원본은 작업 종료 후
          정리됩니다.
        </p>
      )}
      {kind === "image.package" && (
        <p>
          이미지 최대 {imageLimitMb}MB·4천만 화소. 첫 프레임을 사용하고 원본은
          결과에 포함하지 않습니다.
        </p>
      )}
      {kind === "ocr.recognize" && (
        <p>
          이미지 최대 {imageLimitMb}MB·4천만 화소·가로세로 각 10,000px. 방향을
          보정한 첫 프레임을 최대 4,096px로 축소해 인식합니다.
          {service?.ocr_limits &&
            ` 처리 제한 ${service.ocr_limits.timeout_seconds}초.`}{" "}
          PDF·표 구조 복원은 지원하지 않습니다. 입력은 작업 종료 후 정리됩니다.
        </p>
      )}
      {["stt.transcribe", "video.subtitles"].includes(kind) && (
        <p>
          음성 또는 음성이 포함된 영상의 첫 오디오 트랙을 인식합니다.
          {service?.limits &&
            ` 최대 ${service.limits.max_duration_seconds / 60}분 · 처리 제한 ${service.limits.timeout_seconds / 60}분 · CPU ${service.limits.cpu_threads}스레드.`}
          결과는 텍스트와 구간별 시각이며, 입력은 작업 종료 후 정리됩니다.
          {kind === "video.subtitles" &&
            " SRT·VTT 자막도 생성합니다. 자막 시각은 음성 구간을 나눈 근사값이며, 단어별 정렬·화자 구분·영상에 자막 입히기는 제공하지 않습니다."}
        </p>
      )}
      {service?.interface === "translation" && (
        <p>
          최대 4,000자. 기존 n8n 모델 라우팅을 사용하며 외부 AI 공급자에게
          원문을 전송합니다. 의미·고유명사·숫자를 원문과 대조해 주세요.
        </p>
      )}
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
      <button className="primary" disabled={busy || !service?.available}>
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
  const [jobs, setJobs] = useState<Task[]>([]);
  const [services, setServices] = useState<Service[]>([]);
  const [workers, setWorkers] = useState<Worker[]>([]);
  const [members, setMembers] = useState<Member[]>([]);
  const [tab, setTab] = useState("작업");
  const [expanded, setExpanded] = useState("");
  const [filter, setFilter] = useState("all");
  const [serviceFilter, setServiceFilter] = useState("all");
  const [offset, setOffset] = useState(0);
  const [newKind, setNewKind] = useState("");

  const [ragView, setRagView] = useState<
    "indexing" | "search" | "embedding" | "raya"
  >("indexing");

  const [creating, setCreating] = useState(false);
  const [retryJob, setRetryJob] = useState<Job | null>(null);
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

      const parameters = new URLSearchParams({
        limit: "100",
        offset: String(offset),
      });

      if (filter !== "all") parameters.set("status", filter);

      if (serviceFilter !== "all") parameters.set("service", serviceFilter);

      const responses = await Promise.all([
        request(`/api/tasks?${parameters}`),
        request("/api/services"),
      ]);

      setJobs(z.array(taskSchema).parse(await responses[0].json()));
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
  }, [filter, serviceFilter, offset]);

  async function cancel(job: Task) {
    if (!user) return;

    try {
      const prefix =
        job.source === "media"
          ? "/api/jobs"
          : job.source === "ai"
            ? "/api/ai/jobs"
            : "/api/ai/indexing";

      await request(`${prefix}/${job.id}/cancel`, mutation(user.csrf));
      await refresh();
    } catch (failure) {
      setError(
        failure instanceof Error
          ? failure.message
          : "취소 요청에 실패했습니다.",
      );
    }
  }

  async function redeliver(job: { id: string }, source = "media") {
    if (!user) return;

    try {
      await request(
        `${source === "ai" ? "/api/admin/ai/jobs" : "/api/admin/jobs"}/${job.id}/webhook-retry`,
        mutation(user.csrf),
      );
      await refresh();
    } catch (failure) {
      setError(
        failure instanceof Error
          ? failure.message
          : "완료 알림 재전송에 실패했습니다.",
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

  const visible = jobs;

  const tabs =
    user?.role === "admin"
      ? [
          "작업",
          "서비스",
          "TTS·목소리",
          "사용량",
          "임베딩·RAG",
          "Raya",
          "연동 지침",
          "워커",
          "사용자",
        ]
      : ["작업", "서비스", "TTS·목소리", "사용량", "임베딩·RAG", "연동 지침"];

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
                      : tab === "TTS·목소리"
                        ? "목소리를 등록·관리하고 선택한 목소리로 음성을 만듭니다."
                        : tab === "사용량"
                          ? "공급자와 모델별 호출 수·토큰 사용량을 확인합니다."
                          : tab === "임베딩·RAG"
                            ? "문서 색인과 검색을 테스트하고 컬렉션을 관리합니다."
                            : tab === "Raya"
                              ? "요청 난이도 판단과 모델 실행 정책을 확인합니다."
                              : tab === "연동 지침"
                                ? "기능별 호출 방법과 현재 연결 가능한 범위를 확인합니다."
                                : tab === "워커"
                                  ? "워커의 마지막 연결 상태를 확인합니다."
                                  : "플랫폼으로 로그인한 사용자의 접근을 관리합니다."}
                </p>
              </div>
              {tab === "작업" && (
                <button
                  className="primary"
                  onClick={() => {
                    setRetryJob(null);
                    setCreating(true);
                  }}
                >
                  새 작업
                </button>
              )}
            </div>
            {tab === "사용량" && <AiUsagePanel key={user.id} />}
            {tab === "TTS·목소리" && (
              <VoicePanel
                key={user.id}
                user={user}
                enabled={services.some(
                  (item) => item.service === "tts" && item.available,
                )}
                showJobs={() => {
                  setTab("작업");
                  setCreating(false);
                  setServiceFilter("all");
                  setFilter("all");
                  setOffset(0);
                  void refresh();
                }}
              />
            )}
            {tab === "임베딩·RAG" && (
              <EmbeddingRagPanel
                key={`${user.id}:${ragView}`}
                csrf={user.csrf}
                initialTab={ragView}
                isAdmin={user.role === "admin"}
              />
            )}
            {tab === "연동 지침" && <IntegrationGuide />}
            {tab === "Raya" && user.role === "admin" && (
              <RayaPanel key={user.id} csrf={user.csrf} />
            )}
            {creating && tab === "작업" && (
              <NewTask
                key={retryJob?.id ?? "new"}
                retryJob={retryJob}
                initialKind={newKind}
                services={services}
                user={user}
                done={() => {
                  setCreating(false);
                  setRetryJob(null);
                  void refresh();
                }}
              />
            )}
            {tab === "작업" && (
              <>
                <ComputePanel csrf={user.csrf} admin={user.role === "admin"} />
                <div className="filters">
                  <label>
                    서비스
                    <select
                      aria-label="서비스"
                      value={serviceFilter}
                      onChange={(event) => {
                        setServiceFilter(event.target.value);
                        setOffset(0);
                      }}
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
                      aria-label="상태"
                      value={filter}
                      onChange={(event) => {
                        setFilter(event.target.value);
                        setOffset(0);
                      }}
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
                          <th scope="col">실행 담당</th>
                          <th scope="col">요청 시각</th>
                        </tr>
                      </thead>
                      <tbody>
                        {visible.map((job) => (
                          <Fragment key={`${job.source}:${job.id}`}>
                            <tr
                              className={
                                expanded === `${job.source}:${job.id}`
                                  ? "selected"
                                  : ""
                              }
                            >
                              <td>
                                <button
                                  className="job-title"
                                  aria-expanded={
                                    expanded === `${job.source}:${job.id}`
                                  }
                                  onClick={() =>
                                    setExpanded(
                                      expanded === `${job.source}:${job.id}`
                                        ? ""
                                        : `${job.source}:${job.id}`,
                                    )
                                  }
                                >
                                  <svg
                                    width="16"
                                    height="16"
                                    viewBox="0 0 16 16"
                                    aria-hidden="true"
                                    className={
                                      expanded === `${job.source}:${job.id}`
                                        ? "rotated"
                                        : ""
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
                                <small>{job.label}</small>
                              </td>
                              <td>
                                <Status value={job.status} />
                              </td>
                              <td>
                                {job.origin === "platform"
                                  ? "플랫폼"
                                  : job.owner_id === user.id
                                    ? "나"
                                    : job.owner_id.slice(0, 8)}
                              </td>
                              <td>{job.executor}</td>
                              <td>{date(job.created_at)}</td>
                            </tr>
                            {expanded === `${job.source}:${job.id}` && (
                              <tr className="detail-row">
                                <td colSpan={6}>
                                  {job.source === "media" ? (
                                    <Details
                                      redeliver={() => {
                                        void redeliver(job.data);
                                      }}
                                      retry={() => {
                                        if (job.service === "tts") {
                                          setTab("TTS·목소리");
                                          setCreating(false);
                                        } else {
                                          setRetryJob(job.data);
                                          setCreating(true);
                                        }

                                        window.scrollTo({ top: 0 });
                                      }}
                                      job={{ ...job.data, status: job.status }}
                                      cancel={() => {
                                        void cancel(job);
                                      }}
                                    />
                                  ) : (
                                    <TaskDetails
                                      task={job}
                                      currentUserId={user.id}
                                      isAdmin={user.role === "admin"}
                                      retryDelivery={() =>
                                        void redeliver(job, "ai")
                                      }
                                      cancel={() => void cancel(job)}
                                    />
                                  )}
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
                      {offset > 0
                        ? "더 표시할 작업이 없습니다."
                        : filter !== "all" || serviceFilter !== "all"
                          ? "조건에 맞는 작업이 없습니다."
                          : "아직 요청한 작업이 없습니다."}
                    </h2>
                    <p>
                      {offset > 0
                        ? "앞선 작업 목록으로 돌아가세요."
                        : filter !== "all" || serviceFilter !== "all"
                          ? "필터를 변경해 다른 작업을 확인하세요."
                          : "새 작업에서 서비스를 선택하고 첫 연산을 요청하세요."}
                    </p>
                    {offset > 0 ? (
                      <button
                        onClick={() => setOffset(Math.max(0, offset - 100))}
                      >
                        이전 목록으로
                      </button>
                    ) : filter !== "all" || serviceFilter !== "all" ? (
                      <button
                        onClick={() => {
                          setFilter("all");
                          setServiceFilter("all");
                        }}
                      >
                        필터 초기화
                      </button>
                    ) : (
                      <button
                        className="primary"
                        onClick={() => {
                          setRetryJob(null);
                          setCreating(true);
                        }}
                      >
                        첫 작업 만들기
                      </button>
                    )}
                  </section>
                )}
                <footer>
                  <span>
                    {visible.length
                      ? `${offset + 1}번째부터 ${visible.length}개 표시`
                      : "표시할 작업 없음"}
                  </span>
                  <div className="actions">
                    <button
                      disabled={offset === 0}
                      onClick={() => setOffset(Math.max(0, offset - 100))}
                    >
                      이전 작업
                    </button>
                    <button
                      disabled={jobs.length < 100}
                      onClick={() => setOffset(offset + 100)}
                    >
                      다음 작업
                    </button>
                  </div>
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
                        ? "파일 업로드"
                        : service.input_type === "text"
                          ? "텍스트"
                          : "문서·검색 조건"}
                    </p>
                    <p>
                      {service.available ? "연결 설정됨" : "연결 설정 필요"}
                    </p>
                    {service.unavailable_reason && (
                      <p>{service.unavailable_reason}</p>
                    )}
                    <div className="actions">
                      <button
                        disabled={
                          !service.available ||
                          (service.interface === "raya" &&
                            user.role !== "admin")
                        }
                        onClick={() => {
                          if (
                            ["media", "ai", "translation"].includes(
                              service.interface,
                            )
                          ) {
                            setRetryJob(null);
                            setNewKind(service.kind);
                            setTab("작업");
                            setCreating(true);
                          } else if (service.interface === "tts") {
                            setCreating(false);
                            setTab("TTS·목소리");
                          } else if (service.interface === "raya") {
                            setTab("Raya");
                          } else {
                            const view =
                              service.interface === "embedding"
                                ? "embedding"
                                : service.interface === "search"
                                  ? "search"
                                  : "indexing";

                            setRagView(view);
                            setTab("임베딩·RAG");
                          }
                        }}
                      >
                        {" "}
                        {service.interface === "raya"
                          ? "실행 정책·테스트"
                          : ["media", "ai", "translation"].includes(
                                service.interface,
                              )
                            ? "작업 만들기"
                            : "관리·테스트"}
                      </button>
                      <button
                        onClick={() => {
                          setServiceFilter(service.service);
                          setOffset(0);
                          setTab("작업");
                          setCreating(false);
                        }}
                      >
                        작업 보기
                      </button>
                    </div>
                    {service.interface === "raya" && user.role !== "admin" && (
                      <p>관리자에게 실행 정책을 확인해 주세요.</p>
                    )}
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
