---
name: 뇌대리
description: 한국어 작업 운영 화면의 현재 구현 디자인 시스템
colors:
  green: "#174e43"
  ink: "#152d28"
  muted: "#536a62"
  line: "#cedcd5"
  focus: "#136ea2"
  paper: "#fff"
  canvas: "#f2f7f4"
  selected: "#e8f3ee"
  button-hover: "#e5eee9"
  primary-hover: "#216653"
  detail: "#f7faf8"
  table-heading: "#e9f1ed"
  result-surface: "#eaf1ed"
  danger: "#9d2929"
  neutral-status-bg: "#edf1ee"
  neutral-status-ink: "#465e50"
  running-bg: "#e4f1ff"
  running-ink: "#155d9a"
  waiting-bg: "#fff1d5"
  waiting-ink: "#805100"
  success-bg: "#dff3e5"
  success-ink: "#23613d"
  failed-bg: "#fce8e6"
  failed-ink: "#9b3030"
typography:
  headline:
    fontFamily: "-apple-system, BlinkMacSystemFont, \"Apple SD Gothic Neo\", \"Noto Sans KR\", sans-serif"
    fontSize: "36px"
    fontWeight: 700
    letterSpacing: "-0.025em"
  title:
    fontFamily: "-apple-system, BlinkMacSystemFont, \"Apple SD Gothic Neo\", \"Noto Sans KR\", sans-serif"
    fontSize: "22px"
    fontWeight: 700
  section-title:
    fontFamily: "-apple-system, BlinkMacSystemFont, \"Apple SD Gothic Neo\", \"Noto Sans KR\", sans-serif"
    fontSize: "17px"
    fontWeight: 700
  body:
    fontFamily: "-apple-system, BlinkMacSystemFont, \"Apple SD Gothic Neo\", \"Noto Sans KR\", sans-serif"
    fontSize: "16px"
    fontWeight: 400
  table-body:
    fontFamily: "-apple-system, BlinkMacSystemFont, \"Apple SD Gothic Neo\", \"Noto Sans KR\", sans-serif"
    fontSize: "15px"
    fontWeight: 400
  label:
    fontFamily: "-apple-system, BlinkMacSystemFont, \"Apple SD Gothic Neo\", \"Noto Sans KR\", sans-serif"
    fontSize: "14px"
    fontWeight: 400
  status:
    fontFamily: "-apple-system, BlinkMacSystemFont, \"Apple SD Gothic Neo\", \"Noto Sans KR\", sans-serif"
    fontSize: "13px"
    fontWeight: 600
rounded:
  preview: "4px"
  control: "6px"
  container: "8px"
  status: "20px"
spacing:
  compact: "8px"
  label: "10px"
  small: "12px"
  control: "16px"
  row: "20px"
  section: "24px"
  wide: "32px"
  detail-gap: "36px"
components:
  button-primary:
    backgroundColor: "{colors.green}"
    textColor: "{colors.paper}"
    rounded: "{rounded.control}"
    padding: "9px 16px"
  button-primary-hover:
    backgroundColor: "{colors.primary-hover}"
    textColor: "{colors.paper}"
  button-secondary:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "9px 16px"
  button-danger:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.danger}"
    rounded: "{rounded.control}"
    padding: "9px 16px"
  input:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "10px 12px"
  navigation:
    backgroundColor: "{colors.green}"
    textColor: "{colors.paper}"
  status-running:
    backgroundColor: "{colors.running-bg}"
    textColor: "{colors.running-ink}"
    rounded: "{rounded.status}"
    padding: "5px 10px"
  form-container:
    backgroundColor: "{colors.paper}"
    rounded: "{rounded.container}"
    padding: "26px"
  inline-detail:
    backgroundColor: "{colors.detail}"
    padding: "22px 32px"
---
# Design System: 뇌대리

## Overview

**Creative North Star: "공공 도서관 안내 체계"**

짙은 녹색 안내 영역과 옅은 민트 바탕, 넓은 흰색 목록으로 작업을 찾고 상태와 다음 행동을 읽는 한국어 운영 화면이다. 서비스와 상태를 분리하고 결과의 형태가 화면 전체의 정체성을 결정하지 않게 한다.

이 문서는 `web/src/style.css`와 `web/src/App.tsx`의 현재 구현을 추출한 기록이다. 사용자가 범용 작업으로 변경한 실제 화면을 승인하여 현재 구현을 시각 기준으로 확정했다. 기존 이미지 시안의 자동 hero 비교 점수 통과를 의미하지 않는다.

