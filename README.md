# 뇌대리

네이티브 PC에서 연산 작업을 실행하고 웹에서 큐·작업·워커·접근 승인을 관리하는 초기 구현이다. Docker를 사용하지 않는다.

현재 실행 서비스는 이미지 통합 처리, FFmpeg 영상 썸네일과 통합 영상 처리다. 이미지 통합 처리는 JPEG 썸네일·WebP 미리보기·메타데이터 ZIP을 생성한다. 통합 처리는 원본 범위에서 480p·720p·1080p HLS, 썸네일과 다운로드 ZIP을 생성한다. 웹에서 자동·수동 화질 선택으로 재생할 수 있다. 공통 작업은 파일을 전제로 하지 않으며, 서비스별 입력·옵션·결과를 분리한다. [구조와 구현 범위](docs/architecture.md)를 참고한다.

## 연동 지침

승인된 사용자에게 웹의 **연동 지침** 메뉴를 제공한다. [원본 Markdown](docs/SERVICE_INTEGRATION.md)을 같은 내용으로 표시하고 복사·다운로드할 수 있다. 현재 웹 사용자 세션 API와 플랫폼 전용 키 API를 구분한다. 기능 변경 시 이 문서도 함께 갱신한다.

## 개발 환경

Python 3.12 이상, uv, Node.js, PostgreSQL 17, FFmpeg/ffprobe, nginx, SOPS와 age가 필요하다.

```sh
uv sync --locked
npm ci --prefix web
npm run build --prefix web
```

개인키는 저장소 밖 기본 SOPS 위치에서 읽는다. `config/platform.enc.env`는 플랫폼 설정, `config/n8n.enc.env`는 별도 연동 키, `config/ai-providers.enc.env`는 AI 공급자 키(`OPENROUTER_API_KEY`·`GROQ_API_KEY`·`GEMINI_API_KEY`·`MISTRAL_API_KEY`)다. 공급자 키는 아직 실행 경로에 연결되지 않았다. 실제 값을 문서·예제·소스에 붙여 넣지 않는다.

## 네이티브 운영

운영 DB와 암호화된 `config/runtime.enc.env`를 초기화하고 macOS launchd에 DB·API·워커·nginx를 등록했다. 실제 경로·연결 정보는 암호화 설정에서 읽는다. DB는 소유자 전용 디렉터리의 Unix 소켓만 사용하며 API는 loopback, 외부 진입은 nginx가 담당한다.

```sh
.venv/bin/python scripts/manage.py status
.venv/bin/python scripts/manage.py start
.venv/bin/python scripts/manage.py stop
.venv/bin/python scripts/manage.py restart
```

macOS 사용자 로그인 시 자동 시작하며 프로세스 종료 시 다시 기동한다. 로그인 전 부팅 단계의 서비스는 아니다. `stop`은 현재 실행을 중지하고 DB와 설정을 보존한다. 다음 로그인에는 다시 시작한다. 코드 변경 후 웹을 빌드하고 `restart`한다.

통합 영상 처리는 별도 입력 없이 기본 `auto`로 실행한다. macOS에서는 VideoToolbox 하드웨어 디코딩·H.264 인코딩을 우선 시도하고, 가속 변환 실패 시 미완성 해상도별 출력을 정리한 뒤 CPU로 한 번 전환한다. 다른 OS는 CPU로 실행한다. 스케일링·썸네일은 CPU를 사용한다. 취소·시간 초과·저장 공간 부족은 재시도하지 않으며 전체 제한시간을 새로 시작하지 않는다.

관리자가 방식을 고정해야 할 때만 암호화 운영 설정의 `FFMPEG_VIDEO_ENCODER`를 `auto`, `libx264`(CPU), `h264_videotoolbox`(하드웨어 전용) 중 하나로 지정한다. 예를 들어 CPU 고정은 아래 명령으로 암호화 저장한다. 워커가 작업 중이면 완료를 기다린 뒤 재시작해 적용한다.

```sh
sops set --input-type dotenv --output-type dotenv config/runtime.enc.env '["FFMPEG_VIDEO_ENCODER"]' '"libx264"'
```

하드웨어 전용 설정은 자동 CPU 전환을 하지 않는다. 결과에 실제 인코더와 전환 여부를 기록하고 웹에 표시한다. 같은 저장 루트의 하드웨어 작업은 하나씩 실행하며 대기·취소·제한시간을 적용한다. 실제 macOS 하드웨어 검사는 `NOEDAERI_TEST_VIDEOTOOLBOX=1 .venv/bin/python scripts/check.py`로 실행한다. 기본 검사는 하드웨어 전용 검수 항목을 건너뛴다.

새 환경에서만 `scripts/manage.py init --state-dir <영속_경로> --port <진입_포트>`로 초기화한다. 기존 설정이나 비어 있지 않은 디렉터리는 덮어쓰지 않는다. PostgreSQL 17과 nginx가 설치되어 있어야 한다. 생성한 운영 설정은 해당 PC 전용이므로 다른 PC에서 그대로 시작하지 않는다.

