# 뇌대리 서비스 연동 지침

문서 버전: 10 · 기준일: 2026-10-05

## 현재 연결 가능한 범위

**플랫폼 전용 서버 API와 서명 웹훅이 구현되어 있습니다.** 웹 테스트는 기존 `/api/` 세션 인증, 플랫폼은 `/api/v1/` 전용 키 인증을 사용합니다. 운영 전 플랫폼 수신 URL·키 공유 설정과 양쪽 연결 검수가 필요합니다. 사용자 쿠키나 워커 키를 서버 연동에 재사용하지 마세요.

공개 문서: `/integrations/SERVICE_INTEGRATION.md` · 플랫폼 API 명세: `/integrations/openapi.json`. 이 둘은 인증 없이 조회하며 실제 작업·결과는 인증이 필요합니다. 서버 클라이언트와 서명 검증 예제는 [platform_client.py](https://github.com/shnea/noedaeri/blob/main/examples/platform_client.py)를 사용합니다.

이 문서의 경로는 뇌대리 주소 기준 상대 경로입니다. 실제 주소·키·내부 경로는 소스나 문서에 넣지 않고 암호화 설정으로 관리합니다. `WORKER_API_KEY`와 `/internal/` 경로는 뇌대리 실행 워커 전용이며 외부 서비스 연동에 사용하지 않습니다.

| 기능 | 작업 종류 | 현재 상태 |
|---|---|---|
| 이미지 통합 처리 | `image.package` | JPEG 썸네일·WebP 미리보기·ZIP 생성 가능 |
| 영상 썸네일 | `video.thumbnail` | 웹 요청·결과 다운로드 가능 |
| 통합 영상 처리 | `video.package` | 썸네일·해상도별 HLS·ZIP 생성 가능 |
| 플랫폼 서버 인증·요청 | `/api/v1/` | 전용 키 인증 구현, 수신 주소 설정 후 접수 |
| 결과 수령·저장 확인 | `receipt` | API 구현. 플랫폼 파일 등록 어댑터는 플랫폼에서 구현 |
| TTS·임베딩·n8n 워크플로 실행 | 미정 | 미구현 |
| Raya 난이도 판단 | 동기 JSON | 기존 플랫폼 키·n8n 실행 키·웹 세션 API, CPU 추론·3등급 분기 구현 |
| n8n AI 작업 분기 예제 | 수동 실행 | 8개 작업·미등록 분기 → Raya → 3등급 분기 → n8n LangChain 모델 연결 완료 |
| 공통 AI 사용량 관리 | 뇌대리 usage | 연결 설계만 반영. 수집·저장·조회 API 미구현 |

## n8n AI 작업 분기 초안

### 외부 요청은 플랫폼에서 접수

외부 서비스의 AI·n8n 요청은 **외부 서비스 → 플랫폼 → 뇌대리 → n8n** 순서로 연결한다. 플랫폼은 파일서비스 연동과 동일한 `NOEDAERI_PLATFORM_API_KEY`를 `X-Noedaeri-API-Key` 헤더로 전달한다. 개별 앱에 뇌대리 키나 n8n 관리 키를 배포하지 않는다. 웹 관리·검수는 기존 플랫폼 로그인·승인 세션을 사용한다.

현재 플랫폼에서 실제 호출할 수 있는 AI 기능은 아래 `POST /api/v1/ai/raya/route` 난이도 판단이다. n8n 작업 접수·실행·결과 수령·usage 조회 API는 아직 미구현이며 수동 초안을 외부 작업 실행 API로 안내하지 않는다. 향후 플랫폼 접수에는 요청 ID·작업 종류·플랫폼이 확인한 서비스/사용자 식별을 연결하고 뇌대리에서 라우팅·캐시·사용량·실행 상태를 관리한다. n8n에는 검증된 작업만 실행용 인증으로 전달하며 n8n 관리 API 키를 외부 요청 인증에 재사용하지 않는다.

[가져오기용 워크플로 JSON](https://github.com/shnea/noedaeri/blob/main/examples/n8n_ai_routing_sample.json)을 제공한다. 수동 실행 → 가상 요청 → 작업 종류 분기 → 빈 지침 → 실제 Raya 추론 → 성능 등급 분기 순서다. 각 분기의 `instruction`을 비워 두었다. 각 모델 분기에는 n8n의 LangChain Agent와 전용 Chat Model 노드가 연결되어 직접 모델을 호출한다.

| `task_type` | 작업 |
|---|---|
| `blog.tags` | 블로그 태그 |
| `blog.summary` | 블로그 요약 |
| `portfolio.search` | 포트폴리오 검색 |
| `ui.render` | UI 빌더 화면 생성 |
| `comment.generate` | 댓글 생성 |
| `document.analyze` | 문서 분석 |
| `code.analyze` | 코드 분석 |
| `chat.general` | 일반 질답 |

입력은 `{ "task_type": "blog.tags", "prompt": "샘플 글" }` 형태다. 기본 샘플 노드는 8개 작업과 미등록 작업을 각각 하나씩 생성한다. 결과는 입력을 유지하고 `task_name`, 빈 `instruction`, `status: "awaiting_instructions"`를 추가한다. 등록되지 않은 작업은 별도 분기에서 `status: "unsupported_task"`로 표시하며 일반 질답으로 자동 처리하지 않는다. 노드 자체의 오류는 n8n 실행 실패로 남고 재시도 설정은 없다.

Raya 판단은 `routing` 아래 원래 요청과 합쳐 제공한다. HTTP 노드는 한 번에 한 요청씩 호출하고 실패 시 실행을 중단한다. 등급 Switch는 `routing.model_tier`의 `L1`, `L2`, `L3`를 지정된 공급자 연결 분기로 보낸다. 순환 후보를 준비하고 n8n 내부의 LangChain 에이전트 노드로 모델을 호출한다. 예상하지 못한 등급은 별도 확인 분기로 보낸다.

새 작업은 Switch에 `task_type` 규칙을 추가하고 지침 노드를 복제한 뒤 출력에 연결한다. 작업 종류는 모델 성능 등급과 별개다. 공급자 순서는 L1 OpenRouter `openrouter/free`, L2 GroqCloud `openai/gpt-oss-120b`, L3 Gemini `models/gemini-3.8-flash`, 폴백 Mistral `ministral-8b-latest`다.

샘플은 n8n 편집 권한으로 수동 실행하며 자동 실행용 웹훅은 없다. 가져오기용 JSON에는 비밀키·실제 서버 주소·Credential ID가 없다. n8n HTTP 노드에 실제 뇌대리 주소와 아래 계약의 Header Auth를 선택한다. 실행 데이터 보관·삭제는 해당 n8n 인스턴스의 정책을 따르며 뇌대리 웹 테스트의 24시간 보관을 적용하지 않는다. 실제 AI 답변의 호출 한도·결과 수령·학습 데이터 보존 계약은 후속 범위다.

### 3단계 공급자와 순환 대체

| 단계 | 지정 공급자 | 초안 상태 |
|---|---|---|
| L1 | OpenRouter `openrouter/free` | n8n LangChain 모델 연결 |
| L2 | GroqCloud `openai/gpt-oss-120b` | n8n LangChain 모델 연결 |
| L3 | Google AI Studio `models/gemini-3.8-flash` | n8n LangChain 모델 연결 |
| 폴백 | Mistral 직접 API `ministral-8b-latest` | n8n LangChain 모델 연결 |

Raya가 L1~L3 중 하나를 직접 판단하며, 초안의 `provider_plan`은 선택 단계부터 **L3→L2→L1→Mistral 폴백→L3** 순환 순서로 후보를 준비한다. 시작별 후보는 다음과 같다.

| 시작 | 한 바퀴의 후보 순서 |
|---|---|
| L3 | L3 → L2 → L1 → Mistral |
| L2 | L2 → L1 → Mistral → L3 |
| L1 | L1 → Mistral → L3 → L2 |

Mistral은 L1 다음 순서이며 기본 3개를 모두 확인한 뒤에만 사용하도록 제한하지 않는다. 한 요청에서 각 공급자는 한 번씩, 최대 4개 공급자를 확인한다. 실제 호출 단계는 `selected_level: null`, 한도 조회는 `quota_status: "not_connected"`로 표시하며 모든 단계를 사용할 수 있다고 가정하지 않는다.

`공급자 경로 · 한도 연결 대기`는 `route_target`의 `primary`·`mistral`·`no_tokens`를 각각 기본 단계·Mistral 폴백·소진 안내로 연결한다. 현재는 한도 조회가 미구현이므로 `primary`만 준비하며 폴백과 소진 안내를 실제로 선택하지 않는다. 향후 확인된 한도 소진 시 순환 순서의 다음 공급자로 이동한다. 이미지·도구·구조화 출력 등 필수 기능을 지원하지 않는 후보는 사용할 수 없다. 기능 미지원·인증 실패·알 수 없는 한도를 토큰 소진으로 표시하지 않는다. 한 바퀴를 확인해 네 공급자가 모두 소진됐으면 `exhausted_action: "notify_no_tokens"`에 따라 “현재 사용 가능한 AI 토큰이 없습니다. 한도 갱신 후 다시 시도해 주세요.”라고 안내하고 종료한다. 같은 소진 상태로 무한 재호출하거나 자동 유료 호출하지 않는다.

Mistral은 Raya의 추가 등급이 아니라 L1 다음에 확인하는 별도의 폴백이다. OpenRouter를 통한 Mistral 모델 호출과 구분해 직접 API를 사용할 계획이며 계정의 무료 모드·실제 모델 접근·사용량·한도를 연결 시 확인한다. Mistral의 무료 모드도 한도가 있다. [Mistral 사용량·한도 문서](https://docs.mistral.ai/admin/billing-usage/usage-limits)를 따른다.

분당 한도·일일/월간 한도는 갱신 시각과 함께 구분할 계획이다. 사용량 보고 누락만으로 잔여 한도를 안다고 간주하지 않는다. 인증 오류·입력 오류·일반 서버 장애를 한도 소진으로 오인해 반복 호출하지 않는다. 실제 한도 판독·소진 안내·순환 대체 공급자 호출은 아직 구현하지 않았다. usage에는 추천 단계·최종 단계·대체 사유와 각 실제 호출을 구분해 남기도록 설계한다.

### 이미지 여부와 결과 캐시

`Raya 요청 준비` 노드는 명시적인 `has_images`·`requirements.vision`, 이미지 참조·이미지 배열·대화의 이미지 content에서 이미지 포함 여부를 정리한다. 도구와 구조화 출력 요구는 각각 `requirements.tools`, `requirements.structured_output`에 남긴다. 실제 공급자 선택은 해당 모델의 기능을 별도로 확인해야 한다. 현재 Raya는 포함 여부와 텍스트만 판단하며 이미지 분석을 제공하지 않는다.

결과 캐시는 인증·소유권 확인 뒤 Raya/공급자 호출보다 먼저 조회하도록 계획한다. 서비스·소유권 범위·작업 종류·입력 내용 및 이미지 식별/버전·UI revision·지침 버전·모델 정책을 키에 반영한다. 적중하면 기존 결과를 반환하고 모델을 호출하지 않으며 cache hit와 실제 공급자 사용량을 분리한다. 댓글의 thread/memory처럼 변화하는 문맥도 키에 포함해야 한다. 권한이 바뀐 결과와 만료된 결과는 재사용하지 않는다.

현재 초안은 `cache: {status: "not_connected", hit: null}`을 표시한다. 저장소·TTL·조회·무효화·용량 제한은 미구현이며 요청자가 보낸 cache hit를 신뢰해 결과를 반환하지 않는다. 기존 블로그의 hash 기반 캐시는 기존 흐름에 유지한다. Raya 추론 응답을 저장하는 캐시와 학습 원문 저장은 이번 범위에 포함하지 않는다.

## 기존 AI 흐름을 연결하는 기준

아래는 기존 워크플로를 확인해 정리한 **후속 연결 설계**다. 현재 수동 초안에는 연결 메모만 반영했으며 운영 워크플로의 입력·출력·저장 대상은 변경하지 않았다. 기존 흐름의 역할을 유지하고 AI 호출 직전에 공통 라우팅을 연결한다.

| 분기 | 유지할 계약·연결 지점 |
|---|---|
| `blog.tags` | `hash`로 처리 점유·캐시 확인 → `context`와 `system` 구성 → 라우팅·AI 호출 → 태그 구조 검증 → `{hash,result}` 저장. 기존 태그는 1~8개, 항목당 1~30자다. |
| `blog.summary` | 동일한 점유·캐시·저장 흐름. 결과의 `summary`는 비어 있지 않은 문자열이며 최대 500자다. |
| `comment.generate` | 원문의 target·thread·memory·말투 문맥과 작업 지침을 유지한다. 기존 출력의 `comment` 계약과 추가 필드·길이 검증 범위는 연결 전에 확정한다. 현재 JSON 객체 검사만으로 완전한 댓글 검증이 된다고 간주하지 않는다. |
| `ui.render` | `messages`·이미지 참조 → 도구를 사용하는 AI Agent → `{reply,imageFileId}` 응답. MCP의 실제 컴포넌트·템플릿·디자인 문맥, 읽기 전용 권한, revision 충돌 검사, 사용자 검토 후 적용할 변경 제안을 유지한다. |
| 나머지 4종 | 검색·문서·코드·일반 질답 지침과 결과 계약은 후속으로 제공한다. 기존 4개 기능의 지침을 자동 복제하지 않는다. |

블로그 작업은 캐시 적중 시 Raya와 실제 모델을 호출하지 않는다. 실패 기록과 기존 오류 응답도 유지한다. `hash`는 기존 중복 처리용 값이며 여러 서비스의 공통 요청 식별자로 바로 대체하지 않는다. UI 빌더에서는 현재 Agent·MCP 흐름을 기준으로 연결하고 사용되지 않는 별도 HTTP 노드를 추가 호출 경로로 오인하지 않는다.

Raya에는 현재 사용자 요청과 필요한 텍스트 문맥을 전달하고, 실제 AI에는 원래 대화·이미지·전체 지침을 유지한다. Raya는 이미지 내용을 분석하지 않는다. 입력 잘림과 이미지·도구·구조화 출력 요구를 별도로 확인해야 한다. 성능 등급만으로 공급자를 확정하지 않고 필요한 기능과 한도를 만족하는 실제 모델을 선택한다. 원문과 외부 도구 결과는 참고 데이터로 취급하며 신뢰된 시스템 지침과 분리한다. 인증 토큰·콜백 키는 모델 입력에 넣지 않는다.

### 뇌대리 usage로 통합할 범위

사용량의 저장·조회 책임은 뇌대리로 모으고 n8n은 실제 호출의 사용량을 보고하는 구조로 계획한다. 기존 수집기는 특정 앱의 요청 ID·콜백 URL·토큰에 묶여 있으므로 이름만 바꾸거나 콜백을 먼저 끊지 않는다. 저장 API·서비스별 인증·조회 권한을 구현하고 이전 집계와 대조한 뒤 전환한다. **현재 usage 수신·조회 엔드포인트는 제공하지 않는다.**

| 기록 | 기준 |
|---|---|
| 요청 연결 | 서비스·소유권을 서버에서 확인한 `request_id`, `task_type`. 공급자 호출 ID 또는 실행·노드·실행 차수·항목 식별자를 연결해 같은 수집 보고의 중복을 제거한다. |
| 모델·실행 | Raya 추천 등급과 실제 `provider`·응답 모델, 호출·재시도 차수, 실행 상태·시간을 구분한다. 실제 모델을 모르면 미확인으로 남긴다. |
| 토큰 | 공급자가 반환한 입력·출력·총 토큰만 집계한다. 추정치를 실제 사용량에 더하지 않고 누락은 미확인으로 표시한다. 도구를 사용하는 Agent의 여러 모델 호출과 실패 전 발생한 호출도 각각 남긴다. |
| 중복·재시도 | 동일 보고의 재전송은 한 번만 반영한다. 실제로 새 모델 호출이 발생한 재시도는 별도의 사용량이다. 성공한 요청만 집계하면 실패 비용을 누락할 수 있다. |
| 범위·보존 | 관리자는 전체 집계, 연결 서비스는 플랫폼을 통해 자신의 요청만 조회하도록 설계한다. 서비스·사용자 식별은 인증된 플랫폼이 확인해 전달하며 임의 식별자로 다른 소유자의 결과를 조회하지 않는다. 원문·지침·응답 전문·인증 토큰·임의 콜백 주소를 usage에 저장하지 않는다. 보존 기간·집계 기준·한도·실패 보고 정책은 구현 전에 확정한다. |

Raya의 CPU 추론 시간·입력 토큰은 라우터 운영 지표로 구분하고 외부 모델 사용량에 합산하지 않는다. 무료 모델도 사용량을 기록하지만 토큰 수만으로 비용·무료 한도를 단정하지 않는다. 공급자마다 사용량 응답 형식이 달라 별도 정규화가 필요하다. 수집 실패는 AI 결과 실패와 구분하고 보고를 재시도할 때 모델을 다시 호출하지 않는다.

usage와 학습 데이터는 구분한다. 관리자 검토·파인튜닝을 위한 원문·결과 수집, 접근 권한, 보존 정책, 내보내기와 학습 실행은 아직 구현하지 않았다. 이후에는 추천 등급·실제 모델·결과 품질·관리자가 고른 적정 등급을 연결해 검토할 수 있도록 별도 설계한다.

## Raya 난이도 판단 API

공식 [TextCortex/raya](https://huggingface.co/TextCortex/raya?hardware=apple-m2-ultra-96gb)의 ONNX fp32 모델을 리비전과 체크섬으로 고정한다. CPU 전용이며 텍스트 요청의 성능 등급을 분류한다. 안전성 판정·AI 답변·멀티턴 대화 전체 이해를 보장하는 기능이 아니다. 공식 선택지 분류 기능에 단순·중간·고난도에 해당하는 L1~L3 기준과 512토큰 예산을 지정한다. 3개 선택지의 정확도는 사용자 작업 데이터로 아직 검증·교정하지 않았다. 입력이 길면 앞부분만 분석하고 `input_truncated=true`를 반환한다. 예측 확률과 confidence는 정답률이 아니며 실제 사용 데이터로 검토·교정해야 한다.

| 용도 | 메서드·경로 | 인증 |
|---|---|---|
| 플랫폼의 난이도 판단 | `POST /api/v1/ai/raya/route` | 기존 `X-Noedaeri-API-Key` 플랫폼 키 |
| n8n 실행 중 내부 난이도 판단 | `POST /api/ai/v1/raya/route` | `X-Noedaeri-Raya-Key` 실행 전용 키 |
| 승인 사용자 추론 | `POST /api/raya/route` | 현재 승인된 세션 + Origin·CSRF |
| 관리자 상태 | `GET /api/admin/raya/status` | 현재 관리자 세션 |
| 최소 유지·유휴 시간 변경 | `PATCH /api/admin/raya/policy` | 관리자 세션 + Origin·CSRF |
| 모델 메모리 명시적 해제 | `POST /api/admin/raya/release` | 관리자 세션 + Origin·CSRF |

플랫폼 경로는 기존 파일서비스 요청 키를 그대로 검증하며 브라우저 쿠키·Raya 실행 키·워커 키로 대체하지 않는다. n8n 실행 전용 키는 내부 난이도 판단만 허용하며 플랫폼 요청 키나 n8n 관리 API 키를 이 헤더에 넣지 않는다. 실행 키는 SOPS 환경 설정과 n8n Credentials에만 보관하며 외부 앱에는 전달하지 않는다. HTTPS 외부 주소와 설정된 내부 API 주소에 동일한 경로를 제공한다. 플랫폼 경로의 요청·응답·인증·오류는 공개 플랫폼 OpenAPI에도 포함한다.

요청 예시:

```json
{"task_type":"blog.summary","prompt":"요약할 샘플 글","instruction":""}
```

`task_type`은 1~80자 식별자, `prompt`는 공백 제외 1~16,000자, `instruction`은 선택 필드로 최대 8,000자다. `has_images`는 선택적인 boolean이며 기본 false다. 이미지 포함 여부만 모델 문맥에 넣고 이미지 파일·URL·내용은 이 API로 수신하거나 분석하지 않는다. 요청 본문 전체는 64KiB 이하이며 알 수 없는 필드는 거절한다. 향후 작업 유형 추가를 위해 `task_type`을 8종으로 제한하지 않는다. 원래 요청과 작업 지침을 모델 입력으로 사용한다.

아래는 **응답 형식 예시**이며 실제 예측 수치가 아니다:

```json
{
  "task_type":"blog.summary",
  "model_tier":"L1",
  "probabilities":{"L1":0.8,"L2":0.15,"L3":0.05},
  "confidence":0.7,
  "input_tokens":40,"input_truncated":false,
  "inference_ms":30.0,"elapsed_ms":30.5,"cold_start":false,
  "model":"TextCortex/raya",
  "revision":"48c8658436c268163f31720dd6c40165894e4093",
  "device":"cpu","runtime":"onnx-fp32"
}
```

결과는 같은 HTTP 응답으로 수령하며 작업 큐·파일 다운로드·완료 웹훅은 사용하지 않는다. 요청·지침·추론 결과를 뇌대리 DB·파일·로그에 저장하지 않고 학습 데이터로 자동 수집하지 않는다. 브라우저 테스트 결과는 해당 화면에서만 유지한다. 모델 파일과 관리자 시간 정책은 영속 저장한다. 모델 실행 상태는 프로세스 수명 동안만 유지한다.

모델 로딩은 한 번만 수행하며 한 번에 하나를 추론한다. 자식이 상속한 로컬 파일 잠금으로 API가 비정상 종료돼도 기존 모델 프로세스의 종료 전에 중복 로딩하지 않는다. 같은 설치의 로컬 프로세스만 조정하며 여러 호스트의 예약은 지원하지 않는다. HTTP 요청은 처리 중 1개와 대기 1개를 허용하고 그 이상은 429다. 기본 대기 상한은 5초, 로딩 포함 추론 상한은 90초다. 최소 유지 60초와 유휴 300초를 모두 만족하면 프로세스를 종료·회수한 뒤 메모리를 다시 사용한다. 활성 추론은 유휴 정리로 종료하지 않는다. 관리자 시간 정책은 DB에 저장하고 재시작 시 복원한다. 상태 API는 상태·대기 수·오류 코드·실행 정책을 제공하며 실제 주소·키·모델 저장 경로는 노출하지 않는다.

정책 변경 예시: `{"minimum_keep_seconds":60,"idle_seconds":300}`. 최소 유지 0~86,400초, 유휴 1~86,400초를 허용한다. 처리 중 정책 변경·명시적 해제는 429로 거절한다. 메모리·대기·제한시간의 운영 값은 암호화 설정에서 조정한다. 로딩 전 설정된 여유 메모리와 로딩 후 최소 1GiB의 여유를 확인한다. 시스템 여유 메모리 검사는 OS 보고값 기준이며 다른 서비스와의 중앙 메모리 예약은 아직 없다.

| HTTP | 오류 코드·처리 |
|---|---|
| 401·403 | 전용 키 또는 로그인·승인·관리자 권한·CSRF 확인 |
| 413·422 | `raya_request_too_large`, `invalid_raya_request` — 입력을 수정 |
| 429 | `raya_busy` — 대기 상한·동시 요청 한도. 잠시 후 제한적으로 재시도 |
| 503 | `raya_not_configured`, `raya_memory_unavailable`, `raya_loading_failed`, `raya_inference_failed`, `raya_process_failed` — 활성화·자원·설치를 확인 |
| 504 | `raya_timeout` — 자식 프로세스 종료·회수 후 실패. 제한적으로 새 요청 가능 |

자동 재시도와 임의 등급으로의 우회는 하지 않는다. 실패 시 종료를 확인하기 전 같은 실행 자원을 재배정하지 않는다. n8n 워크플로에서도 실패를 표시하고 실제 AI 호출을 진행하지 않는다.

## 플랫폼 기능 대응 현황

구현된 흐름은 작업 접수 → 완료·실패 웹훅 → 플랫폼의 결과 수령·저장 → 저장 확인 후 정리입니다. 플랫폼은 완료를 기다리며 반복 조회할 필요가 없습니다. 조회 API는 응답 유실·알림 누락 복구용입니다. 웹훅 서명·지속성·제한된 재전송을 제공하고, 수신 측은 이벤트 ID로 중복을 제거해야 합니다. 현재 기능 대응표는 [플랫폼 기능 조사 문서](https://github.com/shnea/noedaeri/blob/main/docs/PLATFORM_COMPATIBILITY.md)에 정리했습니다.

| 영역 | 현재 판단 |
|---|---|
| 이미지 썸네일·WebP 미리보기 | 구현·생성 샘플 검수 완료. 실제 플랫폼 파일 대조 검수는 후속 |
| 영상 썸네일·HLS | 길이·비트레이트·파일 크기 제공, 출력 공간 예약·종료 확인 후 복구 구현 |
| 화질 단계 | 뇌대리 480p·720p·1080p 유지. 플랫폼에서 수용하도록 변경 예정 |
| 원본 보기·OG·공유 URL | 플랫폼의 저장·표시 기능으로 유지 |
| 플랫폼 전용 API | 뇌대리 구현 완료, 실제 플랫폼 연동 검수는 후속 |

## 인증과 공통 규칙

1. 플랫폼 로그인 후 뇌대리 관리자의 승인이 필요합니다. 승인 대기 사용자는 작업을 호출할 수 없습니다.
2. 현재 API는 뇌대리의 Secure·HttpOnly 세션 쿠키를 사용합니다. 브라우저는 같은 origin으로 요청합니다.
3. 변경 요청에는 `/api/me` 응답의 `csrf`를 `X-CSRF-Token` 헤더에 넣습니다. 서버는 `Origin`도 확인합니다.
4. 작업과 결과는 소유자만 조회합니다. 파일마다 현재 접근 권한과 만료 시각을 확인합니다.
5. 등록 요청마다 UUID `idempotency_key`를 만듭니다. 통신 실패 후 같은 요청을 재전송할 때는 같은 키와 같은 내용을 유지합니다. 같은 키로 내용을 바꾸면 409입니다.

아래 예시는 **로그인·승인된 뇌대리 웹과 같은 origin에서의 브라우저 호출 예시**입니다. 외부 서버용 실행 예제로 해석하지 마세요.

```javascript
const meResponse = await fetch('/api/me');
if (!meResponse.ok) throw new Error('로그인 상태를 확인하세요');
const me = await meResponse.json();
const headers = {
  'Content-Type': 'application/json',
  'X-CSRF-Token': me.csrf,
};
// 재전송할 때 유지할 값입니다. 재시도마다 새로 만들지 않습니다.
const requestKey = crypto.randomUUID();
```

## 요청부터 결과 수령까지

| 단계 | 메서드와 경로 | 처리 |
|---|---|---|
| 서비스 확인 | `GET /api/services` | 작업 종류·입력 타입·옵션 스키마 조회 |
| 작업 등록 | `POST /api/jobs` | 작업 ID 발급, 영상은 `uploading` 상태 |
| 입력 전송 | `PUT /api/jobs/{jobId}/input` | 원시 파일 바이트 전송, 성공 시 `queued` |
| 상태 조회 | `GET /api/jobs?limit=100` | 자신의 최근 작업 목록에서 ID로 확인 |
| 취소 | `POST /api/jobs/{jobId}/cancel` | 대기·실행 작업의 취소 요청 |
| 결과 다운로드 | `GET /api/jobs/{jobId}/result` | 썸네일 JPEG 또는 통합 ZIP |
| 통합 결과 파일 | `GET /api/jobs/{jobId}/files/{filename}` | 결과에 명시된 HLS·조각·썸네일·ZIP |

개별 작업은 `GET /api/jobs/{jobId}`로 조회할 수 있습니다. 목록은 최근 최대 100개이며 페이지네이션은 없습니다. 플랫폼은 접수 시 job ID와 요청 키를 자체 DB에 저장하고 `/api/v1/jobs/{jobId}`로 누락을 복구합니다.

```javascript
const payload = {
  kind: 'video.package', // 썸네일만 필요하면 video.thumbnail
  title: '영상 변환 요청',
  idempotency_key: requestKey,
  input: { type: 'upload' },
  options: { seconds: 0 },
};
const created = await fetch('/api/jobs', {
  method: 'POST', headers, body: JSON.stringify(payload),
});
if (!created.ok) throw new Error(`등록 실패: ${created.status}`);
const job = await created.json();
// videoFile은 사용자가 선택한 File 객체입니다.
if (job.status === 'uploading') {
  const uploaded = await fetch(`/api/jobs/${job.id}/input`, {
    method: 'PUT',
    headers: {
      'Content-Type': 'application/octet-stream',
      'X-CSRF-Token': me.csrf,
    },
    body: videoFile,
  });
  if (!uploaded.ok) throw new Error(`입력 실패: ${uploaded.status}`);
}
```

입력은 multipart가 아닌 파일 바이트입니다. 업로드 응답이 유실됐다면 먼저 작업 상태를 확인합니다. `uploading`일 때만 같은 입력을 다시 보내고, `queued` 이후에는 덮어쓰지 않습니다. 바이트 위치부터 업로드 재개하는 기능은 없습니다.

상태 조회는 간격을 두고 수행합니다. 웹은 5초 간격을 사용합니다. `succeeded`, `failed`, `cancelled`에서는 자동 조회를 종료하고 결과 또는 실패 원인을 처리합니다. 완료되지 않은 작업의 결과를 먼저 요청하지 마세요.

## 이미지 통합 처리

작업 종류: `image.package`. 원본 파일은 결과에 포함하지 않습니다.

```json
{
  "kind": "image.package",
  "title": "이미지 파생물 생성",
  "idempotency_key": "<요청마다 생성한 UUID>",
  "input": { "type": "upload", "extension": "png" },
  "options": {}
}
```

`extension`은 점 없는 소문자 확장자이며 필수입니다. 파일명·경로는 전달하지 않습니다. 허용 값은 `png`, `jpg`, `jpeg`, `jfif`, `gif`, `webp`, `bmp`, `ico`, `tif`, `tiff`, `heic`, `heif`, `avif`입니다. 실제 디코딩 형식과 일치해야 합니다. 입력 전송·상태 조회는 위 공통 계약과 같습니다.

| 항목 | 처리 기준 |
|---|---|
| 이미지 변환 제한 | 디코딩 단계에서 최대 32,000,000바이트·40,000,000화소·각 변 10,000px. 공통 업로드 5GiB와 별도 |
| 썸네일 | `thumbnail.jpg`, 최대 480×320, 확대 안 함, JPEG 품질 85 |
| 미리보기 | `preview.webp`, 최대 1600×1600, 확대 안 함, 품질 80·압축 수준 4 |
| 출력 제한 | 이미지별 최대 2,000,000바이트. 초과 시 실패하며 부분 결과는 공개하지 않음 |
| 방향·색상 | EXIF 방향 보정, ICC가 있으면 sRGB 변환. 잘못된 프로필은 실패할 수 있음 |
| 투명도 | WebP는 유지, JPEG는 흰 배경 합성 |
| 메타데이터 | EXIF·원본 ICC·파일명 등은 결과 이미지에서 제거 |
| 움직이는/여러 장 입력 | 첫 프레임만 처리. ICO는 디코더가 선택한 최대 아이콘을 사용 |
| 프로세스 제한 | 별도 프로세스, CPU 30초·전체 60초 제한, 취소·점유 상실 시 종료 |
| 단계 | `image_processing` → `finished` |

결과는 `type=image_package`, `source`(format·media_type·보정 후 width/height·frame_policy), `thumbnail`과 `preview`(name·width·height·media_type·bytes), `download=image.zip`, `files`로 구성됩니다.

`files`는 `thumbnail.jpg`, `preview.webp`, `metadata.json`, `image.zip`입니다. `/result`는 ZIP이며, 개별 파일은 `/files/{filename}`으로 받습니다. ZIP에는 썸네일·미리보기·metadata.json만 들어갑니다. `source`는 원본을 설명하는 값이며 원본 파일이나 다운로드 주소가 아닙니다.

현재 PC에서 13개 확장자별 생성 샘플의 실제 디코딩·출력을 검사했습니다. 동일 코덱의 모든 실기기 변형·HDR 색 표현·손상 패턴까지 검증했다는 의미는 아닙니다. 플랫폼의 실제 자료로 추가 대조할 예정입니다. 출력은 SDR 8비트 미리보기이며 HDR 보존을 보장하지 않습니다.

## 영상 썸네일

작업 종류: `video.thumbnail`

| 입력·옵션 | 의미 |
|---|---|
| `input.type` | `upload` 고정 |
| `options.seconds` | 추출 시점(초). 기본 0, 실제 영상 길이 미만이어야 함 |
| 결과 | 최대 480×320 범위의 JPEG, 원본 비율 유지 |

완료한 작업의 `result.type`은 `artifact`, `name`은 `thumbnail.jpg`, `media_type`은 `image/jpeg`입니다. `/result`에서 실제 파일을 받습니다.

## 통합 영상 처리

작업 종류: `video.package` · 입력과 `seconds` 옵션은 썸네일과 같습니다.

- 입력 검사·결과 공간 예약 → 썸네일 → 해상도별 변환 → HLS 재생 목록과 ZIP 생성 순서입니다.
- 원본의 짧은 변을 기준으로 480p·720p·1080p 중 가능한 크기만 생성합니다. 작은 영상을 확대하지 않습니다. 480p 미만은 원본 짧은 변의 짝수 크기를 사용합니다.
- 출력은 H.264/AAC, 30fps, 약 6초 단위 MPEG-TS VOD입니다. 오디오 없는 입력도 처리합니다.
- 해상도별 순차 변환합니다. 별도 입력 없이 기본 `auto`로 macOS VideoToolbox 하드웨어 디코딩·H.264 인코딩을 우선 시도하고, 가속 변환 실패 시 CPU로 한 번 전환합니다. 다른 OS는 CPU입니다. 크기 조정·썸네일은 CPU로 처리합니다. `FFMPEG_VIDEO_ENCODER`는 서버의 선택적 운영 설정이며 요청 `options`에는 넣지 않습니다.
- 모든 단계가 끝나야 결과를 공개합니다. 단계별 부분 성공·이어하기는 아직 지원하지 않습니다.

`stage`에는 `probing`, `reserving`, `thumbnail`, `encoding_480p` 같은 해상도별 단계, `packaging`, `finished`가 표시됩니다. `waiting_hardware_encoder`는 같은 저장 루트의 다른 작업이 하드웨어 인코더를 사용 중이라는 뜻입니다. 대기도 전체 30분 제한에 포함하며 취소할 수 있습니다. 실패·취소 시 마지막 단계를 보존합니다. 정확한 백분율 진행률은 아직 제공하지 않습니다.

기본 `auto`에서는 하드웨어 실행이 실패하면 자식 종료·슬롯 반환을 확인하고 미완성 해상도별 출력을 삭제한 뒤 전체 해상도를 CPU로 한 번 재변환합니다. `cpu_fallback` 단계와 결과 `hardware_fallback=true`로 전환을 알립니다. CPU 재변환도 실패하면 작업을 실패로 종료합니다. 취소·시간 초과·저장 공간 부족은 CPU 재시도를 하지 않으며 전체 제한시간은 유지합니다. 서버 관리자가 `h264_videotoolbox`로 고정한 경우에는 CPU 전환 없이 `hardware_encoding_failed`로 종료하고, `libx264`는 CPU 고정입니다. 실패한 작업의 새 요청에는 새 작업 ID·원본 재업로드를 사용합니다.

결과의 `video_encoder`에 실제 사용한 인코더, `hardware_fallback`에 가속 실패 후 CPU 전환 여부를 기록하고 웹에 표시합니다. 기존 결과에는 이 필드가 없을 수 있습니다. 인증·업로드 한도·다운로드·웹 24시간/플랫폼 별도 보존·저장 확인 계약은 동일하게 적용합니다.

완료 결과 형식 예시입니다. 실제 파일 목록과 해상도는 원본에 따라 달라집니다.

```json
{
  "type": "video_package",
  "video_encoder": "h264_videotoolbox",
  "hardware_fallback": false,
  "master": "master.m3u8",
  "thumbnail": "thumbnail.jpg",
  "download": "video.zip",
  "variants": [
    { "label": "480p", "width": 852, "height": 480, "playlist": "480p.m3u8" }
  ],
  "files": ["thumbnail.jpg", "480p.m3u8", "480p-00000.ts", "master.m3u8", "video.zip"]
}
```

`master`는 자동 화질 전환의 진입점입니다. `variants`는 수동 화질 선택에 사용할 수 있습니다. `files`에 없는 이름을 추측해서 요청하지 마세요. 웹 재생은 현재 같은 origin의 인증된 세션을 사용하며, 외부 도메인 임베드·공개 스트리밍·CORS 허용 계약은 제공하지 않습니다.

파일 서비스로 옮길 때는 재생 목록과 영상 조각을 함께 저장하고 상대 파일명 관계를 유지해야 합니다. 실제 등록 API는 파일 서비스 측에서 별도로 연결할 예정입니다. 뇌대리가 현재 자동 업로드한다고 가정하지 마세요.

## 한도·보관·실패 처리

| 항목 | 현재 기본값·정책 |
|---|---|
| 영상 입력 | 최대 5GiB (5,368,709,120바이트), MP4/MOV·Matroska/WebM |
| 영상 | 최대 1시간, 각 변 4096px 이하·850만 픽셀 이하 |
| 실행 제한 | 썸네일 120초, 통합 작업 전체 30분 |
| 동시 실행 | 워커당 한 작업. 같은 저장 루트의 하드웨어 인코딩은 한 작업씩 배정 |
| 결과 보관 | 웹 테스트 완료 후 24시간, `expires_at` 확인 |
| 입력·중간 파일 | 종료 후 정리, 실행 중 파일은 정리하지 않음 |
| 플랫폼 결과 보존 | 저장 확인까지 유지하되 기본 최대 7일. 아래 플랫폼 계약 참조 |
| 재시도 | 무조건 재실행하지 않음. `interrupted`는 실행 상태 확인 필요 |

오류는 HTTP 상태와 JSON `detail`을 함께 확인합니다. 입력 구조 검증 실패는 `detail`이 배열일 수 있습니다. 민감한 헤더·쿠키·키를 로그에 남기지 마세요.

| 응답·오류 | 호출 측 처리 |
|---|---|
| 401 `login_required` | 세션 확인·다시 로그인 |
| 403 `approval_required` / `csrf_failed` | 승인 또는 CSRF·Origin 확인 |
| 409 `idempotency_conflict` | 같은 키로 보낸 내용이 달라졌는지 확인 |
| 410 `result_unavailable` / `result_missing` | 만료·파일 유실. 기존 다운로드 주소 재시도 중단 |
| 413 `upload_too_large` | 입력 파일 크기 축소 |
| 422 | 작업 종류·옵션·입력 검증 결과 확인 |
| 507 `storage_capacity_exceeded` | 임시 저장 공간 확보 후 새 요청 검토 |

실행 중 실패는 작업의 `error_code`로 제공합니다. 대표적으로 `unsupported_media`, `processing_timeout`, `invalid_media_or_conversion_failed`, `storage_capacity_exceeded`, `lease_lost`가 있습니다. 취소 요청 성공은 프로세스 종료 완료와 다릅니다. 작업 상태가 `cancelled`로 바뀌는지 확인하세요.

## 연결 검수 목록

- 인증·미승인·권한 철회·다른 소유자 결과 접근을 확인합니다.
- 동일 키 재전송, 업로드 실패, 취소, 만료를 확인합니다.
- HLS의 master·해상도별 재생 목록·모든 조각을 함께 검수합니다.
- 문서 조회 성공과 실제 변환·재생 성공을 구분합니다.
- 새 기능의 요청·응답·인증·한도·보존 정책·실패 처리·구현 상태를 이 문서에 함께 기록합니다.


## 결과 공간 예약·중단 복구·재시도

영상 결과에는 `duration_seconds`(입력 길이, 초), `frame_rate`(출력 30fps), `estimated_output_bytes`(예약량), `total_bytes`(ZIP 포함 실제 총 바이트), `file_sizes`(파일명 → 바이트)가 추가됩니다. 각 `variants`에는 `video_bitrate`(인코더 목표 bit/s), `bandwidth`(master에 기록하는 추정 bit/s), `bytes`(해당 화질 재생 목록·조각 합계)가 포함됩니다. 비트레이트는 실측 평균값이 아닙니다.

변환 전 길이 × 각 화질의 영상·오디오 비트레이트 합계를 기준으로 TS 여유 30%, ZIP 중복 2배, 추가 4MiB를 예약합니다. 이미지 통합 처리는 8,000,000바이트, 단일 썸네일은 2MiB를 예약합니다. 입력·다른 작업 예약·현재 결과 파일량과 디스크 여유를 함께 검사하며, 일부 사용량을 중복 계산하는 보수적 제한입니다. 공간이 부족하면 `storage_capacity_exceeded`로 실패합니다. 실행 중에도 실제 출력량·전체 저장량을 검사하지만 OS 디스크 할당량처럼 쓰기를 원자적으로 제한하지는 않습니다.

작업 응답의 `output_reserved`는 현재 예약 바이트이며 종료 후 0입니다. 점유 만료 시 `interrupted`로 표시하고, 워커와 자식 프로세스의 파일 잠금 해제를 확인한 뒤 `failed`로 복구합니다. 살아 있는 프로세스를 단순히 연결 끊김만으로 재실행하거나 그 파일을 삭제하지 않습니다. 적용 범위는 현재 동일 호스트의 네이티브 워커이며 NAS·여러 호스트 간 잠금은 미검증입니다. 기존 버전에서 시작한 잠금 미적용 작업은 자동 복구하지 않습니다.

실패·취소 작업은 웹의 **입력 다시 올려 재시도**로 새 작업을 만들 수 있습니다. `POST /api/jobs`에 기존 필드와 `retry_of: "<기존 작업 UUID>"`를 넣고 **새 idempotency_key**를 사용합니다. 해당 새 요청 자체의 통신 재전송에는 같은 키를 유지합니다. 기존 작업은 같은 소유자의 `failed` 또는 `cancelled` 상태여야 하며 종류는 동일해야 합니다. 원본을 다시 업로드하고 제목·옵션은 변경할 수 있습니다. 실행 중·중단 확인 중이면 409 `retry_not_safe`, 다른 소유자의 작업은 404입니다. 기존 작업 이력을 덮어쓰거나 부분 결과부터 이어서 실행하지 않으며 자동 재시도도 하지 않습니다.


## 플랫폼 서버 연동 계약 v1

### 환경과 인증

| 뇌대리 설정 | 의미 |
|---|---|
| `NOEDAERI_PLATFORM_API_KEY` | 플랫폼 → 뇌대리 요청 전용 키. `X-Noedaeri-API-Key` 헤더로 전달 |
| `NOEDAERI_PLATFORM_WEBHOOK_URL` | 뇌대리 → 플랫폼 완료 수신 주소. 고정 HTTPS URL, 사용자 정보·쿼리·fragment 금지 |
| `NOEDAERI_PLATFORM_WEBHOOK_SECRET` | 완료 알림 HMAC-SHA256 공유 비밀. 요청용 키와 분리 |
| `PLATFORM_RESULT_TTL_SECONDS` | 미확인 결과 최대 보관, 기본 604800초(7일), 설정 범위 1시간~30일 |

실제 값은 SOPS 암호화 env로 관리합니다. `PLATFORM_API_KEY`는 기존 플랫폼을 호출하는 키로 위 전용 키와 다른 값입니다. 키는 브라우저에 넣지 않습니다. 플랫폼 요청 키는 플랫폼 작업과 플랫폼 경로의 Raya 판단에 접근하고 웹 사용자 작업·관리자 API에는 접근하지 못합니다. AI 기능도 이 기존 요청 키를 재사용합니다. 키 변경은 암호화 설정 변경 및 재시작으로 즉시 교체하며 이중 키 유예는 제공하지 않습니다.

요청마다 콜백 URL을 받지 않습니다. 서버 관리자가 설정한 주소만 사용하며 리다이렉트를 따라가지 않고 TLS 인증서를 검증합니다. 내부망에서도 신뢰되는 HTTPS 주소를 사용합니다. 웹훅 주소가 없으면 접수는 503 `platform_delivery_not_configured`로 거절합니다. 키가 없거나 잘못되면 401 `platform_key_required`입니다.

초기화: `.venv/bin/python scripts/configure_platform.py init`은 기존 키를 유지하며 없는 키를 생성합니다. 수신 서버 준비 후 `.venv/bin/python scripts/configure_platform.py webhook`으로 숨김 입력하고 `.venv/bin/python scripts/manage.py restart`로 반영합니다. 키를 플랫폼 서버 비밀 설정에 안전하게 전달해야 하며 채팅·로그·소스에 복사하지 않습니다.

### API와 처리 순서

| 메서드·경로 | 계약 |
|---|---|
| `GET /api/v1/services` | 지원 종류·옵션 스키마 |
| `POST /api/v1/jobs` | 앞의 웹 요청과 같은 JSON. 종류는 image.package / video.thumbnail / video.package. `callback_url` 등 미정 필드는 422 |
| `PUT /api/v1/jobs/{id}/input` | 원본 바이트. 같은 업로드 한도 적용 |
| `GET /api/v1/jobs/{id}` | 개별 상태·결과 manifest·`terminal_event_id`·`delivery`·`received_at` |
| `GET /api/v1/jobs?limit=100` | 최근 플랫폼 작업, 최대 100개. 대량 목록 복구 대신 자체 저장 ID 사용 |
| `POST /api/v1/jobs/{id}/cancel` | 업로드 대기·큐·실행 취소. 실행 중이면 종료 확인까지 기다림 |
| `GET /api/v1/jobs/{id}/result` | 단일 JPEG 또는 통합 ZIP |
| `GET /api/v1/jobs/{id}/files/{name}` | manifest에 기재된 통합 결과 파일, 인증 필요 |
| `POST /api/v1/jobs/{id}/receipt` | `{ "event_id": "<terminal_event_id>" }`, 저장·등록 완료 확인 |

1. 플랫폼 DB에 원본 file ID·변환 generation·새 요청 UUID를 기록하고 접수합니다. 제목에는 비밀값·내부 경로를 넣지 않습니다.
2. 응답의 job ID를 저장하고 원본을 업로드합니다. 같은 접수의 재전송은 동일 요청 키·내용을 유지합니다. 업로드 응답이 유실되면 개별 조회로 `uploading`인지 확인한 후 재전송합니다.
3. 완료 알림은 검증 후 durable inbox에 event ID를 UNIQUE로 저장하고 빠르게 2xx 응답합니다. 실제 다운로드·등록은 별도 작업으로 처리합니다.
4. 성공이면 결과를 다운로드하고 모든 파생물·재생 목록을 등록합니다. ZIP의 경로를 그대로 신뢰해 풀지 말고 manifest 파일명 허용 목록·경로 탈출·압축 해제 크기를 검사합니다. 결과 묶음에는 원본이 없습니다.
5. 플랫폼 DB의 현재 generation과 job ID가 일치할 때만 결과를 반영합니다. 이전 작업의 늦은 알림으로 새 결과를 덮어쓰지 않습니다.
6. 파일 영구 저장과 파생물 등록 트랜잭션이 끝난 뒤 receipt를 보냅니다. 응답 유실 시 같은 receipt를 다시 보내도 됩니다. 성공은 `accepted=true, cleanup=scheduled`이며 실제 파일 삭제 완료를 의미하지 않습니다.

receipt는 즉시 다운로드를 닫고 다음 정리 주기에 결과를 삭제합니다. 동일 확인은 만료 후에도 200, 잘못된 이벤트 ID·실패 작업 확인은 409 `receipt_mismatch`, 확인 전에 자연 만료된 결과는 410입니다. 저장 확인을 보내지 않아도 보관 상한 뒤 만료됩니다. 실패·취소 입력과 중간 파일은 종료 후 정리합니다. 작업 이력과 알림 기록은 파일 정리와 별도로 유지합니다.

### 완료 웹훅

종류는 `job.succeeded`, `job.failed`, `job.cancelled`입니다. 입력 업로드 시간 초과와 중단 후 종료 확인 복구도 실패 알림을 만듭니다. `interrupted`는 종료 미확인이므로 아직 완료 알림을 보내지 않습니다.

```json
{
  "version": 1,
  "event_id": "<고정 이벤트 UUID>",
  "type": "job.succeeded",
  "job_id": "<작업 UUID>",
  "idempotency_key": "<플랫폼 요청 UUID>",
  "occurred_at": "<작업 종료 ISO-8601>",
  "job": {
    "kind": "video.package",
    "status": "succeeded",
    "error_code": null,
    "result": { "type": "video_package", "files": ["<실제 manifest 파일명>"] },
    "expires_at": "<결과 보관 상한 ISO-8601>"
  },
  "job_path": "/api/v1/jobs/<작업 UUID>",
  "result_path": "/api/v1/jobs/<작업 UUID>/result"
}
```

`result`는 위 기능별 전체 manifest입니다. 실패·취소에는 result/result_path가 null입니다. 이벤트 본문은 처음 생성된 값으로 유지되므로 재전송 시 이미 만료됐을 수 있습니다. 최신 상태는 개별 조회로 확인하고 410을 처리합니다.

| 헤더 | 검증 |
|---|---|
| `X-Noedaeri-Event-ID` | 본문 event_id와 같아야 함 |
| `X-Noedaeri-Timestamp` | 전송 시각 Unix 초, 수신 시각과 ±300초 이내 |
| `X-Noedaeri-Signature` | `sha256=` + HMAC-SHA256(secret, timestamp ASCII + `.` + **원본 요청 body 바이트**)의 소문자 hex |

JSON 재직렬화 후 서명을 계산하지 않습니다. 서명을 constant-time 비교하고 크기 상한 128KiB·버전·종류·UUID를 검증한 뒤 처리합니다. 서버 시계를 동기화합니다. 재전송은 같은 event ID·본문에 새 timestamp·서명을 사용합니다. 서명 검증만으로 중복이 제거되지는 않으므로 플랫폼 inbox UNIQUE 제약이 필수입니다.

전송 시도는 요청별 네트워크 단계 제한 5초, 자동 최대 8회입니다. 실패 후 30·60·120·240·480·960·1920초 간격으로 재시도합니다. 2xx만 성공이며 3xx/4xx/5xx·통신 실패 모두 제한적으로 재시도합니다. 프로세스가 수신 성공 직후 종료되면 같은 알림이 다시 도착할 수 있습니다. 응답 본문은 저장·로그 출력하지 않습니다.

웹 관리자는 플랫폼 작업을 조회·취소하고 전달 상태와 시도 수를 확인할 수 있습니다. 실패한 알림의 **완료 알림 다시 전송**은 같은 이벤트를 다시 예약하며 변환을 재실행하지 않습니다. 관리자 API는 `POST /api/admin/jobs/{id}/webhook-retry`이며 웹 세션·CSRF가 필요합니다. 플랫폼 결과 저장 확인 시 알림 상태는 `acknowledged`로 바뀌고 추가 자동 전송을 중지합니다.

### 플랫폼에서 해야 할 일

- API 키·공유 비밀 주입, HTTPS 수신 URL 제공, inbox 중복 제거와 비동기 결과 수령 구현.
- 기존 파생물 등록·generation 조건부 반영·HLS 상대 경로 매핑 구현. 480/720/1080, 현재 파일명과 manifest를 수용.
- 원본 저장·파일 권한·Range·원본 보기·PDF/TXT/Markdown/오디오 원본 뷰어·OG/공유 URL은 플랫폼에서 유지.
- 실제 모바일 HEIC·회전 영상·무음·긴 영상·큰 파일로 기존 뷰어와 대조하고 기능 플래그로 단계 전환.

현재 뇌대리 측 검수와 실제 플랫폼 연결 성공은 구분합니다. 모의 수신 검사는 플랫폼의 실제 파일 등록·뷰어·도메인 라우팅 검수를 대체하지 않습니다.


## 대용량 입력과 플랫폼 복구 책임

공통 작업 입력 업로드 한도는 **5GiB (5,368,709,120바이트)**입니다. 영상·첨부 원본을 플랫폼이 저장하는 한도도 플랫폼에서 같은 값으로 설정해야 합니다. 뇌대리는 일반 첨부 저장소가 아니며 등록된 image.package/video.thumbnail/video.package 종류만 처리합니다. 이미지 디코딩은 별도의 32MB 한도를 유지해 큰 이미지의 자원 폭증을 막습니다. 업로드 허용이 모든 종류의 변환 성공을 의미하지 않습니다.

기본 임시 총량은 20GiB이고 UPLOAD_MAX_BYTES/STORAGE_MAX_BYTES로 설정합니다. 실제 디스크 여유·입력 및 출력 예약 검사를 통과해야 접수합니다. 로컬 nginx 한도도 같은 설정을 사용합니다. 외부 Nginx Proxy Manager·플랫폼 업로드 프록시에는 5GiB 이상의 client_max_body_size와 대용량 전송에 맞는 제한시간을 별도로 적용해야 합니다. 원본 전송의 바이트 위치부터 재개는 지원하지 않습니다.

| 상황 | 뇌대리 제공 | 플랫폼에서 반드시 구현 |
|---|---|---|
| 완료 웹훅 중복 | 고정 event_id, 서명, 제한 재전송 | inbox event_id UNIQUE, 중복 수신 2xx, 결과 반영 멱등성 |
| 결과 다운로드 실패 | receipt 전 보관 기한 내 재다운로드 가능. 미완성 다운로드를 지우는 Python 예제 | 수령 작업의 제한 재시도·상태 기록, 응답 유실 조회 복구. 저장 성공 전 receipt 금지 |
| 원본이 삭제됨 | 작업 취소 API. 이미 완료된 결과는 플랫폼 저장 확인까지 보관 | 반영 직전 원본 존재·삭제 상태를 확인하고 삭제된 파일에 파생물을 다시 붙이지 않음 |
| 이전 변환이 늦게 완료 | 새 작업 ID·고정 이벤트 ID로 이전 작업 구분 | 현재 file generation과 job ID를 같은 트랜잭션에서 대조해 오래된 결과 반영 차단 |

원본 삭제·세대 변경은 뇌대리에 자동 전달되지 않습니다. 플랫폼은 삭제 시 진행 작업을 취소하고, 성공 알림이 먼저/나중에 도착하더라도 조건부 등록으로 막아야 합니다. 수령 실패 복구와 실제 파일 등록의 중복 방지는 아직 플랫폼에서 실행 검증하지 않았습니다.
