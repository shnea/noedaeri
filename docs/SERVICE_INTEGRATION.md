# 뇌대리 서비스 연동 지침

문서 버전: 2 · 기준일: 2026-10-05

## 현재 연결 가능한 범위

**영상 처리 기능은 구현되어 있지만, 외부 서버용 API 키 인증은 아직 준비 중입니다.** 현재 아래 API는 플랫폼 로그인과 관리자 승인을 받은 웹 사용자 세션으로만 호출할 수 있습니다. NAS·플랫폼·n8n의 무인 호출에 사용자 쿠키를 복사해서 사용하지 마세요.

이 문서의 경로는 뇌대리 주소 기준 상대 경로입니다. 실제 주소·키·내부 경로는 소스나 문서에 넣지 않고 암호화 설정으로 관리합니다. `WORKER_API_KEY`와 `/internal/` 경로는 뇌대리 실행 워커 전용이며 외부 서비스 연동에 사용하지 않습니다.

| 기능 | 작업 종류 | 현재 상태 |
|---|---|---|
| 영상 썸네일 | `video.thumbnail` | 웹 요청·결과 다운로드 가능 |
| 통합 영상 처리 | `video.package` | 썸네일·해상도별 HLS·ZIP 생성 가능 |
| 외부 서비스 인증·요청 | 미정 | 서비스별 키·권한 API 준비 중 |
| 파일 서비스에 결과 등록 | 미정 | 호출 측 연결 및 등록 계약 준비 중 |
| TTS·임베딩·n8n 워크플로 실행 | 미정 | 미구현 |

## 플랫폼 기능 대응 현황

플랫폼의 연산 기능을 대응한 뒤 플랫폼 전용 호출을 연결합니다. 현재 기능 대응표는 [플랫폼 기능 조사 문서](https://github.com/shnea/noedaeri/blob/main/docs/PLATFORM_COMPATIBILITY.md)에 정리했습니다.

| 영역 | 현재 판단 |
|---|---|
| 이미지 썸네일·WebP 미리보기 | 미구현, 다음 개발 대상 |
| 영상 썸네일·HLS | 구현됨. 기간·bandwidth·복구 등 계약 보완 필요 |
| 화질 단계 | 뇌대리 480p·720p·1080p 유지. 플랫폼에서 수용하도록 변경 예정 |
| 원본 보기·OG·공유 URL | 플랫폼의 저장·표시 기능으로 유지 |
| 플랫폼 전용 API | 기능 대응 검수 후 연결 |

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

현재 개별 작업 조회 API와 목록 페이지네이션은 없습니다. 최근 목록은 최대 100개이므로 외부 서비스의 대량 연동에는 적합하지 않습니다. 외부 호출 API를 추가할 때 별도 계약을 제공합니다.

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

상태 조회는 간격을 두고 수행합니다. 웹은 5초 간격을 사용합니다. `succeeded`, `failed`, `cancelled`, `interrupted`에서는 자동 조회를 종료하고 결과 또는 실패 원인을 처리합니다. 완료되지 않은 작업의 결과를 먼저 요청하지 마세요.

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

- 썸네일 → 해상도별 변환 → HLS 재생 목록과 ZIP 생성 순서입니다.
- 원본의 짧은 변을 기준으로 480p·720p·1080p 중 가능한 크기만 생성합니다. 작은 영상을 확대하지 않습니다. 480p 미만은 원본 짧은 변의 짝수 크기를 사용합니다.
- 출력은 H.264/AAC, 30fps, 약 6초 단위 MPEG-TS VOD입니다. 오디오 없는 입력도 처리합니다.
- CPU로 해상도별 순차 변환합니다. GPU 변환은 아직 구현하지 않았습니다.
- 모든 단계가 끝나야 결과를 공개합니다. 단계별 부분 성공·이어하기는 아직 지원하지 않습니다.

`stage`에는 `thumbnail`, `encoding_480p` 같은 해상도별 단계, `packaging`, `finished`가 표시됩니다. 실패·취소 시 마지막 단계를 보존합니다. 정확한 백분율 진행률은 아직 제공하지 않습니다.

완료 결과 형식 예시입니다. 실제 파일 목록과 해상도는 원본에 따라 달라집니다.

```json
{
  "type": "video_package",
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
| 입력 | 최대 512MiB, MP4/MOV·Matroska/WebM |
| 영상 | 최대 1시간, 각 변 4096px 이하·850만 픽셀 이하 |
| 실행 제한 | 썸네일 120초, 통합 작업 전체 30분 |
| 동시 실행 | 워커당 한 작업 |
| 결과 보관 | 웹 테스트 완료 후 24시간, `expires_at` 확인 |
| 입력·중간 파일 | 종료 후 정리, 실행 중 파일은 정리하지 않음 |
| 외부 요청 보존 정책 | 미구현. 웹 테스트 정책을 외부 서비스 계약으로 확정하지 않음 |
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
