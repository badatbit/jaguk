# 예제 — 풍우래기3 (風雨来記3)

풍우래기3 한국어 패치 프로젝트의 원장(레저) 전체 — **912개 파일, 2,589개
행** — 를 브라우저에서 바로 글자 주입으로 실연하는 정적 데모입니다.

**별도 렌더러가 아닙니다.** 주입 보기는 이 저장소의
[typelet](../../typelet/) 패키지(파이프라인·jaguk GUI 와 같은 코드)를
Pyodide/WebAssembly + Pillow 로 브라우저에서 **그대로 실행**한 결과입니다
— 같은 원장, 같은 글꼴 파일, 같은 `render.py`. 실제 산출물과의 차이는
FreeType 빌드 차이에 의한 가장자리 AA ±수 계조뿐입니다.

![데모 화면](screenshot.png)

- GitHub Pages: `https://<host>/jaguk/furaiki3/`
- 로컬: **저장소 루트에서** `python -m http.server 8000` →
  `http://localhost:8000/examples/furaiki3/`
  (엔진이 `../../typelet/` 소스를 읽는다 — Pages 에선 워크플로가
  `_typelet/` 로 실어 준다)

번역을 고치면 즉시 다시 렌더링되고, **상자** 토글을 켜면 스테이지에서
text/crop 상자를 드래그로 옮기고 핸들로 리사이즈할 수 있습니다 (행별 ↺
되돌리기). 수정은 브라우저의 **localStorage 에만** 저장됩니다 —
서버·원장에는 아무것도 쓰지 않습니다.
`?file=G00483&view=original&zoom=2&boxes=1` 식 URL 로 초기 상태 지정.
첫 주입 보기에서 엔진(Pyodide + Pillow, 수 MB)을 한 번 내려받습니다 —
원본/erased 보기는 엔진 없이 동작합니다.

## 구성

| 경로 | 내용 |
| --- | --- |
| `index.html` | 셸 — 공용 앱([../_app/](../_app/))에 제목·저장 키만 준다 |
| `project/lettering.json` | 원장 사본 (용어표 번역은 빌드 때 ko 로 구움) |
| `project/typelet.config.json` | 브라우저 FS 레이아웃으로 재매핑한 설정 |
| `fonts/` | 파이프라인과 같은 실제 글꼴 파일 |
| `assets/original|erased/` | 게임 원본 / 텍스트 지운 베이스 |
| `data.js` | UI 매니페스트 (파일·행·에셋 목록) — 렌더에는 안 씀 |
| `compare/` | 실제 파이프라인 산출물의 전후 비교 이미지 |

원장이 담는 렌더 규칙 전부가 그대로 동작합니다 — 정렬·4x 슈퍼샘플·
외곽선·drop_shadow·squeeze·세로쓰기·run·flow·distribute·rotate·
alpha_clear·rgb_ink·post overlay·재조합 좌표계.

| 실제 산출물 전후 비교 | |
| --- | --- |
| 방면 안내판 | ![](compare/roadguide.png) |
| 스팟명 | ![](compare/spotname.png) |
| 재조합 아틀라스 — 작업은 재조합 좌표계(1280), 저장 시 게임 네이티브(1024)로 복원 | ![](compare/recompose.png) |
