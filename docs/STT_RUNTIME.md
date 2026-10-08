# STT 네이티브 설치와 실행

기준일: 2026-10-09. Docker·n8n 없이 CPU로 실행한다.

```sh
uv sync --project stt_runtime --locked --python 3.12
python3 scripts/install_stt.py
```

`stt_runtime/uv.lock`은 Python 3.12·sherpa-onnx/core 1.13.8·soundfile 0.13.1·numpy 2.4.4를
고정한다. macOS wheel은 core 공유 라이브러리가 필요해 두 패키지를 함께 명시한다.
모델·가상환경은 Git에서 제외한다. 설치 스크립트는 공식 다운로드의 전체 SHA-256을
검증하고 필요한 모델·토큰·샘플만 추출한다. 아카이브 경로를 그대로 풀거나 코드를 실행하지 않는다.

| 자산 | 공식 출처 | SHA-256 |
|---|---|---|
| SenseVoice INT8 아카이브 | [sherpa-onnx 공식 asr-models](https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2) | `7d1efa2138a65b0b488df37f8b89e3d91a60676e416f515b952358d83dfd347e` |
| Silero VAD ONNX | [sherpa-onnx 공식 asr-models](https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx) | `9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6` |

아카이브 약 163MB이며 설치된 모델은 `models/stt/`에 있다. 배포 링크가 바뀌어도 해시가
다르면 설치를 중단한다. sherpa-onnx·SenseVoice 코드·Silero VAD 코드의 라이선스는 각각
[Apache-2.0](https://github.com/k2-fsa/sherpa-onnx/blob/master/LICENSE),
[MIT](https://github.com/FunAudioLLM/SenseVoice/blob/main/LICENSE),
[MIT](https://github.com/snakers4/silero-vad/blob/master/LICENSE)다.
SenseVoice 모델 가중치는 코드 라이선스와 별개로 공식
[모델 카드](https://huggingface.co/FunAudioLLM/SenseVoiceSmall)의
[FunASR Model License](https://github.com/modelscope/FunASR/blob/main/MODEL_LICENSE)를 따른다.
ONNX 변환·INT8 모델에도 원본 모델명·출처를 유지하며 코드 라이선스로 가중치 조건을 대체하지 않는다.

암호화 운영 env에서 `STT_ENABLED=1`을 적용하고 API·워커를 다시 시작한다.
`STT_TIMEOUT_SECONDS=900`, `STT_MAX_DURATION_SECONDS=3600`, `STT_CPU_THREADS=4`가 기본값이다.
현재 값을 서비스 목록의 `limits`에서 확인할 수 있다. 독립 데몬을 추가하지 않고 기존 로그인 후
자동 시작 워커가 STT도 점유한다. 모델 로딩·인식은 별도 프로세스이고 작업 종료 시 해제한다.
CPU 스레드 수는 ONNX 추론 설정이며 프로세스 전체 CPU·메모리의 OS 강제 상한이 아니다.

파일·TTS·AI와 동일한 공통 예약·네이티브 실행 잠금을 사용한다. 실행 중 취소·점유 상실·
시간 초과 시 프로세스 그룹 종료를 확인한 뒤 슬롯을 반환한다. 전처리와 묶음 생성도 총 제한시간에
포함한다. 작은 블록으로 음성을 읽고 VAD 구간은 최대 30초로 나누어 긴 입력을 통째로 RAM에 올리지 않는다.

```sh
RUN_STT_SMOKE=1 .venv/bin/python scripts/check.py
node scripts/ui-stt-check.cjs
```

실제 엔진 검사는 공식 한국어 음성 → 네이티브 워커 HTTP 접수/업로드 → 인식 → TXT/JSON/ZIP,
권한·미등록 파일 차단·임시 입력 정리·결과 만료와 무음·잘못된 파일·길이 초과·취소·시간 한도를 검사한다.
서명 완료 알림·receipt는 임시 DB·모의 HTTPS 수신기로 검사한다. 운영 플랫폼 STT 어댑터는 아직
구현되지 않았으므로 플랫폼 실제 수령 성공과 구분한다. UI 검사 데이터도 가상 데이터다.

한국어 공식 샘플 4.608초의 첫 실행은 전처리·결과 묶음 포함 약 1.9초·최대 RSS 약 0.93GB였다.
48kHz stereo AAC를 포함한 영상도 전처리해 두 한국어 음성 구간을 분리·인식했고 시작·종료 시각을 확인했다.
짧은 샘플 측정으로 긴 음성의 처리 속도나 인식 정확도를 보장하지 않는다. 고유명사·한국어
띄어쓰기·잡음·긴 녹음·다언어 혼합 품질은 실제 사용자 파일로 추가 검수한다.
VAD 시각은 음성 구간 경계이며 단어 정렬·화자 구분·완성된 자막을 제공하지 않는다.

원본·PCM은 임시 경로에만 남기고 작업 종료 후 정리한다. 웹 결과는 완료 후 24시간, 플랫폼 결과는
receipt 또는 플랫폼 TTL에 따라 정리한다. STT 전용 영속 자료는 아직 없어 NAS에 새로 보관하는 데이터가 없다.
요청자별 영속 음성 사전 등 추가 기능이 생기면 공통 NAS 루트의 `stt/` 아래 용도별 보존 정책을 정한다.

2026-10-09 검수: `RUN_STT_SMOKE=1` 전체 검사 156개 통과·5개 제외(TTS MLX 선택 검사 3개,
VideoToolbox 선택 검사 2개). Python/웹 정적 검사·웹 빌드와 STT·통합 작업·TTS 화면 동작 검사 통과.
실제 HTTPS 서비스 목록에서 STT 활성화·한도를 확인했고 연동 지침 버전 20·웹 HTTP 200을 확인했다.
기존 API·워커 자동 시작 서비스에 적용했다. 실제 재부팅과 1시간·5GiB 입력 전송은 별도 검수 대상이다.
