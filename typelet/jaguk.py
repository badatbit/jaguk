# -*- coding: utf-8 -*-
"""jaguk — 이미지 텍스트 로컬라이징 통합 CLI (typelet 파이프라인의 워크플로 층).

설정은 **cwd 의 jaguk.json** (또는 -c 로 지정). 설정 파일이 있는 곳이
프로젝트 루트다. 스키마는 typelet.config.json 과 같다.

작업 흐름:
    init        프로젝트 만들기 — 원본·데이터·지움·주입 디렉토리와 언어
    configure   설정 변경 — dict(용어표)·ocr-dict(교정 사전)·lang·backend 등
    scan        (비활성) 텍스트 있는 파일 필터링 — OCR 이 파일을 놓칠 수
                있어 잠정 비활성. originals 에 원본을 직접 두고 작업한다
    copy        (비활성) scan 목록 기반 복사 — 같은 이유로 잠정 비활성
    set         파일/디렉토리별 처리 규칙 — 대상은 **originals(작업 원본)
                안** 경로 (copy 된 파일을 보면서 마킹)
                  --text-only             이미지 전체가 글자 (카탈로그로)
                  --same-pattern          전부 같은 스타일
                  --row N ref|replace     N번째 줄의 역할 (ref=원문 유지·앵커,
                                          replace=지우고 그 자리에 번역)
                  --multicolumn           한 줄에 여러 항목 (열별 짝짓기)
                  --ignore                처리 제외
                  --dict FILE             이 무리의 번역 용어표
    extract     마킹된 파일을 규칙대로 **본 OCR** 해 원장에 기록
                (set 없으면 auto = 행 씨앗, ignore 는 OCR 도 안 함)
    erase       텍스트 지우기 — 무문자 베이스 생성
    inject      번역 주입 — 렌더
    status      진행 상황
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from . import config as configmod
from . import ledger as ledgermod
from . import ocr as ocrmod
from .config import DEFAULTS, Project

CONFIG_NAME = "jaguk.json"


def posix(value: str) -> str:
    """JSON 에 기록하는 경로는 전부 POSIX 스타일(/) — WSL/리눅스와 공유 가능."""
    return value.replace("\\", "/")


def load_project(config_arg: str) -> Project:
    path = Path(config_arg or CONFIG_NAME)
    if not path.exists():
        sys.exit(f"설정 파일이 없습니다: {path.resolve()} — `jaguk init` 먼저, "
                 f"또는 -c 로 지정하세요.")
    return configmod.load_path(path.resolve())


def save_config(project: Project, mutate) -> None:
    path = project.root / CONFIG_NAME
    file_raw = json.loads(path.read_text(encoding="utf-8"))
    # 구세대 키는 저장하면서 새 키로 이행한다 (값 보존, 구 키 제거)
    for old, new in configmod.LEGACY_KEYS.items():
        if old in file_raw:
            file_raw.setdefault(new, file_raw[old])
            del file_raw[old]
    raw = {**DEFAULTS, **file_raw}
    mutate(raw)
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=1) + "\n",
                    encoding="utf-8")


# ---- init -------------------------------------------------------------------

def cmd_init(args) -> int:
    directory = Path(args.directory).resolve()
    config_path = directory / CONFIG_NAME
    if config_path.exists():
        sys.exit(f"이미 프로젝트입니다: {config_path}")
    directory.mkdir(parents=True, exist_ok=True)
    raw = dict(DEFAULTS)
    raw.update({
        "source": posix(args.source),
        "originals": posix(args.originals),
        "data": posix(args.data),
        "erased": posix(args.erased),
        "injected": posix(args.injected),
        "ledger": f"{posix(args.data)}/lettering.json",
        "ocr_lang": args.lang,
        "ocr_backend": args.backend,
    })
    config_path.write_text(json.dumps(raw, ensure_ascii=False, indent=1) + "\n",
                           encoding="utf-8")
    for key in ("originals", "data", "erased", "injected",
                "preview_root", "font_root"):
        (directory / raw[key]).mkdir(parents=True, exist_ok=True)
    ledger_path = directory / raw["ledger"]
    ledger_path.write_text(
        json.dumps({"styles": [], "rows": []}, ensure_ascii=False, indent=1)
        + "\n", encoding="utf-8")
    print(f"프로젝트 생성: {config_path}")
    source_note = raw["source"] or "(미설정 — originals 를 직접 스캔)"
    print(f"""
{directory.name}/
  jaguk.json      설정 (이 파일이 있는 곳이 프로젝트 루트)
  {raw['originals']}/      작업 원본 — copy 의 도착지, 파이프라인의 입력
  {raw['data']}/           작업 데이터 — scan.json·원장 lettering.json
  {raw['erased']}/         텍스트 지운 이미지 — erase 출력, 손질본 두는 곳
  {raw['injected']}/       텍스트 주입 결과 — inject 출력
  preview/        검수 그림
  fonts/          글꼴 파일