**Key Characteristics:**

- 짙은 녹색 상단 탐색과 민트 계열의 작업 바탕
- 한국어 시스템 산세리프와 숫자 정렬
- 넓은 표에서 같은 행 아래로 펼치는 실행·결과 상세
- 모바일에서는 핵심 필드와 세로 상세를 우선 표시

## Colors

짙은 녹색을 탐색과 주요 행동에 쓰고, 낮은 채도의 민트·흰색 표면으로 작업 정보를 받친다. 정확한 값은 frontmatter가 기준이다.

### Primary

- **안내 녹색** (`green`): 상단 탐색과 주요 실행 버튼. `primary-hover`는 주요 버튼의 호버 상태다.
- **포커스 파랑** (`focus`): 키보드 포커스 표시. 실행 중 상태와 별도 역할이다.

### Neutral

- **깊은 녹색 잉크** (`ink`)와 **보조 잉크** (`muted`): 제목·본문과 설명·시간을 구분한다.
- **민트 바탕** (`canvas`), **흰 종이** (`paper`), **구분선** (`line`): 화면·표·컨테이너의 경계를 정한다.
- **선택 민트** (`selected`)와 **상세 바탕** (`detail`): 선택한 작업과 펼쳐진 상세를 연결한다.
- `table-heading`은 열 제목, `result-surface`는 결과 대기·JSON 영역, `button-hover`는 보조 버튼의 호버 표면이다.

상태색은 `running-*`의 파랑, `waiting-*`의 호박색, `success-*`의 녹색, `failed-*`의 적색을 쓴다. `neutral-status-*`는 취소 등 기본 상태다. `danger`는 취소·접근 철회 동작을 구분한다. 이 색상들은 별도 브랜드 악센트가 아니다.

**The Separate Service and State Rule.** 서비스는 작업 종류를, 상태 색상은 실행·승인 상황을 설명한다. 같은 의미로 합치지 않는다.

## Typography

**Body Font:** frontmatter의 한국어 시스템 산세리프 스택. 운영 화면의 제목도 같은 스택을 사용하며 별도 장식용 display 서체는 없다. Operate 기준에서 허용된 선택이다.

### Hierarchy

- **Headline:** 기본 페이지 제목. 넓은 화면의 작업 제목은 34px, 모바일 작업 제목은 28px로 바뀐다. 접근 안내 제목은 모바일 30px와 줄높이 1.4를 쓴다.
- **Title / Section Title:** 섹션과 상세 영역 제목. 굵기로 정보 계층을 구분한다.
- **Body:** 기본 UI. 설명 문단은 줄높이 1.65, 최대 너비 72ch다. 표는 tabular-nums를 사용한다.
- **Table Body / Label / Status:** 행 내용, 보조 라벨, 상태 표식을 구분한다. 작업명 버튼은 16px·600·줄높이 1.5다.

**The Korean Operation Rule.** 브랜드 표현보다 한국어 작업명·실패 원인·다음 행동의 가독성을 우선한다.

## Layout

상단 메뉴는 최소 높이 64px, 좌우 여백 32px다. 본문은 최대 너비 1600px로 중앙 정렬하며 기본 여백은 36px 32px다. 1100px 이상에서는 필터가 제목 오른쪽에 놓이고 새 작업 버튼의 공간을 남긴다.

작업 목록은 전체 너비 표다. 작업명 열은 32%를 기준으로 하고 나머지 열에 서비스·상태·요청자·워커·요청 시각을 배치한다. 행의 세로 패딩은 12px다. 선택한 작업 아래에 상세가 펼쳐지고 실행·결과를 같은 너비 두 열로 나눈다. 현재 최종 상세 간격은 36px, 패딩은 22px 32px이며 둘째 영역은 왼쪽 경계와 44px 여백을 가진다. 넓은 화면 상세의 최소 높이는 224px다.

760px 이하에서는 상단 메뉴가 줄바꿈되고 본문 좌우 여백이 16px가 된다. 작업 표의 헤더를 숨기고 각 행을 작업명 전체 너비·서비스와 상태의 두 칸으로 바꾼다. 요청자·워커·시각 열은 숨기고 펼친 상세에서 요청 시각과 축약한 요청자·워커 식별자를 표시한다. 미배정 워커는 배정 대기로 표시한다. 작업 상세는 한 열·패딩 22px 16px이며 둘째 영역 위에 구분선과 24px 여백을 둔다. 폼도 한 열이 되고 하단 갱신 정보는 세로로 쌓인다. 이 모바일 행 변환은 작업 표에만 적용된다.

