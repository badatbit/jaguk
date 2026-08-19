# type-lettering

이미지 속 텍스트를 **추출(OCR) → 지우기 → 주입(번역 렌더)** 하는 범용 파이프라인.
furaiki3-l10n 의 `imgtext` 파이프라인에서 이미지 처리 부분만 떼어, 게임 의존성
(아카이브 덤프/주입, exe 좌표 검증 등) 없이 독립시킨 프로그램이다.

CLI 는 둘이다:
- **`jaguk`** — 통합 워크플로 CLI (아래 [jaguk](#jaguk--통합-워크플로) 절). 보통 이걸 쓴다.
- **`typelet`** — 저수준 파이프라인 CLI (원장을 직접 다룰 때).

## jaguk — 통합 워크플로

설정은 **cwd 의 `jaguk.json`** (또는 `-c 파일`). 흐름:

디렉토리 이름은 파이프라인 단계다 — `source`(대량 원본, 스캔 대상) →
`originals`(작업 원본) → `data`(작업 데이터 — scan.json·원장 등 이런저런
정보 전부) → `erased`(텍스트 지운 이미지) → `injected`(주입 결과):

```
jaguk init [dir] --source <대량원본> [--originals originals --data data
                                     --erased erased --injected injected] --lang ja
jaguk configure dict <용어표.json>      # 번역 용어표 (spot.json 등)
jaguk configure ocr-dict <어휘.json>    # OCR 교정 사전 — 오독을 어휘에 스냅
# originals/ 에 원본 트리를 직접 둔다 — scan/copy 는 OCR 이 파일을 놓칠 수
# 있어 잠정 비활성 (놓치는 것보다 전수 작업이 낫다)
jaguk set originals/parts/saveloadspotname --text-only --same-pattern
jaguk set originals/parts/roadguidesign --row 1 ref --row 2 replace --multicolumn
jaguk set originals/parts/roadsign --ignore
jaguk extract                           # ③ 마킹된 파일만 **본 OCR** 해 규칙대로 원장에 기록
                                        #    (ignore 는 OCR 도 안 함, 규칙 --dict 는 그 무리에만 교정)
jaguk erase [--method fill --color '#0a579d']   # ④ 텍스트 지우기 → erased/
jaguk inject                            # ⑤ 번역 주입 렌더 → injected/
jaguk status
```

- `set` 대상은 cwd 상대/절대 경로이되 **반드시 originals(작업 원본) 안**을
  가리킨다 — copy 된 실제 파일/디렉토리를 보면서 그 경로 그대로 마킹한다.
  아직 copy 전이면 scan.json 목록으로 검증한다.
- 규칙 셋: `--text-only`(+`--same-pattern`) = 이미지 전체가 글자 →
  text-only 묶음(base 불필요) / `--row N ref|replace`(+`--multicolumn`) = ref 줄은
  원문 유지·번역 키·앵커, replace 줄은 지우고 그 자리에 주입 (안내판 꼴) /
  `--ignore` = 제외. 규칙 없는 파일은 auto(행 씨앗).
- OCR 교정 사전(`ocr-dict`): 알려진 원문 어휘(.txt 한 줄 하나, 또는 용어표
  파일의 원문 키)와 유사도 ≥ `ocr_dict_min`(기본 0.7)이면 스냅 교정한다.
  원문 OCR 값은 `"ocr"` 필드에 남는다 (スウェーテン→スウェーデン).

## 설치

```
python -m venv .venv
.venv\Scripts\pip install -e .
# erase 의 inpaint 방식을 쓰려면:
.venv\Scripts\pip install -e .[inpaint]
```

CLI 는 `typelet` 하나다. 지우기·렌더는 OS 무관이고, 추출(OCR)은 백엔드를
고른다 (`--backend` 또는 설정 `ocr_backend`, 기본 `auto`):

| 백엔드 | 플랫폼 | 준비물 |
|---|---|---|
| `windows` | Windows 전용 | OS 언어팩 (설정 > 시간 및 언어 > 언어에서 일본어 추가) |
| `tesseract` | 어디서나 | `apt install tesseract-ocr tesseract-ocr-jpn` + `pip install .[tesseract]` |
| `easyocr` | 어디서나 (파이썬만) | `pip install .[easyocr]` — torch 포함이라 무겁다. CPU 만이면 torch 를 먼저 `--index-url https://download.pytorch.org/whl/cpu` 로 |

`auto` 는 win32 면 `windows`, 아니면 `tesseract` → `easyocr` 순서로 있는 것을
쓴다. 언어는 설정 `ocr_lang` 에 BCP-47("ja")로 적는다 — tesseract 코드("jpn")
변환은 내부에서 한다. 어느 백엔드든 결과 형태(줄 단위 text + x,y,w,h)는 같다.

## 워크플로

```
typelet init myproject          # 뼈대 생성
cd myproject
# originals/ 에 원본 이미지를 넣는다
typelet extract --seed          # ① 추출 — OCR 로 원문·좌표 → 원장 씨앗 행
typelet preview                 # 상자 확인 (preview/boxes)
typelet erase                   # ② 지우기 — 무문자 베이스 생성 (base/)
#   방식: inpaint(OpenCV)/median/alpha/fill(--color #RRGGBB 단색 채움)
#   base/ 의 결과를 손봐도 된다 — 재실행해도 --force 없이는 안 덮는다
# lettering.json 에서 ko·style 을 채우고 status 를 render_ready 로
typelet render                  # ③ 주입 — base/ + 원장 → out/
typelet render --on-original    # 원본 위 덧구움 비교본 (preview/ko-on-original)
typelet status                  # 진행 상황
```

## 프로젝트 구조 (`typelet init` 이 만든다)

```
typelet.config.json   설정 — 경로·글꼴 매핑·OCR 언어. 이 파일이 있는 곳이 루트
lettering.json        원장 — 스타일 + 행. 파일이 원본이다
originals/            작업 원본 이미지 트리 (읽기 전용 취급)
data/                 작업 데이터 저장소 — scan.json·원장 (jaguk 이 쓴다)
erased/               텍스트 지운 이미지 (erase 출력 + 손질본)
injected/             텍스트 주입 결과 (render 출력)
preview/              검수 산출물 (boxes, ko-on-original)
fonts/                글꼴 파일 — 설정 "fonts" 의 "패밀리/weight" → 파일 매핑
```

## 원장 (lettering.json)

furaiki3-l10n 의 `image_text.json` 과 같은 스키마다 — 그대로 복사해 와도 된다.
행 = `{box_id, file, jp, ko, crop, text, source, canvas, style, opacity, status, …}`,
스타일 = `{name, font_family_ko, font_weight, font_size_px, fill_rgb, outline_*,
effect, text_align, …}`. 상세는 [typelet/ledger.py](typelet/ledger.py) 도크스트링.

- `text` 상자는 **좌상단 기준**이고 상자 안 정렬은 스타일 `text_align` 이 정한다.
- 같은 패턴이 반복되는 무리는 스타일에 `crop_size`([w,h])와 `pad`({l,t,r,b})를
  두면 행에는 **crop 위치 [x,y]만** 남는다 — crop 크기는 스타일이, text 상자는
  crop+pad 파생 (예: 15×3 메뉴 그리드 45행이 위치 2개 값씩만 가진다).
- `status` 가 `render_ready` 인 행만 렌더된다. OCR 씨앗 행은 `todo` 로 들어온다.
- 렌더러 기능: run(한 상자 여러 스타일 이어 그리기), flow(어절 단위 자동
  줄바꿈), 세로쓰기, 균등 분배, drop_shadow / rotate / italic(전단) 효과,
  4x 슈퍼샘플 AA, alpha_clear · rgb_ink(알파 구운 스프라이트 직접 기록),
  post overlay(선화 레이어 재합성), 비텍스트 영역 불변 검증.

## text-only 묶음 — 텍스트만 달랑 있는 이미지들

saveloadspotname 처럼 **이미지 전체가 글자 하나**인 파일 무리는 행 대신
원장 최상위 `text_only` 로 묶는다 (구 키 `catalogs` 도 계속 읽힌다). 공통 style·canvas·text 상자를 한 번만
선언하고, 항목은 `파일명 → {jp, ko, status}` 만 든다:

```json
"text_only": [{
  "name": "saveloadspotname",
  "dir": "parts/saveloadspotname",
  "canvas": [512, 48], "base": "blank",
  "style": "spotname", "text": [3, 0, 506, 48],
  "overflow": "squeeze",
  "entries": {"slsn00001.tga.png": {"jp": "宗谷岬", "ko": "소야곶", "status": "render_ready"}}
}]
```

- `base: "blank"` — base 파일 없이 투명 캔버스에서 렌더한다. erase 단계가
  통째로 필요 없다 (이미지 = 글자 전부라 지우면 아무것도 안 남는다).
- `overflow: "squeeze"` — 번역이 상자보다 넓으면 **가로만** 압축 (크기·높이
  불변, spotname 258장의 검증된 규칙).
- `canvas: "original"` — 장마다 크기가 다르면 원본 이미지 크기를 쓴다.
- `fit: "original-body"` — 크기·테두리·자리를 원장에 굽지 않고 **원본을
  실측해 재현**한다 (touringspotname 규칙: 몸통 높이에 맞는 최대 크기,
  테두리 폭은 잉크 여백 실측, 가로 중앙·세로는 몸통 상단). 원본이 필요하고
  text 상자·canvas·font_size_px 는 생략 가능하다.
- entries 항목은 canvas·text·style·opacity·overflow·fit 을 개별 override 할
  수 있다.
- 씨앗: `typelet extract --catalog saveloadspotname` — 묶음 dir 의 파일만
  OCR 해 `파일명 → jp` 로 entries 를 채운다 (기존 항목 유지).
- `typelet status` 가 묶음별로 status·미번역을 집계한다.
- 렌더·프리뷰에서 text-only 항목은 가상 행으로 전개된다 (box_id =
  `이름:파일stem`). 원장 파일에 저장되는 원본은 entries 뿐이다.

## 용어표(terms) — 번역을 외부 JSON 에서 참조

같은 원문이 여러 행에 반복되면(안내판 지명 222종 → 1,745행) 번역을 행마다
굽지 않는다. 원장 최상위 `terms` 에 소스를 선언하면 **ko 가 빈 행은 렌더
때 jp 로 용어표를 찾는다** — 번역 수정이 한 곳에서 전판에 반영된다:

```json
"terms": ["../furaiki3-l10n/translation/images/roadguide_ko.json"]
```

소스는 인라인 dict 또는 파일 경로(뒤가 앞을 덮음). 파일 형식 자동 인식:
TSV(`원문<TAB>번역`), 평면 맵 `{"원문": "번역"}`, 중첩 맵
`{"원문": {"ko": …}}` (roadguide_ko.json 꼴), 레코드 목록
`{"names": [{"ja", "ko", "tr": …}]}` (spot.json 꼴 — status ok 만).
미등록 원문은 렌더가 건너뛰고 보고한다. `typelet status` 가 해결/미등록
수를 집계한다.

스타일 `squeeze_min`(0~1)은 squeeze 의 압축 하한 — 그 이상 눌리느니 정렬
앵커 기준으로 상자를 넘치게 둔다 (안내판 규칙).

`tools/migrate_roadguide.py` 는 furaiki3 방면 안내판 434장을 이 구조로
이관하는 어댑터다 (실측 레이아웃 JSON → 행 1,745개 + terms 참조).

## 재조합(recompose) — 조각난 아틀라스를 게임이 그리는 모양으로

아틀라스 한 장에 "페이지 본체 + 이어붙일 조각들"이 따로 담긴 경우, 게임이
런타임에 조각을 제자리에 blit 해 완성한다. 편집·좌표 작업은 완성된 모양에서
해야 하므로 `jaguk recompose` 로 트리를 그 모양으로 바꾸고, 납품 직전에
`jaguk recompose --restore` 로 원형 복원본을 별도 출력한다 (왕복 무손실 —
저장 전 자동 검증). **트리·GUI·원장 좌표는 항상 재조합 기준**이다.

규칙은 원장 최상위 `recompose` 에 데이터로 적는다. move 하나 =
`[sx, sy, w, h, dx, dy]` — 원본 (sx,sy) 의 w×h 조각을 (dx,dy) 로 옮기고
원 자리는 비운다. move 에 안 걸린 영역은 제자리에 남는다.

**예제 1 — 오른쪽 확장** (1024 캔버스 아래쪽 조각들로 1280 브라우저 완성,
furaiki3 internetmode1a/2/3/4a 실측):

```json
{"file": "parts/internetmode2.tga.png",
 "canvas": [1024, 1024], "to": [1280, 1024],
 "moves": [[  0, 720, 256, 304, 1024,   0],
           [256, 720, 256, 304, 1024, 304],
           [512, 720, 256, 112, 1024, 608]]}
```
```
┌──────────────┐          ┌──────────────┬──┐
│   페이지      │          │   페이지      │↑ │  ← 조각 3장이 오른쪽
│  1024x720    │    →     │  1024x720    │측 │     x1024 열에 위→아래로
├──┬──┬──┬────┤          ├──┬──────────┴──┤     (304+304+112 = 720)
│1 │2 │3 │기타 │          │빈칸      │기타  │  ← 기타 조각은 제자리
└──┴──┴──┴────┘          └──────────┴─────┘
```

**예제 2 — 아래 확장** (오른쪽 여백의 세로 조각을 페이지 아래에 이어붙임):

```json
{"file": "ui/longpage.png",
 "canvas": [1024, 512], "to": [1024, 768],
 "moves": [[768, 0, 256, 256, 0, 512],
           [768, 256, 256, 256, 256, 512]]}
```

**예제 3 — 조각 하나만 이동** (배너가 캔버스 구석에 따로 저장된 경우):

```json
{"file": "ui/title.png",
 "canvas": [512, 512], "to": [640, 512],
 "moves": [[0, 448, 128, 64, 512, 0]]}
```

**규칙 도출 팁**: 조각 배치는 엔진 blit 지식이라 자동 추론하지 않는다.
이음새 실측으로 검증할 수 있다 — 페이지 가장자리 열(x=1023)과 조각의 첫
열 픽셀 차이 평균이 ≈0 이면 맞는 짝이다 (internetmode2 실측: 정답 짝 0.0,
오답 짝 16.5). 새 아틀라스는 AI 에게 "이 아틀라스 이음새 분석해서 recompose
스펙 만들어줘"라고 시키고 GUI 로 눈검증 후 고정하면 된다.

주의: `fit`/text-only 묶음과 마찬가지로, 이미 추출된 행 좌표는 recompose 실행
시점에 canvas 가 네이티브 크기인 것만 자동 변환된다 — 재조합 이후에 추출한
행은 이미 맞는 좌표계다.

## 무엇이 여기 없나

원본 이미지를 어디서 가져오고 결과를 어디에 넣는지는 이 도구의 관심사가
아니다 — 게임 아카이브 덤프/재주입(`raiki imgtext dump/inject`)은 furaiki3-l10n
에 남아 있다. 이 도구는 `originals/ → out/` 디렉토리 트리만 안다.
