# 예제 — 북으로. White Illumination (北へ。)

드림캐스트 **북으로. White Illumination** 한국어 패치 프로젝트의 원장
전체 — **85개 파일, 511개 행** — 를 **로컬과 같은 jaguk GUI 로**
브라우저에서 다루는 정적 데모입니다.

**별도 구현이 아닙니다.** 로컬 `jaguk gui` 의
[gui.html](../../typelet/gui.html) 을 그대로 띄우고, 서버(gui.py 핸들러)를
Pyodide/WebAssembly + Pillow 로 브라우저 안에서 구동합니다 — overlay 규칙
그룹(카테고리 폴더·✎ 이름변경·hide_base), 그룹 합성 보기, 상자·번역·
스타일 편집까지 로컬과 동일하게 동작합니다.

![데모 화면](screenshot.png)

- GitHub Pages: `https://<host>/jaguk/kitae/`
- 로컬: **저장소 루트에서** `python -m http.server 8000` →
  `http://localhost:8000/examples/kitae/`

원장 수정은 이 브라우저의 **localStorage 에만** 미러됩니다 (서버·디스크
기록 없음, `?reset=1` 로 비움). OCR·erase 계열 기능은 브라우저 판에서
미지원. 구성은 [풍우래기3 예제](../furaiki3/)와 같습니다.
