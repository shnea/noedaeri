# 플랫폼 연결 인계

기준일: 2026-10-10. 이 문서는 뇌대리에서 제공하는 API·클라이언트·완료 알림을 플랫폼에
반영하기 위한 순서다. 플랫폼 저장소 수정·배포·권한 발급은 포함하지 않는다.
정확한 요청·한도·응답은 [서비스 연결 지침](SERVICE_INTEGRATION.md)을 따른다.

## 연결할 기능

| 기능 | 접수 | 입력과 주요 옵션 | 결과 |
|---|---|---|---|
| 이미지 파생물 | `POST /api/v1/jobs`, `image.package` | upload, 원본 포맷 확장자 | 썸네일·미리보기·OG·manifest ZIP |
| 영상 파생물 | 같은 경로, `video.package` | upload, 플랫폼의 해상도 설정 | 썸네일·해상도별 HLS·manifest ZIP |
| 영상 썸네일 | 같은 경로, `video.thumbnail` | upload, `seconds` | JPEG |
| TTS | 같은 경로, `tts.synthesize` | text, language·requester_id·선택 voice_id | 단일 `speech.wav` + 웹훅·작업 조회 JSON의 합성 정보. ZIP 없음 |
| 목소리 등록 | `POST /api/v1/voices` | requester_id·project·environment, 프리셋 또는 등록 음성 | 프로필. 복제 음성의 참조 파일은 서비스 NAS에 보존 |
| STT | `POST /api/v1/jobs`, `stt.transcribe` | upload, language·use_itn | 전사 JSON/TXT ZIP |
| 이미지 OCR | 같은 경로, `ocr.recognize` | upload, language·language_correction | OCR JSON/TXT ZIP |
| PDF | 같은 경로, `pdf.extract` | upload, mode=auto/text/ocr·language·language_correction | 문서·페이지별 JSON/TXT ZIP |
| 영상 자막 | 같은 경로, `video.subtitles` | upload, language·use_itn | 전사 JSON/TXT·SRT·VTT ZIP |
| 문장 번역 | `POST /api/v1/translations` | text·source_language·target_language·request_id·project·environment | AI Job JSON·번역 TXT |
| 기존 AI 작업 | `POST /api/v1/ai/jobs` | task_type·prompt·input·request_id·project·environment | AI Job JSON·사용량 |

목소리 목록은 `GET /api/v1/voices`, 상세·이름 수정·삭제는
`GET/PATCH/DELETE /api/v1/voices/{voice_id}`를 사용한다. 목소리 API의 기본 경로는
복수형 `/api/v1/voices`이며 `/api/v1/tts/voice`·`/api/v1/tts/voices`·`/api/v1/voice`는 제공하지 않는다.

TTS 성공 웹훅의 `result_path`(`GET /api/v1/jobs/{id}/result`)는 `audio/wav`인
`speech.wav`의 원본 바이트를 반환한다. ZIP으로 해제하거나 JSON으로 파싱하지 않는다.
합성 시간·샘플레이트·사용 목소리 등은 웹훅의 `job.result` 또는 작업 조회 JSON의 `result`에서
읽는다. 음성과 필요한 메타데이터를 플랫폼에 영속 저장한 뒤 기존 파일 Job의 `receipt`를 보낸다.
TTS에는 `/files/speech.wav` 경로를 사용하지 않는다.

파일 기반 요청은 접수 ID를 저장한 뒤 `PUT /api/v1/jobs/{id}/input`으로 원본 바이트를 보낸다.
원본은 플랫폼이 보관하고 권한을 확인한다. 결과를 새 플랫폼 원본으로 등록할 필요는 없으며,
기존 파일의 현재 변환 세대에 해당하는 파생물 또는 업무 결과로 등록한다.

기본 파일 업로드 한도 5 GiB, 이미지/OCR 200 MB, PDF 200 MB·100페이지다. 유효 한도는
`GET /api/v1/services`에서 읽는다. 설정으로 변경 가능하며 픽셀·처리 시간·여유 공간 제한은
바이트 제한과 별개다. PDF와 SRT/VTT는 자막 번역·영상 자막 입히기 기능을 포함하지 않는다.
자막 시각은 VAD 구간에 비례한 근삿값이며 단어별 강제 정렬이 아니다.

## 키와 설정

