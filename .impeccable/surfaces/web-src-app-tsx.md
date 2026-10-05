---
version: 1
slug: "web-src-app-tsx"
primary_target: "web/src/App.tsx"
related_targets: ["web/src/style.css"]
---

# 작업 관리

Mode: Operate. 첫 화면은 작업 목록과 진행 상태를 보여준다. 사용자가 2번 시안의 상단 메뉴·넓은 목록·행 내부 상세 구성을 선택했다. 이후 파일 중심이 아닌 모든 종류의 연산 작업을 다루도록 수정했다.

Approved comp: .impeccable/mocks/approved.png

## Direction contract

THESIS: 여러 서비스의 작업을 한 목록에서 찾고 실행 상태와 결과를 확인한다. 파일 관리나 변환 전용 화면으로 보이지 않는다.

OWN-WORLD: 공공 도서관 안내 체계의 짙은 녹색 탐색 영역과 옅은 민트 작업 바탕, 넓은 흰색 표, 간결한 한국어 산세리프. 서비스명과 상태를 분리한다.

STORY: 사용자는 작업 종류를 선택해 요청하고 목록에서 자신의 작업을 찾는다. 행을 펼치면 입력·실행·결과를 확인한다. 서비스마다 다른 입력 폼을 사용한다.

FIRST VIEWPORT: 높이 약 60px 상단 메뉴, 좌측 작업 제목, 우측 필터와 새 작업 버튼. 전체 폭 목록에 작업명·서비스·상태·요청자·워커·시각. 상세는 선택 행 아래에 펼쳐진다. 단계 표시는 실행 상태와 연결하며 임의 백분율을 만들지 않는다.

FORM: 4번 공공 도서관 안내 체계, f1786757. 사용자 선택 2번의 상단 메뉴 구성. signature interaction: 선택 행에서 실행 단계를 펼친다. 애니메이션은 상태 확인을 방해하지 않고 reduced-motion을 따른다.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

## 승인된 내용 변경

파일명 열과 예시 이미지 결과는 사용자의 범용 작업 요청으로 변경한다. 실제 결과 이미지는 FFmpeg 결과가 있는 작업 상세에서만 표시한다. TTS·임베딩 예시는 실행 가능한 서비스로 가장하지 않는다. 비로그인·승인 대기는 실데이터 표 대신 권한 상태를 표시한다.


2026-10-05: 사용자가 범용 작업으로 변경한 실제 화면을 승인하고 운영 연결 진행을 요청했다. 현재 구현을 시각 기준으로 확정한다. 기존 이미지 시안 자동 비교 점수의 통과를 의미하지 않는다.
