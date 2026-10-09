# Apple Vision OCR 실행 기록

기준일: 2026-10-09. macOS·Apple Silicon 네이티브 실행이며 Docker를 사용하지 않는다.

## 설치와 준비 확인

```sh
python3 scripts/install_ocr.py
```

Xcode Command Line Tools의 Swift 컴파일러로 `scripts/ocr_inference.swift`를 빌드해
Git에서 제외한 `ocr_runtime/bin/`에 실행 파일과 해시 매니페스트를 둔다.
Apple Vision revision 3·정확도 우선 모드를 고정하고 필요한 한글·영문·일본어·중국어 지원을
실제 API로 조회한 뒤 설치한다. 별도 공개 모델 다운로드나 상시 OCR 데몬은 없다.
이 Mac에서는 macOS 26.5·Swift 6.3.2로 빌드했다. 다른 OS/도구 버전은 별도 검수가 필요하다.

암호화 env의 `OCR_ENABLED=1`과 `OCR_TIMEOUT_SECONDS`(기본 120, 30–600초)를 적용한다.
API·워커를 재시작한 뒤 `/api/v1/services`의 `ocr.recognize.available`·`ocr_limits`를 확인한다.
실행 파일·원본 소스 해시가 맞지 않거나 엔진이 없으면 활성화로 표시하지 않고 접수 503으로 거부한다.
소스 변경 또는 OS·도구 업데이트 후 설치 스크립트를 다시 실행한다.
기존 네이티브 워커의 로그인 후 자동 기동을 사용하며 새 launchd 서비스는 추가하지 않는다.

## 실행·저장 범위

- 기존 Job·공통 최상위 실행 1개·네이티브 잠금을 사용한다. 인식 프로세스는 작업별로 실행·종료한다.
- 기존 플랫폼 로그인·승인·소유권과 전용 요청 키를 재사용한다. OCR 전용 인증은 추가하지 않는다.
- EXIF 방향·색상·투명 배경을 보정한 첫 프레임을 최대 4,096px로 축소하고 Vision에 전달한다.
- 입력은 최대 32,000,000바이트·각 축 10,000px·4천만 화소다. 공통 업로드 한도가 더 작으면 그 값을 따른다.
- 중간 PNG와 결과에 80MiB 공간을 예약한다. JSON은 최대 4MiB·10,000줄이다.
- 결과는 TXT·줄별 텍스트/신뢰도/정규화 좌표 JSON·ZIP이다. 전체 텍스트는 DB 이력·로그·웹훅에 넣지 않는다.
- 중간 파일·요청 JSON은 종료 시 제거하고 원본은 기존 작업 정리로 삭제한다. 웹 테스트 결과는 완료 후 24시간,
  플랫폼 결과는 수령 확인 또는 플랫폼 TTL로 정리한다. 이력·만료 상태는 남긴다.
- 영속 OCR 데이터는 아직 없다. 향후 파일 서비스에 부적합한 영속 데이터는 공통 NAS 루트의 `ocr/` 용도별
  폴더를 사용하며 실제 경로는 암호화 env로만 관리한다.
- n8n을 통할 필요가 없는 로컬 문자 인식이다. 후속 문서 분석·번역 오케스트레이션은 별도로 검토한다.

계약·예시·오류·웹훅·보존 규칙은 [서비스 연동 지침](SERVICE_INTEGRATION.md)의 OCR 절을 따른다.

## 검수와 한계

```sh
RUN_OCR_SMOKE=1 RUN_STT_SMOKE=1 .venv/bin/python scripts/check.py
npm run lint --prefix web
npm run build --prefix web
node scripts/ui-ocr-check.cjs
```

네이티브 검수는 macOS Vision 실행과 임시 PostgreSQL·소켓을 허용한 환경에서 진행한다.
브라우저 검사는 로컬 Vite 서버와 설치된 Playwright를 사용하며 플랫폼 인증을 우회하지 않는다.

전체 검사는 **163개 통과·5개 건너뜀**이었다. 건너뛴 항목은 별도 실행을 요구하는
TTS MLX 3개·VideoToolbox 2개다. Python 정적 검사·웹 lint·빌드도 통과했다. 기존 큰 JS 청크
경고와 TestClient 사용 중단 예정 경고는 남아 있다.

1,200×360px의 한국어·영어 2줄 테스트 이미지를 별도 네이티브 실행으로 측정하면 준비·인식·ZIP
총 약 **0.305초**, 인식 프로세스 최대 메모리 약 **73.9MiB**였다. 한 번의 작은 입력 측정이며
다른 입력의 속도·메모리를 보장하지 않는다.

실제 한국어·영어 테스트 이미지를 API에 업로드하고 별도 워커 프로세스가 공통 큐에서 처리해
TXT·JSON·ZIP을 전달하는 경로를 확인했다. 빈 이미지 성공, 방향/투명 배경/축소 전처리,
포맷 불일치·손상 파일 거부, 비활성·엔진 누락, 멱등 접수·권한·인식 단계 취소·시간 제한,
소유권·정리·만료와 격리된 모의 HTTPS 수신기의 완료 웹훅·수령 확인을 검증한다.
브라우저에서는 서비스 진입·업로드 옵션·공통 작업·취소·결과 오류 재시도·50줄 페이지·긴 단어 줄바꿈·
빈 결과·만료·승인 대기와 데스크톱/모바일 표시를 검수한다. UI 결과는 가상 데이터다.

PDF·표 구조·수식·필기·다단 문서 읽기 순서·번역은 제공하지 않는다. 저해상도·작은 글자·
실제 영수증/촬영 문서의 인식 품질, 최대 32MB·4천만 화소 실물 입력, 일본어·중국어 실제 인식 품질,
다른 브라우저·OS·기기와 실제 재부팅은 미검증이다. 플랫폼 OCR 어댑터·실제 결과 저장·완료 웹훅
수신은 플랫폼 작업 이후 양쪽에서 검수한다. 로컬 검사 성공을 플랫폼 연결 성공으로 해석하지 않는다.

Apple API 근거: [문자 인식 요청](https://developer.apple.com/documentation/vision/vnrecognizetextrequest),
[지원 언어 조회](https://developer.apple.com/documentation/vision/vnrecognizetextrequest/supportedrecognitionlanguages()),
[자동 언어 감지](https://developer.apple.com/documentation/vision/vnrecognizetextrequest/automaticallydetectslanguage).

실제 HTTPS 서비스 목록의 OCR 활성화·120초 한도, 연동 지침 버전 21, 새 웹 빌드·HTTP 200과
워커의 최신 연결 상태를 확인했다. 실행 중인 작업이 없는 상태에서 암호화 설정을 검증하고 API·워커만
재시작했다. 플랫폼 OCR 작업은 실제로 발행하지 않았다.
