# 예제 — 풍우래기3 (風雨来記3)

풍우래기3 한국어 패치 프로젝트의 실제 원장(레저) 데이터 **전체 — 912개
파일, 2,589개 행 — 를 브라우저에서 바로 글자 주입으로 실연**하는 정적
데모입니다. 서버 없이 동작합니다.

- GitHub Pages: `https://<host>/jaguk/furaiki3/`
- 로컬: 저장소 루트에서 `python -m http.server 8000` →
  `http://localhost:8000/examples/furaiki3/`

![데모 화면](screenshot.png)

왼쪽 파일 목록(디렉토리 그룹·필터)에서 파일을 고르고, 오른쪽 패널에서
번역을 고치면 Canvas 가 즉시 다시 렌더링합니다. 수정은 브라우저의
**localStorage 에만** 저장됩니다 — 서버·원장에는 아무것도 쓰지 않습니다.
`?file=parts/parts&view=original&zoom=2&boxes=1` 식의 URL 로 초기 상태를
지정할 수 있습니다.

## 구성

| 경로 | 내용 |
| --- | --- |
| `index.html` | 데모 앱 — 원장 해석 스펙을 Canvas 2D 로 렌더 |
| `data.js` | 원장 전체에서 추출한 **해석 완료 스펙** (스타일 병합·규칙 적용 후) |
| `assets/original/` | 게임 원본 이미지 (재조합 파일은 작업 좌표계) |
| `assets/erased/` | 원문 텍스트를 지운 베이스 (`typelet erase` + 손질) |
| `fonts/` | 번들 글꼴 (IBM Plex Sans KR 은 Google Fonts CDN) |
| `compare/` | 실제 파이프라인 산출물의 전후 비교 이미지 |

## 데모 렌더러가 옮겨 온 것

`data.js` 의 행은 `typelet.render.resolve()` 가 해석한 최종 스펙과 같은
값입니다. JS 렌더러는 파이프라인의 그리기 경로를 Canvas 2D 로 다시
구현합니다:

- 펜/베이스라인 정렬 (`text_align`), 4x 슈퍼샘플, 외곽선(stroke)
- `drop_shadow(dx,dy,blur,color)` · 반투명 fill · `rotate(angle=…)`
- `overflow: squeeze` (넘치면 가로만 압축, `squeeze_min` 하한)
- 세로쓰기 · 균등 분배(distribute) · 여러 줄 자동 줄바꿈(flow) · run
  (한 상자를 공유하는 멤버들을 이어 그리기)
- `alpha_clear` (crop 알파를 비우고 새 글자를 새김 — 꼬리 위치는 추출 때
  원본에서 계산해 실음) · `rgb_ink` (알파 먹은 사본에 잉크 RGB 만 —
  `source-atop` 합성; 판정은 추출 때 베이스 평균 알파로 계산)
- post overlay (지도 도로 선화 — `alpha`/`alpha_white` 픽셀 단위 감쇠)

실제 산출물은 [typelet/render.py](../../typelet/render.py)
(PIL/FreeType)가 만들고 게임에 주입되는 것도 그쪽입니다 — 이 페이지는
같은 스펙을 브라우저 글꼴 래스터라이저로 그린 근사입니다 (획 AA·메트릭이
1px 수준에서 다를 수 있습니다). 아래는 실제 산출물의 전후 비교:

| | |
| --- | --- |
| 방면 안내판 | ![](compare/roadguide.png) |
| 스팟명 | ![](compare/spotname.png) |
| 재조합 아틀라스 — 작업은 재조합 좌표계(1280), 저장 시 게임 네이티브(1024)로 복원 | ![](compare/recompose.png) |

## 글꼴

IBM Plex Sans KR(OFL, Google Fonts CDN) 외에 프로젝트가 쓰는 무료 글꼴을
번들합니다: NEXON Football Gothic(넥슨), 학교안심 수쑥깡(교육부),
열린명조(열린 글꼴). 각 글꼴의 라이선스는 배포처 고지를 따릅니다.
