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
  unavailable_reason: z.string().nullable().optional(),
  kind: z.string(),
  service: z.string(),
  label: z.string(),
  input_type: z.string(),
  interface: z
    .enum(["media", "ai", "raya", "embedding", "indexing", "search", "tts"])
    .default("media"),
  available: z.boolean().default(true),
  task_types: z
    .array(z.object({ value: z.string(), label: z.string() }))
    .default([]),
});

export const voiceSchema = z.object({
  id: z.string(),
  owner_id: z.string(),
  name: z.string(),
  kind: z.enum(["preset", "clone"]),
  speaker: z.string().nullable(),
  requester_id: z.string(),
  project: z.string(),
  environment: z.string(),
  reference_text: z.string(),
  status: z.string(),
  registration_job_id: z.string().nullable(),
  sample_bytes: z.number(),
  sample_available: z.boolean().default(true),
  error_code: z.string().nullable(),
  created_at: z.string(),
});

export type Voice = z.infer<typeof voiceSchema>;

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

export const aiJobSchema = z.object({
  id: z.string(),
  request_id: z.string(),
  task_type: z.string(),
  project: z.string(),
  environment: z.string(),
  status: z.string(),
  result: z.json().nullable().optional(),
  error_code: z.string().nullable().optional(),
  error_message: z.string().nullable().optional(),
  created_at: z.string(),
  updated_at: z.string().nullable().optional(),
  finished_at: z.string().nullable().optional(),
  expires_at: z.string().nullable().optional(),
});

export type AiJobResult = z.infer<typeof aiJobSchema>["result"];

export const aiUsageSummaryItemSchema = z.object({
  provider: z.string(),
  model: z.string(),
  task_type: z.string(),
  call_count: z.number(),
  total_prompt_tokens: z.number(),
  total_completion_tokens: z.number(),
  total_tokens: z.number(),
});

export const aiUsageRecordSchema = z.object({
  id: z.string(),
  job_id: z.string().nullable().optional(),
  project: z.string(),
  environment: z.string(),
  request_id: z.string(),
  task_type: z.string(),
  provider: z.string(),
  model: z.string(),
  prompt_tokens: z.number(),
  completion_tokens: z.number(),
  total_tokens: z.number(),
  model_tier: z.string().nullable().optional(),
  created_at: z.string(),
});

export const aiUsageResponseSchema = z.object({
  summary: z.array(aiUsageSummaryItemSchema),
  records: z.array(aiUsageRecordSchema),
});

export const embeddingResponseSchema = z.object({
  object: z.string().optional(),
  model: z.string(),
  data: z.array(
    z.object({
      index: z.number(),
      embedding: z.array(z.number()),
    }),
  ),
  usage: z.object({
    prompt_tokens: z.number(),
    total_tokens: z.number(),
  }),
});

export const indexingJobSchema = z.object({
  id: z.string(),
  owner_id: z.string(),
  result_state: z.string(),
  reused: z.boolean().optional(),
  request_id: z.string(),
  project: z.string(),
  environment: z.string(),
  collection: z.string(),
  mode: z.string(),
  status: z.string(),
  document_count: z.number(),
  indexed_count: z.number(),
  deleted_count: z.number(),
  total_tokens: z.number(),
  result: z.json().nullable().optional(),
  error_code: z.string().nullable().optional(),
  error_message: z.string().nullable().optional(),
  created_at: z.string(),
  updated_at: z.string().nullable().optional(),
  finished_at: z.string().nullable().optional(),
  expires_at: z.string().nullable().optional(),
});

export const collectionOverviewSchema = z.object({
  owner_id: z.string(),
  collection: z.string(),
  project: z.string(),
  environment: z.string(),
  document_count: z.number(),
  total_tokens: z.number(),
  last_updated_at: z.string().nullable().optional(),
});

export const vectorSearchResultItemSchema = z.object({
  document_id: z.string(),
  title: z.string().nullable().optional(),
  content: z.string(),
  similarity: z.number(),
  metadata: z.json().nullable().optional(),
});

export const vectorSearchResponseSchema = z.object({
  query: z.string(),
  collection: z.string(),
  total_candidates: z.number(),
  matched_count: z.number(),
  results: z.array(vectorSearchResultItemSchema),
});

const taskBaseSchema = z.object({
  compute: z
    .object({
      state: z.string(),
      parent_id: z.string().nullable(),
      error_code: z.string().nullable(),
    })
    .nullable()
    .optional(),
  id: z.string(),
  title: z.string(),
  kind: z.string(),
  service: z.string(),
  label: z.string(),
  status: z.string(),
  owner_id: z.string(),
  origin: z.string(),
  executor: z.string(),
  created_at: z.string(),
});

const operationSchema = z.object({
  finished_at: z.string().nullable(),
  expires_at: z.string().nullable(),
  error_code: z.string().nullable(),
  result: z.json().nullable(),
});

export const taskSchema = z.discriminatedUnion("source", [
  taskBaseSchema.extend({ source: z.literal("media"), data: jobSchema }),
  taskBaseSchema.extend({ source: z.literal("ai"), data: aiJobSchema }),
  taskBaseSchema.extend({
    source: z.literal("indexing"),
    data: indexingJobSchema,
  }),
  taskBaseSchema.extend({
    source: z.literal("operation"),
    data: operationSchema,
  }),
]);

