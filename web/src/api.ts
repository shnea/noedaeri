import { z } from "zod";

export const userSchema = z.object({
  id: z.string(),
  status: z.string(),
  role: z.string(),
  csrf: z.string(),
});

export const jobSchema = z.object({
  origin: z.string().optional(),
  received_at: z.string().nullable().optional(),
  delivery: z
    .object({
      state: z.string(),
      attempts: z.number(),
      last_http_status: z.number().nullable(),
      next_attempt_at: z.string(),
    })
    .nullable()
    .optional(),
  retry_of: z.string().nullable().optional(),
  output_reserved: z.number().optional(),
  options: z.object({ seconds: z.number().optional() }).optional(),
  id: z.string(),
  title: z.string(),
  kind: z.string(),
  service: z.string(),
  status: z.string(),
  stage: z.string(),
  owner_id: z.string(),
  worker_id: z.string().nullable(),
  created_at: z.string(),
  updated_at: z.string(),
  finished_at: z.string().nullable(),
  expires_at: z.string().nullable(),
  cancel_requested: z.boolean(),
  error_code: z.string().nullable(),
  cleanup_state: z.string(),
  result_state: z.string(),
  result: z.json().nullable().optional(),
});

export const serviceSchema = z.object({
  kind: z.string(),
  service: z.string(),
  label: z.string(),
  input_type: z.string(),
});

export const memberSchema = z.object({
  id: z.string(),
  subject: z.string(),
  status: z.string(),
  role: z.string(),
});

export const workerSchema = z.object({
  id: z.string(),
  last_seen: z.string(),
  online: z.boolean(),
});

export type User = z.infer<typeof userSchema>;

export type Job = z.infer<typeof jobSchema>;

export type Service = z.infer<typeof serviceSchema>;

export type Member = z.infer<typeof memberSchema>;

export type Worker = z.infer<typeof workerSchema>;

const messages = new Map(
  Object.entries({
    approval_required: "관리자 승인이 필요합니다.",
    login_required: "로그인이 만료됐습니다. 다시 로그인해 주세요.",
    storage_capacity_exceeded:
      "임시 저장 공간이 부족합니다. 결과 정리 후 다시 시도해 주세요.",
    upload_too_large: "업로드 한도를 초과했습니다.",
    unsupported_media: "지원하지 않는 영상입니다.",
    invalid_job_options: "작업 옵션을 확인해 주세요.",
    invalid_media_or_conversion_failed: "영상을 읽거나 변환하지 못했습니다.",
    processing_timeout: "작업 제한시간을 초과했습니다.",
    lease_lost:
      "실행이 중단되었습니다. 종료 확인 후 실패 상태에서 다시 요청할 수 있습니다.",
    retry_not_safe:
      "기존 실행의 종료를 확인하지 못했습니다. 잠시 후 상태를 확인해 주세요.",
    delivery_not_failed:
      "재전송 가능한 실패 알림이 없습니다. 상태를 새로고침해 주세요.",
    job_not_cancellable: "이미 종료되었거나 취소할 수 없는 작업입니다.",
  }),
);

export function errorLabel(code: string) {
  return (
    messages.get(code) ??
    "요청을 처리하지 못했습니다. 상태를 새로고침한 뒤 다시 시도해 주세요."
  );
}

export async function request(path: string, init?: RequestInit) {
  const response = await fetch(path, init);

  if (!response.ok) {
    const body = z
      .object({ detail: z.string() })
      .safeParse(await response.json());

    throw new Error(
      body.success
        ? errorLabel(body.data.detail)
        : "서버 응답을 확인할 수 없습니다.",
    );
  }

  return response;
}

export function mutation(csrf: string, body?: string): RequestInit {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
    body,
  };
}