source(대량 원본, 스캔 대상): {source_note}

다음 단계: originals 에 원본을 두고 → set → extract → erase → inject
(scan/copy 는 OCR 누락 위험으로 잠정 비활성)""")
    return 0


# ---- configure --------------------------------------------------------------

CONFIG_KEYS = ("dict", "ocr-dict", "lang", "backend", "min-conf", "dict-min",
               "source", "font")


def cmd_configure(args) -> int:
    project = load_project(args.config)
    if not args.key:
        # 현재 설정 요약
        print(f"프로젝트: {project.root}")
        print(f"  source   = {project.source_root or '(없음 — originals 직접 스캔)'}")
        print(f"  originals= {project.original_root}")
        print(f"  data     = {project.data_root}")
        print(f"  erased   = {project.base_root}")
        print(f"  injected = {project.output_root}")
        print(f"  lang     = {project.ocr_lang} / backend = {project.ocr_backend}")
        print(f"  ocr_dict = {project.ocr_dict or '(없음)'}")
        data = ledgermod.load(project)
        terms = data.get("terms") or []
        print(f"  용어표(terms) = {terms or '(없음)'}")
        return 0
    key, values = args.key, args.value
    if key not in CONFIG_KEYS:
        sys.exit(f"모르는 설정 키 {key!r} — 가능: {', '.join(CONFIG_KEYS)}")
    if key == "dict":
        if not values:
            sys.exit("용어표 파일 경로가 필요합니다: jaguk configure dict <파일>")
        data = ledgermod.load(project)
        terms = data.get("terms") or []
        if isinstance(terms, (str, dict)):
            terms = [terms]
        entry = values[0].replace("\\", "/")
        if entry not in terms:
            terms.append(entry)
            data["terms"] = terms
            ledgermod.save(project, data)
        merged = ledgermod.load_terms(project, data)
        print(f"용어표 등록: {entry} (총 {len(merged)}어휘)")
        return 0
    if key == "font":
        if len(values) != 2:
            sys.exit('사용: jaguk configure font "패밀리/weight" <파일>')
        font_file = posix(values[1])
        save_config(project, lambda raw: raw["fonts"].__setitem__(values[0], font_file))
        print(f"글꼴 등록: {values[0]} -> {font_file}")
        return 0
    if not values:
        sys.exit(f"값이 필요합니다: jaguk configure {key} <값>")
    value = values[0]
    if key in ("ocr-dict", "source"):
        value = posix(value)
    config_key = {"ocr-dict": "ocr_dict", "lang": "ocr_lang",
                  "backend": "ocr_backend", "min-conf": "ocr_min_conf",
                  "dict-min": "ocr_dict_min", "source": "source"}[key]

    def mutate(raw):
        if config_key == "ocr_dict":
            if value not in raw["ocr_dict"]:
                raw["ocr_dict"].append(value)
        elif config_key in ("ocr_min_conf", "ocr_dict_min"):
            raw[config_key] = float(value)
        else:
            raw[config_key] = value
    save_config(project, mutate)
    print(f"{config_key} = {value}")
    return 0


# ---- scan / copy ------------------------------------------------------------

def scan_path(project: Project) -> Path:
    return project.data_root / "scan.json"


def scan_root(project: Project) -> Path:
    return project.source_root or project.original_root


# scan/copy 잠정 비활성 (2026-08-19 사용자 결정): OCR 필터링이 파일을 놓치는
# 위험(touringspotname 전멸 사례)이 복사량 절감보다 크다. originals 에 원본을
# 직접 두고 전수 작업한다. scan.json 이 이미 있으면 extract 가 참고는 한다.
SCAN_DISABLED = (
    "scan/copy 는 잠정 비활성입니다 — OCR 필터링이 파일을 놓칠 수 있습니다.\n"
    "originals 디렉토리에 원본 트리를 직접 두고 set → extract 로 진행하세요."
)


def load_scan(project: Project) -> dict[str, int] | None:
    """scan.json → {상대경로: 줄 수}. 없으면 None (scan 비활성 — 전수 작업)."""
    path = scan_path(project)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))["files"]


def cmd_scan(args) -> int:
    """(비활성) 대량 원본에서 텍스트 있는 파일 필터링."""
    print(SCAN_DISABLED, file=sys.stderr)
    return 2


def _cmd_scan_impl(args) -> int:      # 보존 — 재활성화 대비
    project = load_project(args.config)
    root = scan_root(project)
    files = ocrmod.collect_files(root, args.only)
    if not files:
        print(f"스캔할 이미지 없음: {root} (only={args.only!r})")
        return 1
    backend = ocrmod.pick_backend(args.backend or project.ocr_backend)
    print(f"스캔 {len(files)}장 ({project.ocr_lang}, {backend}) — {root}")
    _, results = ocrmod.run_ocr(project, files, backend=backend, root=root)

    path = scan_path(project)
    files_map: dict[str, int] = {}
    if path.exists() and args.only:      # 부분 스캔은 기존 목록에 병합
        files_map = json.loads(path.read_text(encoding="utf-8"))["files"]
    for entry in results:
        files_map[entry["file"]] = len(entry["lines"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        {"root": posix(str(root)), "backend": backend,
         "files": dict(sorted(files_map.items()))},
        ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    with_text = sum(1 for n in files_map.values() if n)
    print(f"텍스트 있음 {with_text}장 / 없음 {len(files_map) - with_text}장 "
          f"-> {path}")
    return 0


def cmd_copy(args) -> int:
    """(비활성) scan 목록 기반 복사."""
    print(SCAN_DISABLED, file=sys.stderr)
    return 2


def _cmd_copy_impl(args) -> int:      # 보존 — 재활성화 대비
    """scan.json 에서 텍스트 있다고 마킹된 파일만 source → originals 복사."""
    project = load_project(args.config)
    if project.source_root is None:
        print("source 미설정 — originals 를 직접 스캔했으므로 복사할 것이 없습니다.")
        return 0
    files_map = load_scan(project)
    if files_map is None:
        sys.exit("스캔 결과가 없습니다 (scan 은 비활성) — originals 에 직접 두세요.")
    copied = kept = missing = 0
    for relative, lines in sorted(files_map.items()):
        if not lines:
            continue
        if args.only and args.only.lower() not in relative.lower():
            continue
        src = project.source_root / Path(*relative.split("/"))
        dst = project.original_root / Path(*relative.split("/"))
        if not src.exists():
            print(f"  원본 없음: {relative}")
            missing += 1
            continue
        if dst.exists() and not args.force:
            kept += 1
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1
    print(f"복사 {copied}장 / 유지 {kept}장 / 원본 없음 {missing}장 "
          f"-> {project.original_root}")
    return 0


# ---- set (규칙) -------------------------------------------------------------

def resolve_rule_key(project: Project, target: str) -> str:
    """set 대상 경로 → 규칙 키 (이미지 트리 기준 상대 경로).

    대상은 cwd 상대 또는 절대 경로이고 **반드시 originals(작업 원본)
    디렉토리 안**이어야 한다 — copy 로 복사된 실제 파일/디렉토리를 보면서
    그 경로 그대로 마킹한다.
    """
    resolved = Path(target).resolve()
    root = project.original_root.resolve()
    try:
        relative = resolved.relative_to(root).as_posix()
    except ValueError:
        sys.exit(f"set 대상은 작업 원본 디렉토리({root}) 안이어야 합니다: {resolved}")
    if relative == ".":
        sys.exit("originals 전체에는 규칙을 걸 수 없습니다 — 하위 경로를 지정하세요.")
    # 실재하지 않으면 (있다면) scan 목록으로라도 검사한다
    if not resolved.exists():
        files_map = load_scan(project) or {}
        if relative not in files_map and not any(
                f.startswith(relative.rstrip("/") + "/") for f in files_map):
            sys.exit(f"실재하지 않는 대상입니다: {relative}")
    return relative


def cmd_set(args) -> int:
    project = load_project(args.config)
    key = resolve_rule_key(project, args.target)
    data = ledgermod.load(project)
    rules = data.setdefault("rules", {})

    if args.clear:
        if rules.pop(key, None) is not None:
            ledgermod.save(project, data)
            print(f"규칙 제거: {key}")
        else:
            print(f"규칙 없음: {key}")
        return 0

    rule = rules.get(key, {})
    if args.ignore:
        rule = {"mode": "ignore"}
    elif args.text_only:
        rule["mode"] = "text-only"
    elif args.row:
        rule["mode"] = "rows"
        rows = rule.setdefault("rows", {})
        for number, role in args.row:
            if role not in ("ref", "replace", "ignore"):
                sys.exit(f"--row 역할은 ref|replace|ignore: {role!r}")
            rows[str(int(number))] = role
    if args.same_pattern:
        rule["same_pattern"] = True
    if args.multicolumn:
        rule["multicolumn"] = True
    if args.style:
        rule["style"] = args.style
    if args.dict:
        rule["dict"] = args.dict.replace("\\", "/")
        terms = data.get("terms") or []
        if isinstance(terms, (str, dict)):
            terms = [terms]
        if rule["dict"] not in terms:
            terms.append(rule["dict"])
        data["terms"] = terms
    if not rule:
        print(f"규칙 {key}: {json.dumps(rules.get(key, {}), ensure_ascii=False)}")
        return 0
    rules[key] = rule
    ledgermod.save(project, data)
    print(f"규칙 저장: {key} = {json.dumps(rule, ensure_ascii=False)}")
    return 0


def match_rule(rules: dict, relative: str) -> tuple[str, dict]:
    """가장 구체적인(긴) 경로의 규칙. 없으면 ('', {})."""
    best_path, best = "", {}
    for path, rule in rules.items():
        if relative == path or relative.startswith(path.rstrip("/") + "/"):
            if len(path) > len(best_path):
                best_path, best = path, rule
    return best_path, best


# ---- read (규칙대로 원장 씨앗) ----------------------------------------------

def cluster_lines(lines: list[dict]) -> list[list[dict]]:
    """OCR 줄들을 y 겹침으로 행 클러스터로 묶는다 (위→아래 순서)."""
    clusters: list[dict] = []
    for line in sorted(lines, key=lambda l: l["y"]):
        placed = False
        for cluster in clusters:
            if line["y"] < cluster["y1"] and line["y"] + line["h"] > cluster["y0"]:
                cluster["y0"] = min(cluster["y0"], line["y"])
                cluster["y1"] = max(cluster["y1"], line["y"] + line["h"])
                cluster["lines"].append(line)
                placed = True
                break
        if not placed:
            clusters.append({"y0": line["y"], "y1": line["y"] + line["h"],
                             "lines": [line]})
    clusters.sort(key=lambda c: c["y0"])
    for cluster in clusters:
        cluster["lines"].sort(key=lambda l: l["x"])
    return [c["lines"] for c in clusters]


def pair_ref(replace_line: dict, ref_lines: list[dict]) -> dict | None:
    """replace 줄에 대응하는 ref 줄 — x 겹침 최대, 없으면 중심 최근접."""
    def overlap(a, b):
        return min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
    best = max(ref_lines, key=lambda r: overlap(replace_line, r), default=None)
    if best is not None and overlap(replace_line, best) > 0:
        return best
    center = replace_line["x"] + replace_line["w"] / 2
    return min(ref_lines,
               key=lambda r: abs(r["x"] + r["w"] / 2 - center), default=None)


def seed_rows_mode(data: dict, entry: dict, rule: dict) -> tuple[int, int]:
    """--row 규칙 씨앗 — ref 줄은 원문(유지·앵커), replace 줄은 지우고 주입."""
    roles = rule.get("rows") or {}
    clusters = cluster_lines(entry["lines"])
    ref_lines: list[dict] = []
    replace_lines: list[dict] = []
    for index, cluster in enumerate(clusters, 1):
        role = roles.get(str(index), "ignore")
        if role == "ref":
            ref_lines.extend(cluster)
        elif role == "replace":
            replace_lines.extend(cluster)
    if not replace_lines:
        return 0, 0
    if not ref_lines:
        ref_lines = replace_lines      # ref 줄이 없으면 제 자리가 원문

    rows = ledgermod.rows(data)
    existing_ids = {r.get("box_id") for r in rows}
    existing_crops = {
        (r.get("file"), tuple((r.get("crop") or {}).get("rect") or ()))
        for r in rows if r.get("crop")
    }
    relative = entry["file"]
    width, height = entry["width"], entry["height"]
    prefix = ocrmod._id_prefix(relative)
    counter = 0
    added = skipped = 0
    for line in replace_lines:
        pad = 2
        rect = [max(0, line["x"] - pad), max(0, line["y"] - pad),
                min(width, line["x"] + line["w"] + pad) - max(0, line["x"] - pad),
                min(height, line["y"] + line["h"] + pad) - max(0, line["y"] - pad)]
        if (relative, tuple(rect)) in existing_crops:
            skipped += 1
            continue
        ref = pair_ref(line, ref_lines)
        counter += 1
        box_id = f"{prefix}r{counter}"
        while box_id in existing_ids:
            counter += 1
            box_id = f"{prefix}r{counter}"
        existing_ids.add(box_id)
        existing_crops.add((relative, tuple(rect)))
        rows.append({
            "box_id": box_id,
            "file": relative,
            "element_id": None,
            "run_id": None,
            "jp": ref["text"],
            "ko": "",
            "ocr_id": None,
            "crop": {"id": None, "src": "OCR", "rect": rect},
            "text": [line["x"], line["y"], line["w"], line["h"]],
            "source": [ref["x"], ref["y"], ref["w"], ref["h"]],
            "canvas": [width, height],
            "pad": None,
            "style": rule.get("style", ""),
            "opacity": "FF",
            "status": "todo",
            "notes": "jaguk extract (rows)",
        })
        added += 1
    return added, skipped


def seed_text_only(data: dict, entry: dict, rule_path: str, rule: dict,
                    lang: str) -> tuple[int, int]:
    """--text-only 규칙 씨앗 — 카탈로그 항목으로 (base 파일 불필요)."""
    directory = rule_path
    if "." in Path(rule_path).name:            # 규칙이 파일 하나를 가리킴
        directory = str(Path(rule_path).parent).replace("\\", "/")
    name = Path(directory).name or "catalog"
    catalogs = data.setdefault("catalogs", [])
    cat = next((c for c in catalogs if c["name"] == name), None)
    if cat is None:
        style = rule.get("style") or name
        cat = {"name": name, "dir": directory, "canvas": "original",
               "base": "blank", "style": style, "overflow": "squeeze",
               "entries": {}}
        catalogs.append(cat)
        styles = data.setdefault("styles", [])
        if not any(s.get("name") == style for s in styles):
            # 채워야 렌더되는 뼈대 — 글꼴·크기·색·정렬은 실측 후 사람이 정한다
            styles.append({"name": style, "label": f"{name} (자동 생성 뼈대)",
                           "font_family_ko": "", "font_weight": "",
                           "font_size_px": "", "fill_rgb": "#FFFFFF",
                           "outline_rgb": "", "outline_weight_px": 0,
                           "effect": "", "font_style": "regular",
                           "text_align": "lm"})
    relative = entry["file"]
    prefix = cat["dir"].strip("/")
    fname = relative[len(prefix) + 1:] if prefix and relative.startswith(prefix + "/") \
        else relative
    if fname in cat["entries"]:
        return 0, 1
    joiner = "" if (lang or "").lower().startswith(("ja", "zh")) else " "
    cat["entries"][fname] = {
        "jp": joiner.join(l["text"] for l in entry["lines"]).strip(),
        "ko": "", "status": "todo"}
    return 1, 0


def _rule_vocab(project: Project, dict_file: str) -> list[str]:
    """규칙의 --dict 파일 → 교정 어휘 (원문 키). load_ocr_dict 와 같은 형식."""
    path = (project.root / dict_file).resolve()
    if not path.exists():
        raise FileNotFoundError(f"규칙 사전 파일이 없습니다: {path}")
    if path.suffix.lower() == ".txt":
        return [l.strip() for l in path.read_text(encoding="utf-8-sig").splitlines()
                if l.strip() and not l.strip().startswith("#")]
    if path.suffix.lower() == ".json":
        return list(ledgermod._terms_from_json(path))
    return list(ledgermod._terms_from_tsv(path))


def cmd_extract(args) -> int:
    """마킹된 규칙대로 **본 OCR 을 다시 돌리고** 원장에 기록한다.

    대상은 originals 트리 전수다 (scan 비활성 — OCR 필터링이 파일을 놓치는
    위험이 절감보다 크다). ignore 규칙은 OCR 자체를 건너뛰고, 규칙의
    --dict 는 그 무리에만 교정으로 적용된다.
    """
    project = load_project(args.config)
    data = ledgermod.load(project)
    rules = data.get("rules", {})

    targets = ocrmod.collect_files(project.original_root, args.only)
    if not targets:
        print(f"originals 에 이미지가 없습니다: {project.original_root} — "
              "원본 트리를 직접 두세요 (scan/copy 는 비활성).")
        return 1

    counts = {"auto": 0, "text-only": 0, "rows": 0, "ignore": 0}
    plan: list[tuple[str, str, dict, str]] = []   # (rel, rule_path, rule, mode)
    for path in targets:
        relative = path.relative_to(project.original_root).as_posix()
        rule_path, rule = match_rule(rules, relative)
        mode = rule.get("mode", "auto")
        if mode == "image-only":         # 구 이름 — 기존 원장 규칙 호환
            mode = "text-only"
        counts[mode] = counts.get(mode, 0) + 1
        if mode == "ignore":
            continue
        plan.append((relative, rule_path, rule, mode))
    if not plan:
        print("추출 대상 없음 (전부 ignore?)")
        return 1

    backend = ocrmod.pick_backend(args.backend or project.ocr_backend)
    files = [project.original_root / Path(*rel.split("/"))
             for rel, _, _, _ in plan]
    print(f"OCR {len(files)}장 ({project.ocr_lang}, {backend}) ...")
    _, results = ocrmod.run_ocr(project, files, backend=backend)

    vocab = ocrmod.load_ocr_dict(project)
    if vocab:
        corrected = ocrmod.correct_results(results, vocab, project.ocr_dict_min)
        print(f"전역 교정 사전 {len(vocab)}어휘 — {corrected}줄 교정")
    by_rel = {e["file"]: e for e in results}

    # 규칙별 --dict — 그 무리에만 적용
    for rule_path in {rp for _, rp, r, _ in plan if r.get("dict")}:
        rule = rules[rule_path]
        subset = [by_rel[rel] for rel, rp, _, _ in plan
                  if rp == rule_path and rel in by_rel]
        rule_vocab = _rule_vocab(project, rule["dict"])
        corrected = ocrmod.correct_results(subset, rule_vocab,
                                           project.ocr_dict_min)
        print(f"규칙 사전({rule_path}) {len(rule_vocab)}어휘 — {corrected}줄 교정")

    added = skipped = 0
    auto_entries = []
    for relative, rule_path, rule, mode in plan:
        entry = by_rel.get(relative)
        if entry is None or not entry["lines"]:
            continue
        if mode == "text-only":
            a, s = seed_text_only(data, entry, rule_path, rule, project.ocr_lang)
        elif mode == "rows":
            a, s = seed_rows_mode(data, entry, rule)
        else:
            auto_entries.append(entry)
            continue
        added += a
        skipped += s
    a, s = ocrmod.seed_rows(data, auto_entries)
    added += a
    skipped += s
    if added:
        ledgermod.save(project, data)
    print(f"처리: auto {counts['auto']}장 / text-only {counts['text-only']}장 / "
          f"rows {counts['rows']}장 / ignore {counts['ignore']}장 (OCR 제외)")
    print(f"원장 기록 {added}건 추가, 기존 {skipped}건 유지 -> {project.ledger_path}")
    return 0


# ---- erase / inject / status ------------------------------------------------

def cmd_erase(args) -> int:
    from . import erase
    project = load_project(args.config)
    return erase.run(project, only=args.only, method=args.method,
                     pad=args.pad, force=args.force, color=args.color)


def cmd_inject(args) -> int:
    from . import render
    project = load_project(args.config)
    return render.run(project,
                      statuses=set(args.status) if args.status else None,
                      only=args.only, list_only=args.list,
                      on_original=args.on_original)


def cmd_status(args) -> int:
    from collections import Counter
    project = load_project(args.config)
    data = ledgermod.load(project)
    rows = ledgermod.rows(data)
    print(f"원장: {project.ledger_path}")
    by_status = Counter((r.get("status") or "(없음)") for r in rows)
    print(f"행 {len(rows)}개 / 스타일 {len(data.get('styles', []))}개 / "
          f"규칙 {len(data.get('rules', {}))}개")
    for status, count in by_status.most_common():
        print(f"  {status:20} {count}")
    for cat in ledgermod.catalogs(data):
        entries = cat.get("entries") or {}
        c = Counter(e.get("status", "todo") for e in entries.values())
        print(f"카탈로그 {cat['name']}: {len(entries)}항목  "
              + " ".join(f"{k}={v}" for k, v in c.most_common()))
    terms = ledgermod.load_terms(project, data)
    if terms:
        flat = ledgermod.flat_rows(data)
        filled, unresolved = ledgermod.apply_terms(flat, terms)
        print(f"용어표 {len(terms)}어휘 — 행 {filled}개 해결, "
              f"미등록 {len(set(unresolved))}종")
    return 0


# ---- main -------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        prog="jaguk",
        description="이미지 텍스트 로컬라이징 통합 CLI — "
                    "scan → copy → set → extract → erase → inject",
    )
    parser.add_argument("-c", "--config", default="",
                        help=f"설정 파일 (기본: cwd 의 {CONFIG_NAME})")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="프로젝트 생성")
    p.add_argument("directory", nargs="?", default=".")
    p.add_argument("--source", default="",
                   help="대량 원본 트리 — scan 대상, copy 출발지 (보통 프로젝트 밖)")
    p.add_argument("--originals", default="originals",
                   help="작업 원본 — copy 도착지, 파이프라인 입력 (기본 originals)")
    p.add_argument("--data", default="data",
                   help="작업 데이터 저장소 — scan.json·원장 등 (기본 data)")
    p.add_argument("--erased", default="erased",
                   help="텍스트 지운 이미지 — erase 출력 (기본 erased)")
    p.add_argument("--injected", default="injected",
                   help="텍스트 주입 결과 — inject 출력 (기본 injected)")
    p.add_argument("--lang", default="ja", help="OCR 언어 (기본 ja)")
    p.add_argument("--backend", default="auto",
                   choices=("auto", "windows", "tesseract", "easyocr"))
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("configure", help="설정 변경/보기")
    p.add_argument("key", nargs="?", default="",
                   help=f"설정 키: {', '.join(CONFIG_KEYS)} (없으면 현재 설정 표시)")
    p.add_argument("value", nargs="*", help="설정 값")
    p.set_defaults(func=cmd_configure)

    p = sub.add_parser("scan",
                       help="(비활성) 텍스트 파일 필터링 — OCR 누락 위험으로 잠정 중단")
    p.add_argument("--only", default="", help="상대 경로 부분일치 필터")
    p.add_argument("--backend", default="",
                   choices=("", "auto", "windows", "tesseract", "easyocr"))
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("copy", help="(비활성) scan 목록 기반 복사 — 잠정 중단")
    p.add_argument("--only", default="")
    p.add_argument("--force", action="store_true", help="이미 있는 파일도 덮어씀")
    p.set_defaults(func=cmd_copy)

    p = sub.add_parser("set",
                       help="파일/디렉토리 처리 규칙 (대상은 originals 안 경로)")
    p.add_argument("target", help="originals(작업 원본) 안의 파일/디렉토리")
    p.add_argument("--text-only", action="store_true",
                   help="이미지 전체가 글자 — 카탈로그로 처리 (base 불필요)")
    p.add_argument("--same-pattern", action="store_true",
                   help="무리 전체가 같은 스타일")
    p.add_argument("--row", nargs=2, action="append", metavar=("N", "ROLE"),
                   help="N번째 줄 역할: ref(원문 유지·앵커)|replace(지우고 주입)|ignore")
    p.add_argument("--multicolumn", action="store_true",
                   help="한 줄에 여러 항목 — 열별로 ref-replace 짝짓기")
    p.add_argument("--ignore", action="store_true", help="처리 제외")
    p.add_argument("--dict", default="", help="이 무리의 번역 용어표 파일")
    p.add_argument("--style", default="", help="적용할 스타일 이름")
    p.add_argument("--clear", action="store_true", help="규칙 제거")
    p.set_defaults(func=cmd_set)

    p = sub.add_parser("extract",
                       help="마킹된 파일을 규칙대로 본 OCR 해 원장에 기록")
    p.add_argument("--only", default="")
    p.add_argument("--backend", default="",
                   choices=("", "auto", "windows", "tesseract", "easyocr"))
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("erase", help="텍스트 지우기 (→ erased)")
    p.add_argument("--only", default="")
    p.add_argument("--method", default="auto",
                   choices=("auto", "inpaint", "median", "alpha", "fill"))
    p.add_argument("--color", default="", help="fill 방식 채움색 #RRGGBB")
    p.add_argument("--pad", type=int, default=2)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_erase)

    p = sub.add_parser("inject", help="번역 주입 렌더 (→ out)")
    p.add_argument("--status", action="append")
    p.add_argument("--only", default="")
    p.add_argument("--list", action="store_true")
    p.add_argument("--on-original", action="store_true")
    p.set_defaults(func=cmd_inject)

    p = sub.add_parser("status", help="진행 상황")
    p.set_defaults(func=cmd_status)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