export type Task = z.infer<typeof taskSchema>;

export type User = z.infer<typeof userSchema>;

export type Job = z.infer<typeof jobSchema>;

export type Service = z.infer<typeof serviceSchema>;

export type Member = z.infer<typeof memberSchema>;

export type Worker = z.infer<typeof workerSchema>;

export type AiJob = z.infer<typeof aiJobSchema>;

export type AiUsageSummaryItem = z.infer<typeof aiUsageSummaryItemSchema>;

export type AiUsageRecord = z.infer<typeof aiUsageRecordSchema>;

export type AiUsageResponse = z.infer<typeof aiUsageResponseSchema>;

export type EmbeddingResponse = z.infer<typeof embeddingResponseSchema>;

export type IndexingJob = z.infer<typeof indexingJobSchema>;

export type CollectionOverview = z.infer<typeof collectionOverviewSchema>;

export type VectorSearchResultItem = z.infer<
  typeof vectorSearchResultItemSchema
>;

export type VectorSearchResponse = z.infer<typeof vectorSearchResponseSchema>;

const messages = new Map(
  Object.entries({
    compute_wait_timeout:
      "자원 배정 대기 한도를 초과했습니다. 앞선 실행 상태를 확인해 주세요.",
    compute_queue_full:
      "자원 배정 대기열이 가득 찼습니다. 잠시 후 다시 요청해 주세요.",
    compute_process_still_running:
      "로컬 연산 프로세스가 아직 실행 중입니다. 종료 후 다시 확인해 주세요.",
    compute_child_still_running:
      "부모 작업의 연산 단계가 아직 실행 중입니다. 종료 후 다시 확인해 주세요.",
    compute_not_interrupted:
      "이 예약은 종료 확인 대상이 아닙니다. 최신 상태를 확인해 주세요.",
    compute_lease_lost:
      "자원 예약의 소유권을 잃었습니다. 실행 상태를 확인해 주세요.",
    n8n_compute_context_not_ready:
      "n8n의 공통 자원 헤더 연결·검수가 필요합니다.",
    raya_key_required: "Raya 연결 키를 확인해 주세요.",
    raya_not_configured:
      "Raya가 활성화되지 않았습니다. 관리자에게 연결 설정을 확인해 주세요.",
    raya_busy: "다른 요청이 Raya를 사용 중입니다. 잠시 후 다시 시도해 주세요.",
    raya_timeout:
      "Raya 로딩·추론 제한시간을 초과했습니다. 잠시 후 다시 요청해 주세요.",
    raya_memory_unavailable:
      "모델을 로딩할 여유 메모리가 부족합니다. 다른 작업을 종료한 뒤 다시 시도해 주세요.",
    raya_loading_failed:
      "Raya 모델을 로딩하지 못했습니다. 관리자에게 설치 상태를 확인해 주세요.",
    raya_inference_failed:
      "Raya 추론에 실패했습니다. 잠시 후 다시 시도해 주세요.",
    raya_process_failed:
      "Raya 실행이 중단됐습니다. 관리자에게 모델 상태를 확인해 주세요.",
    raya_request_too_large:
      "Raya 요청 크기가 한도를 초과했습니다. 입력을 줄여 주세요.",
    invalid_raya_request: "요청 유형과 분석할 내용을 확인해 주세요.",
    approval_required: "관리자 승인이 필요합니다.",
    login_required: "로그인이 만료됐습니다. 다시 로그인해 주세요.",
    storage_capacity_exceeded:
      "임시 저장 공간이 부족합니다. 결과 정리 후 다시 시도해 주세요.",
    upload_too_large: "업로드 한도를 초과했습니다.",
    unsupported_media: "지원 형식과 입력 길이를 확인해 주세요.",
    tts_not_configured:
      "TTS 설치와 연결 설정이 필요합니다. 관리자에게 확인해 주세요.",
    tts_generation_failed:
      "음성 생성에 실패했습니다. 입력을 줄여 다시 시도해 주세요.",
    tts_memory_unavailable:
      "음성 모델을 실행할 메모리가 부족합니다. 다른 연산 종료 후 다시 시도해 주세요.",
    voice_sample_missing:
      "참조 음성을 찾을 수 없습니다. 목소리를 다시 등록해 주세요.",
    voice_storage_unavailable:
      "목소리 저장소가 연결되지 않았습니다. NAS 마운트를 확인해 주세요.",
    voice_not_ready:
      "목소리 등록이 아직 완료되지 않았습니다. 등록 작업 상태를 확인해 주세요.",
    voice_in_use:
      "이 목소리를 사용하는 작업이 있습니다. 종료 또는 취소를 확인한 후 삭제해 주세요.",
    voice_storage_capacity_exceeded:
      "목소리 저장 공간이 부족합니다. 사용하지 않는 목소리를 삭제해 주세요.",
    clone_style_not_supported:
      "등록한 참조 음성에는 말투 지시를 사용할 수 없습니다.",
    invalid_job_options: "작업 옵션을 확인해 주세요.",
    invalid_media_or_conversion_failed: "영상을 읽거나 변환하지 못했습니다.",
    hardware_encoding_failed:
      "하드웨어 영상 변환이 실패했습니다. 관리자에게 인코더 상태와 CPU 전환 설정을 확인해 달라고 요청해 주세요.",
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
