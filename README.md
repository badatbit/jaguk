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
jaguk scan                              # ① 대량 원본에서 텍스트 있는 파일 필터링
                                        #    → 파일별 대상 여부만 data/scan.json 한 파일에
jaguk copy                              # ② 텍스트 있다고 마킹된 파일만 source → originals
jaguk set data/parts/saveloadspotname --image-only --same-pattern
jaguk set data/parts/roadguidesign --row 1 ref --row 2 replace --multicolumn
jaguk set data/parts/roadsign --ignore
jaguk extract                           # ③ 마킹된 파일만 **본 OCR** 해 규칙대로 원장에 기록
                                        #    (ignore 는 OCR 도 안 함, 규칙 --dict 는 그 무리에만 교정)
jaguk erase [--method fill --color '#0a579d']   # ④ 텍스트 지우기 → erased/
jaguk inject                            # ⑤ 번역 주입 렌더 → injected/
jaguk status
```

- `set` 대상은 cwd 상대/절대 경로이되 **반드시 data 디렉토리 안** 이름공간을
  가리킨다 (`data/<이미지 트리 경로>`). 실재하는 파일일 필요는 없다 —
  scan.json 목록에 있는지로 검증한다.
- 규칙 셋: `--image-only`(+`--same-pattern`) = 이미지 전체가 글자 →
  카탈로그(base 불필요) / `--row N ref|replace`(+`--multicolumn`) = ref 줄은
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
- `status` 가 `render_ready` 인 행만 렌더된다. OCR 씨앗 행은 `todo` 로 들어온다.
- 렌더러 기능: run(한 상자 여러 스타일 이어 그리기), flow(어절 단위 자동
  줄바꿈), 세로쓰기, 균등 분배, drop_shadow / rotate / italic(전단) 효과,
  4x 슈퍼샘플 AA, alpha_clear · rgb_ink(알파 구운 스프라이트 직접 기록),
  post overlay(선화 레이어 재합성), 비텍스트 영역 불변 검증.

## 카탈로그 — 텍스트만 달랑 있는 이미지 묶음

saveloadspotname 처럼 **이미지 전체가 글자 하나**인 파일 무리는 행 대신
원장 최상위 `catalogs` 로 묶는다. 공통 style·canvas·text 상자를 한 번만
선언하고, 항목은 `파일명 → {jp, ko, status}` 만 든다:

```json
"catalogs": [{
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
- 씨앗: `typelet extract --catalog saveloadspotname` — 카탈로그 dir 의 파일만
  OCR 해 `파일명 → jp` 로 entries 를 채운다 (기존 항목 유지).
- `typelet status` 가 카탈로그별로 status·미번역을 집계한다.
- 렌더·프리뷰에서 카탈로그 항목은 가상 행으로 전개된다 (box_id =
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

## 무엇이 여기 없나

원본 이미지를 어디서 가져오고 결과를 어디에 넣는지는 이 도구의 관심사가
아니다 — 게임 아카이브 덤프/재주입(`raiki imgtext dump/inject`)은 furaiki3-l10n
에 남아 있다. 이 도구는 `originals/ → out/` 디렉토리 트리만 안다.