| 방향 | 인증·설정 | 담당 |
|---|---|---|
| 플랫폼 → 뇌대리 | HTTPS origin + `X-Noedaeri-API-Key` | 뇌대리 발급 전용 요청 키를 플랫폼 서버 비밀 설정에 저장 |
| 뇌대리 → 플랫폼 파일 완료 수신 | `NOEDAERI_PLATFORM_WEBHOOK_URL` + 서명 비밀 | 기존 파일 수신기. `job.*` 이벤트 |
| 뇌대리 → 플랫폼 AI 완료 수신 | `NOEDAERI_PLATFORM_AI_WEBHOOK_URL` + 같은 서명 비밀 | 별도 AI 수신 경로. `ai.job.*` 이벤트 |
| 뇌대리 → 플랫폼 자체 AI API 검수 | `PLATFORM_API_KEY` + `X-Platform-Key` | 플랫폼이 발급. 현재 AI 권한 부족으로 실제 검수 403 |
| 뇌대리 → n8n | 기존 서버 연결 및 실행 설정 | 뇌대리가 담당. 플랫폼에 n8n 관리 키나 임시 연산 토큰 전달 금지 |

실제 주소·키·프로젝트/환경 식별자는 문서에 복제하지 않는다. 뇌대리는 SOPS + age 암호화
설정으로 저장한다. 플랫폼에는 플랫폼의 비밀 설정 방식을 사용한다. 사용자가 제출한
project/environment/requester_id로 권한을 판정하지 말고, 인증된 서버 문맥에서 확정한다.
플랫폼은 사용자·업무별 소유권을 검증하고 승인된 요청만 전용 서버 키로 전달한다.

## 파일 결과 반영

1. 업무·원본 파일·변환 세대·멱등 UUID를 먼저 영속 저장한다. HTTP 응답 유실 후 새 UUID로
   무조건 다시 접수하지 않는다. 같은 본문·UUID로 기존 작업 ID를 복구한다.
2. 원본 업로드는 자동 무한 재시도하지 않는다. 응답이 불확실하면 작업을 한 번 확인해
   아직 uploading일 때만 재전송한다. queued/running 입력을 덮어쓰지 않는다.
3. 완료 수신은 원문 바이트의 HMAC 서명·5분 시각 허용치·헤더/본문 event_id 일치를 검증한다.
   event_id와 본문 해시를 영속 inbox에 저장한 후 빠르게 2xx로 응답한다. 같은 ID의 다른
   본문은 거부하고, 후속 다운로드·등록은 별도 내부 작업에서 재시도한다.
4. 다운로드 파일 수·총량·이름·ZIP 경로와 manifest를 검증한다. 실패하면 부분 파일을 지우고
   같은 완료 이벤트·같은 작업에서 다운로드를 재시도한다. 변환 자체를 새로 실행하지 않는다.
5. 원본 삭제 여부와 현재 변환 세대를 다시 대조하고 조건부 트랜잭션으로 결과를 반영한다.
   삭제된 파일이나 이전 세대 결과는 반영하지 않는다. 임시 저장물을 정리한다.
6. 저장·파생물 등록이 영속 반영된 뒤 `/api/v1/jobs/{id}/receipt`에 event_id를 보낸다.
   이 확인은 멱등이며 결과 삭제를 예약한다. 수신 2xx와 결과 수령 확인은 다른 단계다.

## AI·번역 결과 반영

`POST /api/v1/translations`는 202와 `pending`을 반환한다. 기존 `/api/v1/ai/jobs`는 200이며
`sync:false`이면 같은 대기 상태다. 플랫폼 어댑터는 pending/running/succeeded/failed/cancelled를
모두 처리해야 한다. 번역은 source_language/target_language만 input에 넣고 RAG용 collection을
자동 추가하지 않는다. 기존 플랫폼의 task 허용 목록에 `text.translate`를 추가해야 한다.

반복 조회 대신 `notify:true`를 선택하고 AI 수신 URL을 먼저 설정한다. 미설정이면 접수를
503으로 거부한다. AI 이벤트에는 원문·번역문·공급자 응답이 없으며 request_id·project·environment·
task_type·job_id로 업무와 대조한다. 파일 이벤트와 같은 서명/중복 제거를 적용하되 타입은
`ai.job.succeeded`, `ai.job.failed`, `ai.job.cancelled`다. 기존 파일 수신기로 보내지 않는다.

