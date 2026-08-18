# type-lettering

이미지 속 텍스트를 **추출(OCR) → 지우기 → 주입(번역 렌더)** 하는 범용 파이프라인.
furaiki3-l10n 의 `imgtext` 파이프라인에서 이미지 처리 부분만 떼어, 게임 의존성
(아카이브 덤프/주입, exe 좌표 검증 등) 없이 독립시킨 프로그램이다.

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
originals/            원본 이미지 트리 (읽기 전용 취급)
base/                 무문자 베이스 (erase 출력 + 손질본)
out/                  렌더 결과
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

## 무엇이 여기 없나

원본 이미지를 어디서 가져오고 결과를 어디에 넣는지는 이 도구의 관심사가
아니다 — 게임 아카이브 덤프/재주입(`raiki imgtext dump/inject`)은 furaiki3-l10n
에 남아 있다. 이 도구는 `originals/ → out/` 디렉토리 트리만 안다.