실행기는 메모리에서 복호화하여 프로세스에 전달한다. 평문 env는 만들지 않는다. nginx 설정과 launchd 등록 파일은 Git 밖에서 소유자 전용 권한으로 관리한다. 현재 stdout·stderr 및 nginx 접근 로그는 저장하지 않는다. 상세 운영 로그와 회전 정책은 후속 범위다.

API는 빌드된 `web/dist`를 함께 제공한다. 초기 관리자 설정이 비어 있으면 로그인 후 승인 대기만 허용한다. 실제 사용자 로그인과 관리자 연결은 아직 검증하지 않았다.

키 숨김 입력창을 다시 열려면:

```sh
.venv/bin/python scripts/configure_secret.py --config config/n8n.enc.env --key NOEDAERI_API_KEY
```

## n8n 분기 초안

n8n의 AI 작업 분기는 [워크플로 JSON](examples/n8n_ai_routing_sample.json)을 가져와 확인할 수 있다. 수동 실행으로 8개 작업과 미등록 작업을 분기하며, 각 작업의 `instruction`은 추후 지침을 입력하도록 비워 두었다. Raya CPU 추론 뒤 L1·L2·L3로 분기한다. L3→L2→L1→Mistral 폴백→L3 순환과 모두 소진 시 토큰 없음 안내로 연결할 틀을 제공한다. 실제 한도 조회·AI 공급자 호출·학습 데이터 수집·파인튜닝은 후속 범위다. 가져오기용 JSON에는 예시 주소만 있으므로 서버 주소와 전용 Header Auth Credential을 선택한다. [연동 계약](docs/SERVICE_INTEGRATION.md#raya-난이도-판단-api)을 참고한다.

### Raya 설치와 실행

Raya는 API의 Python 환경과 분리한 Python 3.12 환경에서 공식 Laya + ONNX Runtime으로 실행한다. 모델은 `models/raya/`에 저장하며 Git과 임시 파일 정리 대상에서 제외한다. 설치 버전은 별도 lockfile로 고정하고, 모델 리비전과 공식 SHA256을 확인한다.

```sh
uv sync --project raya_runtime --locked --python 3.12
raya_runtime/.venv/bin/python scripts/install_raya.py
```

암호화 운영 설정에서 `RAYA_ENABLED=1`, 32자 이상 전용 `NOEDAERI_RAYA_API_KEY`를 설정하고 API를 재시작한다. 키는 플랫폼·워커·n8n 관리 키와 분리한다. n8n 연결 설정의 `N8N_ORIGIN`, `N8N_RAYA_WORKFLOW_ID`와 관리 API 키 `NOEDAERI_API_KEY`는 `config/n8n.enc.env`로 관리한다. 기존 비활성 초안에 아래 명령으로 추론 단계를 연결하며, 만든 n8n Credential의 ID도 암호화 저장한다. 원본 작업 지침과 샘플 입력은 보존한다. 활성 워크플로는 수정하지 않는다.

```sh
.venv/bin/python scripts/connect_raya_n8n.py
```

추론 요청이 들어오면 자격증명을 상속하지 않는 별도 프로세스로 모델을 로딩한다. 기본 최소 유지 60초·유휴 해제 300초이며, 관리자 **Raya** 메뉴에서 시간 정책 저장·메모리 해제·상태 조회·한국어 요청 테스트를 제공한다. 저장한 시간 정책은 DB에 영속 보관하고 환경 기본값보다 우선한다. 대기 5초·로딩 포함 제한 90초·로딩 전 여유 메모리 3GiB는 암호화 운영 설정으로 조정한다. 다른 서비스와의 시스템 메모리 예약 통합은 아직 없으며 OS의 실제 여유 메모리를 확인한다. 요청·결과를 학습 데이터로 자동 저장하지 않는다.

## 검사

### TTS 설치와 목소리

Apple Silicon에서 Qwen3-TTS 1.7B 8bit를 별도 MLX 환경으로 실행한다. 기본 목소리는 CustomVoice,
참조 음성은 Base 모델을 사용한다. 두 모델은 약 6.2GB이며 모델·가상환경은 Git에서 제외한다.

```sh
uv sync --project tts_runtime --locked --python 3.12
tts_runtime/.venv/bin/python scripts/install_tts.py
```

암호화 운영 설정에서 `TTS_ENABLED=1`을 적용하고 API·워커를 재시작한다. **TTS·목소리**에서
목소리 등록·이름 수정·삭제·참조 음성 재생·선택한 목소리로 음성 생성을 제공한다.
참조 음성 등록과 생성은 기존 Job 큐를 사용하고 **작업**에서 상태·취소·결과를 관리한다.
기본 목소리 선택은 연산이 없어 즉시 등록된다. 플랫폼 호출 계약은 [연동 지침](docs/SERVICE_INTEGRATION.md)을 따른다.

목소리 메타데이터는 DB, 정규화한 참조 음성은 공통 `SERVICE_STORAGE_ROOT` 아래
`tts/voices/<voice_id>/reference.wav`에 보관한다. 향후 STT·OCR도 `stt/`, `ocr/` 등
서비스별 폴더와 용도별 하위 폴더를 사용한다. `VOICE_STORAGE_ROOT`로 TTS 경로만 재정의할 수 있다.
공통 루트 미설정 시 영속 상태 루트를 쓰며 임시 저장 루트와 겹치면 기동을 거부한다.
등록된 목소리는 삭제까지 유지하고 웹에서 생성한 결과 WAV는 24시간 뒤 정리한다.
운영 NAS의 실제 경로·접속 정보는 암호화 설정에만 둔다. `SERVICE_STORAGE_MOUNT_ROOT`로
필수 마운트를 지정하며 연결 해제 시 로컬 경로에 대신 저장하지 않는다.

`SERVICE_STORAGE_SMB_*`를 설정하면 `manage.py start`가 Swift NetFS 도우미를 영속 상태
루트에 컴파일하고 NAS 연결 LaunchAgent를 설치한다. Mac 사용자 로그인 후 기동하고
30초마다 연결을 확인해 재연결하며 한 번의 연결 시도는 30초로 제한한다. 비밀번호는
메모리와 표준 입력으로만 전달한다. 실제 재부팅·로그인 전 실행은 검수하지 않았다.
추가 서비스의 영속 데이터 보존 기간은 기능별로 명시하며 NAS에 있다는 이유만으로 영구 보존하지 않는다.

파일·TTS 워커는 같은 로컬 연산 슬롯을 사용한다. 모델은 매 작업에서 로딩하고 종료 시 프로세스를
회수한다. 기본 대기와 생성 제한은 각각 600초이며 MLX 메모리 목표는 6GiB다.
`TTS_MEMORY_LIMIT_BYTES`는 MLX 할당기의 목표값으로 프로세스 전체 메모리의 강제 상한은 아니다.
다른 실행 경로와의 전역 자원 예약·모델 유지 및 유휴 시간 관리 확대는 후속 작업이다.

```sh
RUN_TTS_SMOKE=1 .venv/bin/python scripts/check.py
node scripts/ui-tts-check.cjs
```

실제 MLX 검사는 기본 목소리 생성 → 생성한 테스트 음성을 참조로 등록 → Base 음성 생성까지 확인한다.
임시 DB와 테스트 경로를 사용하며 운영 플랫폼으로 완료 알림을 보내지 않는다.

### 공통 검사

```sh
.venv/bin/ruff check src tests scripts/run.py scripts/check.py scripts/configure_secret.py scripts/manage.py
.venv/bin/python scripts/check.py
npm run lint --prefix web
npm run build --prefix web
```

DB 검사는 로컬 소켓만 사용하는 일회성 PostgreSQL을 띄운 뒤 삭제한다. 실제 FFmpeg 영상 생성·썸네일 출력, 워커 HTTP 실행, 멱등성·권한·점유·취소·만료 처리를 검사한다. UI 검사는 Playwright 설치 환경에서 `node scripts/ui-check.cjs`로 실행하며 개발 서버가 필요하다. UI 검사의 여러 서비스는 명시된 가상 데이터이며 실제 구현 서비스 목록이 아니다.

관리 화면의 ‘작업’은 파일·AI·색인·직접 연산 이력을 통합하며 서비스·상태 필터와 페이지 이동을 제공한다. 토큰 사용량과 임베딩·RAG는 별도 목적의 화면으로 유지한다. 통합 조회·권한·등록부는 `tests/test_tasks.py`, 화면 경로는 `node scripts/ui-tasks-check.cjs`로 검사한다. 직접 연산의 기존 동기 응답은 유지하며 요약 이력을 기록한다. 전역 실행 큐·자원 배정의 통합은 후속 작업이다.

`web/tools/oxlint/anti-slop`은 프로젝트에 보관한 원본 플러그인이다. 원본 출처와 의도적인 변경 사항은 해당 디렉터리의 UPSTREAM.md에 기록한다.

플랫폼 이관 준비 상태는 [기능 대응표](docs/PLATFORM_COMPATIBILITY.md), 다음 주부터 진행할 전체 순서는 [다음 작업표](docs/NEXT_WORK.md)를 참고한다.


### 플랫폼 전용 연동

전용 키 API·서명 완료 웹훅·저장 완료 확인을 제공한다. 계약은 [연동 지침](docs/SERVICE_INTEGRATION.md), 서버 예제는 [platform_client.py](examples/platform_client.py)에 있다. 공개 진입점은 `/integrations/SERVICE_INTEGRATION.md`, API 명세는 `/integrations/openapi.json`이다.

`.venv/bin/python scripts/configure_platform.py init`으로 없는 전용 키를 암호화 생성한다. 플랫폼의 HTTPS 수신 서버가 준비되면 `.venv/bin/python scripts/configure_platform.py webhook`을 실행해 숨김 입력하고 네이티브 서비스를 재시작한다. 실제 값은 코드·Git 평문에 넣지 않는다. 수신 주소가 없으면 플랫폼 작업 접수는 503이며 웹 테스트는 계속 사용할 수 있다.