## Elevation & Depth

현재 화면에는 box-shadow가 없다. 상단 탐색의 짙은 면, 흰색 목록, 선택 민트, 옅은 상세 바탕과 1px 구분선으로 정보의 깊이를 표현한다. 포커스는 그림자가 아닌 3px outline과 3px offset으로 표시한다.

**The Flat Work Surface Rule.** 그림자 없이 바탕색과 가는 구분선으로 탐색·목록·상세를 나눈다.

## Shapes

컨트롤과 작은 표면은 `control`, 목록·폼·빈 상태 컨테이너는 `container` 모서리를 쓴다. 이미지 결과는 `preview`, 상태 표식은 `status`의 둥근 형태다. 탐색 버튼은 직각이고 활성 메뉴에 가는 밑줄을 사용한다. 단계 노드는 원형이며 연결선으로 순서를 표현한다.

## Components

### Buttons

한국어 동작 이름을 가진 단단한 컨트롤이다. 기본 최소 높이는 42px, 굵기는 600이다. 주요 버튼은 안내 녹색, 보조 버튼은 흰색과 경계선, 위험 버튼은 적색 글자와 경계선이다. 호버는 색상 변화이며 비활성은 opacity 0.55와 not-allowed 커서를 사용한다. 공통 키보드 포커스는 파란 outline이다.

### Inputs / Fields

최소 높이 44px, 패딩 10px 12px, 얇은 녹색 회색 경계의 입력과 선택 필드다. 라벨을 함께 제공하며 입력 커서는 안내 녹색이다. 공통 포커스 규칙을 따른다. 현재 오류는 적색 문구와 오류 배너로 전달하며 입력별 오류 테두리는 정의되지 않았다.

### Navigation

브랜드·메뉴·계정 조작이 같은 녹색 띠에 놓인다. 브랜드는 27px·800, 모바일에서는 24px다. 메뉴의 활성 상태는 흰색 글자와 2px 밑줄이고 호버는 더 밝은 녹색이다. 모바일 메뉴는 헤더의 마지막 줄 전체 너비를 차지한다.

### Status Chips

13px·600의 한국어 상태 이름과 currentColor 원점을 함께 표시한다. 대기·입력 대기·승인 대기, 실행, 완료·승인, 실패·확인 필요·거부·접근 철회의 의미를 색상 군으로 구분한다. 조작 버튼이 아닌 정보 표식이다.

### Cards / Containers

목록·새 작업 폼·서비스 목록·빈 상태는 흰 배경과 얇은 경계를 가진다. 새 작업 폼의 기본 패딩은 26px, 모바일은 20px다. 장식용 카드 그리드로 작업 목록을 대체하지 않는다.

### Inline Job Detail

작업명 앞의 SVG 화살표가 펼침 상태를 표시하고 0.18s ease-out으로 90도 회전한다. `aria-expanded`를 제공하며 reduced-motion에서는 transition과 animation을 끈다. 실행 단계와 결과를 한 행 안에서 읽는다. 대기 단계는 대기 상태에서 현재 단계로, 입력 대기에서는 미완료로 표시한다. 실행 단계는 실행 중에 현재 단계로 표시하고 종료 시각과 워커가 함께 있을 때 완료로 채운다. 종료 단계는 성공이면 녹색 완료, 그 외 종료는 회색 노드로 구분한다. 결과는 다운로드·만료 안내·JSON·실제 결과 이미지로 구분한다. 현재 이미지 너비는 180px이며 코드에 장식용 래스터 자산은 없다.

## Do's and Don'ts

### Do:

- Do 서비스명과 작업 상태를 별도 필드로 유지한다.
- Do 색상과 함께 한국어 상태 이름 및 원인·다음 행동을 표시한다.
- Do 키보드 포커스와 reduced-motion 대응을 유지한다.
- Do 실제 작업 결과가 이미지일 때만 결과 미리보기를 표시한다.

### Don't:

- Don't 장식용 래스터 이미지를 작업 결과처럼 배치한다.
- Don't 준비되지 않은 서비스를 실행 가능한 항목처럼 표시한다.
- Don't 실제 상태와 관계없는 진행 백분율을 추가한다.
- Don't 사용자 승인과 자동 이미지 비교 통과를 혼동한다.



2026-10-05: 사용자가 범용 작업으로 변경한 실제 화면을 승인하고 운영 연결 진행을 요청했다. 현재 구현을 시각 기준으로 확정한다. 기존 이미지 시안 자동 비교 점수의 통과를 의미하지 않는다.
