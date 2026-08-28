# 예제 — 브라우저에서 typelet 그대로 주입

[examples/](examples/)에는 실제 한국어 패치 프로젝트의 원장(레저) 데이터로
**이미지 텍스트 주입을 브라우저에서 실연**하는 정적 데모 두 개가 있다.
서버 없이 동작하고, GitHub Pages 로 `…/jaguk/furaiki3/` ·
`…/jaguk/kitae/` 에 서빙된다 (진입점 `…/jaguk/`).

핵심은 **별도 렌더러가 없다**는 것이다. 주입 보기는 이 저장소의
[typelet](typelet/) 패키지 — 파이프라인과 jaguk GUI 가 쓰는 바로 그
`render.py` — 를 Pyodide/WebAssembly + Pillow 로 브라우저에서 그대로
실행한 결과다. 같은 원장, 같은 글꼴 파일, 같은 코드가 도니 산출도 같다
(실측: 파이프라인 산출물과의 픽셀 차이는 FreeType 빌드 차이에 의한
가장자리 AA ±수 계조, 예: 안내판 0.6% 픽셀에서 ±5). 배포 워크플로가
typelet/ 소스를 사이트에 `_typelet/` 로 같이 실어, typelet 이 바뀌면
예제 엔진도 자동으로 따라간다 — 사본이 갈라질 일이 없다.

## 공통 조작

- **보기**: 원본 → erased(텍스트 지운 판) → 주입(라이브). 원본/erased 는
  엔진 없이 뜨고, 첫 주입 보기에서 엔진(Pyodide + Pillow, 수 MB)을 한 번
  내려받는다.
- **번역 편집**: 오른쪽 상자 목록(id · 원문 jp · 번역 ko)에서 ko 를
  고치면 즉시 다시 렌더링된다.
- **상자 편집**: `상자` 토글을 켜면 스테이지의 상자(초록 = text, 자주 =
  crop)를 드래그로 옮기고 8개 핸들로 리사이즈할 수 있다. 행의 id/원문을
  클릭해도 선택된다. run(한 상자를 공유하는 멤버들)은 같이 움직인다.
- 모든 수정은 브라우저 **localStorage 에만** 저장된다 — 서버·원장에는
  아무것도 쓰지 않는다. 행별 ↺ 로 되돌리고, `수정 초기화` 로 전부 비운다.
- `?file=…&view=…&zoom=…&boxes=1` 식 URL 로 초기 상태를 지정할 수 있다.
- 로컬 실행: **저장소 루트에서** `python -m http.server 8000` →
  `http://localhost:8000/examples/furaiki3/` (엔진이 `../../typelet/`
  소스를 읽는다).

## 풍우래기3 (風雨来記3) — [examples/furaiki3/](examples/furaiki3/)

홋카이도 오토바이 여행 게임의 한국어 패치 원장 **전체 — 912개 파일,
2,589개 행**. 물량과 렌더 규칙의 다양성을 보여주는 예제다:

- 메뉴 아틀라스 한 장에 외곽선·반투명 행·세로쓰기가 섞여 있고,
- 방면 안내판 434장은 규칙 그룹 슬롯으로 좌표를 통일한 채 넘치는 지명만
  가로 압축(squeeze)하며,
- 스팟명 465장은 이미지 전체가 글자인 blank 베이스(text-only 묶음),
- 지도는 글자를 그린 뒤 도로 선화를 알파 감쇠해 덮는 post overlay,
- 그 밖에 run(이어 그리기)·flow(자동 줄바꿈)·distribute(균등 분배)·
  rotate·alpha_clear·rgb_ink·재조합 좌표계까지 원장의 규칙 전부가
  그대로 동작한다.

![풍우래기3 데모](examples/furaiki3/screenshot.png)

## 북으로. White Illumination (北へ。) — [examples/kitae/](examples/kitae/)

드림캐스트 연애 어드벤처의 한국어 패치 원장 **전체 — 85개 파일, 511개
행**. overlay 규칙 그룹 중심의 예제다:

- 게임이 공유 베이스 위에 프레임을 얹어 그리는 **overlay 그룹** 47개 —
  왼쪽 목록이 그룹 단위(카테고리 폴더로 접힘)이고, 멤버를 열면 그룹
  base 를 밑판으로 깔아 게임에서 보이는 모양대로 확인한다.
- erased 가 없는 same_pattern 멤버는 base 의 지운 판을 공유하고,
  글자 없는 순수 배경(base)은 목록에서 숨긴다(hide_base).
- 손글씨 메모풍의 상자 틸트(angle, 세로쓰기 조합 포함) · 여러 줄(\n) ·
  반투명 잉크를 불투명 판에 얹는 rgb_ink · 다중 drop_shadow.
- 텍스트가 하나뿐인 파일은 파일명 옆에 번역을 축약해 보여준다.

![북으로 데모](examples/kitae/screenshot.png)

## 구성

```
examples/
  _app/            공용 앱 — app.js(UI) · glue.py(wasm 접착) · app.css
  <이름>/
    index.html     셸 — 제목·저장 키만 정의하고 공용 앱을 불러온다
    project/       원장 사본 + 브라우저 FS 레이아웃 설정
    fonts/         파이프라인과 같은 실제 글꼴 파일
    assets/        원본(original)·텍스트 지운 판(erased) 이미지
    data.js        UI 매니페스트 (파일·행·에셋·그룹) — 렌더에는 안 쓴다
```

새 예제를 추가하려면: 프로젝트의 jaguk 설정으로 원장·글꼴·이미지를
추출해 위 구조로 넣고(추출 스크립트가 rgb_ink 판정 같은 파생 정보 없이
원장을 그대로 실으므로 단순 복사에 가깝다), 셸 index.html 한 장과
[examples/index.html](examples/index.html) 목록 카드를 만들면 된다.