성공 이벤트를 받은 뒤 `result_path`를 한 번 조회하고 원문 업무의 현재 세대와 소유권을 다시
검증해 결과를 영속 반영한다. 성공 등록 후 `receipt_path`에 event_id를 보낸다. 결과 본문을
수신하기 전에 receipt를 보내면 복구할 수 없다. 실패·취소 이벤트에는 receipt가 없다.
`notify:false`는 기존 동기 호출·직접 테스트를 위한 방식이며 자동 완료 알림을 제공하지 않는다.
원격 모델 타임아웃·실행 종료 불확실 상태를 새 작업으로 자동 반복하지 않는다.

## 서버 클라이언트 사용 예

공개 Python 예제를 `platform_client.py`로 저장하고 호스트의 비밀 환경 설정으로 호출한다.
아래 값은 모두 자리표시자이며 실제 원본은 플랫폼이 권한 확인 후 제공한다.

```python
from pathlib import Path
from uuid import uuid4
from platform_client import PlatformClient

client = PlatformClient(origin, request_key)  # 호스트 비밀 설정에서 읽음
try:
    job = client.create(kind="pdf.extract", title="PDF 추출", request_id=uuid4(),
                        extension="pdf", options={"mode": "auto", "language": "ko"})
    # 접수 ID를 업무에 영속 기록한 뒤 원본을 업로드한다.
    client.upload(job["id"], Path(source_file))
    # 완료는 서명 웹훅으로 받고 ZIP 저장·등록 후 client.receipt(...) 호출.
    translated = client.translate(request_id=new_work_id, text=source_text,
                                  target_language="en", project=trusted_project,
                                  environment=trusted_environment, notify=True)
    # AI 웹훅 확인 후 client.ai_job(...), 저장 후 client.ai_receipt(...).
finally:
    client.close()
```

웹훅 서명 검증은 `verify_webhook(raw_body, headers, shared_secret)`으로 한다. 반환된 이벤트의
업무 소유권·현재 세대·중복 inbox 처리는 호스트가 추가해야 한다. 예제는 자동 폴링·자동
재접수·ZIP 추출을 하지 않는다. 다운로드 실패 시 부분 파일을 제거한다.

## 플랫폼에서 남은 실제 검수

- 위 작업 종류별 어댑터·웹 진입점·공통 작업 상태·권한을 반영한다.
- 기존 파일 수신기는 새 파일 kind·manifest를 수용하고 AI 수신기는 별도로 추가한다.
- AI 수신 URL을 뇌대리 암호화 설정에 넣고 양쪽 서명 비밀을 일치시킨다.
- 플랫폼 자체 AI API 사용에 필요한 권한 키는 어댑터 반영 후 발급한다.
- 작은 합성 입력으로 접수→완료→저장→화면 갱신→receipt를 기능별 검수한다.
- 중복 이벤트·다운로드 실패·삭제 원본·변환 세대 변경·만료·서명 위조·권한 거부를 검수한다.

뇌대리 내부 및 모의 수신기 검수는 플랫폼의 실제 연결 성공을 의미하지 않는다.

## 이번 뇌대리 검수 기록

2026-10-10: 실제 PDF/OCR·STT 엔진 선택 검사를 포함한 전체 검사 190개 통과, 5개 선택
검사 생략(TTS MLX·VideoToolbox 선택 검사). Ruff·프런트엔드 lint·빌드·AI 전달 상태의
데스크톱/모바일 UI 검사 통과. 기존 프런트엔드 번들 크기 경고는 남아 있다.
동시 파일/AI 이벤트 수집의 중복 키 충돌을 재현하고 양쪽 outbox를 수정해 재검수했다.
서명·재전송·수령 확인·소유권·만료 원문/결과 정리는 모의 수신기로 확인했다.

운영 API·워커를 작업이 없는 상태에서 재시작하고 HTTPS 공개 인계 문서·클라이언트·OpenAPI,
서비스 준비 상태·최신 웹 번들·워커 heartbeat를 확인했다. 실제 n8n 한국어→영어 번역,
멱등 요청 재사용·통합 작업 조회·TXT 수령도 확인했다.
플랫폼 AI 수신 URL은 비워 두었으며 notify:true는 503으로 새 작업을 만들지 않는 것을 확인했다.
플랫폼 저장소 수정·AI 수신기와 파일 파생물 실제 등록·양쪽 완료 알림 검수는 수행하지 않았다.
