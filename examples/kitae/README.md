# 예제 — 북으로. White Illumination (北へ。)

드림캐스트 **북으로. White Illumination** 한국어 패치 프로젝트의 원장
전체 — **85개 파일, 511개 행** — 를 브라우저에서 바로 글자 주입으로
실연하는 정적 데모입니다.

**별도 렌더러가 아닙니다.** 주입 보기는 이 저장소의
[typelet](../../typelet/) 패키지(파이프라인·jaguk GUI 와 같은 코드)를
Pyodide/WebAssembly + Pillow 로 브라우저에서 **그대로 실행**한 결과입니다
— 같은 원장, 같은 글꼴, 같은 `render.py`.

![데모 화면](screenshot.png)

- GitHub Pages: `https://<host>/jaguk/kitae/`
- 로컬: **저장소 루트에서** `python -m http.server 8000` →
  `http://localhost:8000/examples/kitae/`

이 프로젝트 특유의 요소:

- **overlay 규칙 그룹** — 게임이 공유 베이스(예: `SOZ/soz_011_00.png`)
  위에 프레임을 얹어 그리는 논리 그룹. 왼쪽 목록이 그룹 단위이고, 멤버
  파일을 열면 그룹 base 를 밑판으로 깔고 위에 얹습니다 (jaguk GUI 와
  동일). erased 가 없는 same_pattern 멤버는 base 의 지운 판을 공유.
- 상자 틸트(angle, 세로쓰기 조합 포함) · 여러 줄(\n) · 세로쓰기 ·
  rgb_ink(반투명 잉크를 불투명 판에) · 다중 drop_shadow.

번역 수정은 브라우저 **localStorage 에만** 저장됩니다.
`?file=soz_016&boxes=1` 식 URL 로 초기 상태 지정. 구성은
[풍우래기3 예제](../furaiki3/)와 같습니다 (project/ 원장+설정, fonts/,
assets/, data.js 매니페스트, 공용 앱 [../_app/](../_app/)).
