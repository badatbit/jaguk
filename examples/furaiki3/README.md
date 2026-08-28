# 예제 — 풍우래기3 (風雨来記3)

풍우래기3 한국어 패치 프로젝트의 원장(레저) 전체 — **912개 파일, 2,589개
행** — 를 **로컬과 같은 jaguk GUI 로** 브라우저에서 다루는 정적 데모입니다.

**별도 구현이 아닙니다.** 이 페이지는 로컬 `jaguk gui` 가 쓰는 바로 그
[gui.html](../../typelet/gui.html) 을 그대로 띄우고, 서버(gui.py 핸들러)를
Pyodide/WebAssembly + Pillow 로 브라우저 안에서 구동합니다. 서비스워커가
`/api/`·`/img/` 요청을 wasm 핸들러로 중계할 뿐, UI·렌더·원장 처리 전부가
로컬과 같은 코드입니다.

![데모 화면](screenshot.png)

- GitHub Pages: `https://<host>/jaguk/furaiki3/`
- 로컬: **저장소 루트에서** `python -m http.server 8000` →
  `http://localhost:8000/examples/furaiki3/`

로컬 GUI 에서 되는 검수·편집이 그대로 됩니다 — 원본/erased/injected 보기,
겹침·상하·좌우 비교, 상자 이동/리사이즈/추가/삭제/나누기, 번역·스타일
편집, 그룹 이름변경…. 다만 **원장 수정은 이 브라우저의 localStorage 에만**
미러됩니다 (서버·디스크 기록 없음, `?reset=1` 로 비움). OCR·erase 계열
기능(박스 지우고 OCR 다시 등)은 브라우저 판에서 미지원입니다. 첫 진입 때
엔진(Pyodide + Pillow)과 이미지 트리를 한 번 내려받습니다.

## 구성

| 경로 | 내용 |
| --- | --- |
| `index.html` | 진입 — 서비스워커 등록 후 `app/`(진짜 gui.html)로 이동 |
| `sw.js` | 서비스워커 스텁 (공용 [../_app/](../_app/) 로직 사용) |
| `project/lettering.json` | 원장 사본 (용어표 번역은 빌드 때 ko 로 구움) |
| `project/typelet.config.json` | 브라우저 FS 레이아웃으로 재매핑한 설정 |
| `fonts/` | 파이프라인과 같은 실제 글꼴 파일 |
| `assets/original|erased/` | 게임 원본 / 텍스트 지운 베이스 |
| `data.js` | 에셋 매니페스트 (엔진이 FS 에 실을 파일 목록) |
| `compare/` | 실제 파이프라인 산출물의 전후 비교 이미지 |

| 실제 산출물 전후 비교 | |
| --- | --- |
| 방면 안내판 | ![](compare/roadguide.png) |
| 스팟명 | ![](compare/spotname.png) |
| 재조합 아틀라스 — 작업은 재조합 좌표계(1280), 저장 시 게임 네이티브(1024)로 복원 | ![](compare/recompose.png) |
