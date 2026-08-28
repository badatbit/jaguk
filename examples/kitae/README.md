# 예제 — 북으로. White Illumination (北へ。)

드림캐스트 **북으로. White Illumination** 한국어 패치 프로젝트의 원장
전체 — **85개 파일, 511개 행** — 를 브라우저에서 바로 글자 주입으로
실연하는 정적 데모입니다.

![데모 화면](screenshot.png)

구조는 [풍우래기3 예제](../furaiki3/)와 같고,
이 프로젝트 특유의 요소를 보여줍니다:

- **overlay 그룹** — 게임이 공유 베이스(예: `SOZ/soz_011_00.png`) 위에
  프레임을 얹어 그리는 논리 그룹. 멤버 파일을 열면 그룹 base 를 밑판으로
  깔고 그 위에 멤버 레이어를 얹습니다 (jaguk GUI 의 멤버 보기와 동일).
  erased 가 없는 same_pattern 멤버는 base 의 지운 판을 공유합니다.
- **상자 틸트(angle)** — 손글씨 메모처럼 기울어 그려지는 행 (세로쓰기와
  조합 포함), **여러 줄(\n)**, **세로쓰기**, 다중 drop_shadow.

## 열어 보기

- GitHub Pages: `https://<host>/jaguk/kitae/`
- 로컬: 저장소 루트에서 `python -m http.server 8000` →
  `http://localhost:8000/examples/kitae/`

번역 수정은 브라우저 **localStorage 에만** 저장됩니다.
`?file=soz_016&boxes=1` 식의 URL 로 초기 상태를 지정할 수 있습니다.

글꼴은 Google Fonts 의 IBM Plex Sans KR(OFL)만 씁니다. 실제 산출물은
[typelet/render.py](../../typelet/render.py) (PIL/FreeType)가 만듭니다 —
이 페이지는 같은 해석 스펙을 Canvas 2D 로 그린 근사입니다.
