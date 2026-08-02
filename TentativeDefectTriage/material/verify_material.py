#!/usr/bin/env python3
"""자료 검증기 — 분석자료가 갱신됐을 때 소비자 가정이 여전히 성립하는지 확인한다.

설계 문서: `../신규 문제 분석 파이프라인 설계.md` §4 "자료 규약"

언제 돌리나
-----------
- 자료 저장소를 `git pull` 한 직후
- 파이프라인 실행 전 게이트로(가볍다 — 파일 읽기뿐, LLM 호출 없음)
- 새 모듈·칩이 추가됐을 때

무엇을 보나 (세 층)
-------------------
1. **선언 ↔ 실제** — 매니페스트가 말한 컬럼·파일이 실제로 있는가
2. **선언 ↔ 소비자 능력** — `schema_version` 을 아는가, 모르는 레벨 어휘나
   미인식 문서 슬롯이 있는가
3. **기준선 대비 변화** — 인덱스 행수·문서 크기·인용 수를 기록해두고 델타를 본다.
   자료 커밋 `d4012348f`(인덱스 904→870행)를 즉시 잡았을 층이다.

3층이 특히 중요하다: 1·2 를 통과해도 자료 **내용**이 바뀌면 측정치와 발췌 결과가
달라진다. 그 변화가 의도된 것인지는 사람이 판단해야 하므로, 검증기는 "달라졌다"를
드러내는 데까지만 책임진다.

종료 코드
---------
- 0 : 이상 없음
- 1 : 경고 — 기준선 변화, 모르는 어휘·슬롯 (사람 판단 필요)
- 2 : 오류 — 구조 위반, 모르는 schema_version (고치기 전엔 결과를 믿으면 안 됨)

사용법
------
    # 검증 (기준선이 있으면 비교)
    ./verify_material.py --material-root /path/SamsungDTV_Analysis

    # 기준선 기록/갱신 — 변화를 확인하고 받아들이기로 했을 때
    ./verify_material.py --material-root /path/... --update-baseline

    # 자료 저장소에 넣을 매니페스트 초안 생성
    ./verify_material.py --emit-manifest MANIFEST.json

의존성: Python 3 표준 라이브러리만.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from excerpt import load_docs, parse_sections  # noqa: E402
from material_contract import (  # noqa: E402
    SUPPORTED_SCHEMA_VERSIONS, Contract, conceptual_docs, load_contract,
    resolve_doc, write_default_manifest,
)
from probe_match_rate import read_text  # noqa: E402

BASELINE_NAME = "material_baseline.json"

# 자료 루트에서 <module>/<chip>/ 을 찾을 때 뒤지는 하위 경로.
ANALYSIS_SUBDIR = "analysis"


class Findings:
    """검증 결과 누적기. 층위별로 분리해 종료 코드를 정한다."""

    def __init__(self) -> None:
        self.errors: list[str] = []     # 구조 위반 → exit 2
        self.warnings: list[str] = []   # 사람 판단 필요 → exit 1
        self.notes: list[str] = []      # 정보

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def note(self, msg: str) -> None:
        self.notes.append(msg)

    def exit_code(self) -> int:
        if self.errors:
            return 2
        return 1 if self.warnings else 0


def discover_chips(material_root: Path) -> list[tuple[str, Path]]:
    """자료 루트에서 (module_name, chip_dir) 목록을 찾는다."""
    base = material_root / ANALYSIS_SUBDIR
    if not base.is_dir():
        return []
    out: list[tuple[str, Path]] = []
    for module_dir in sorted(p for p in base.iterdir() if p.is_dir()):
        for chip_dir in sorted(p for p in module_dir.iterdir() if p.is_dir()):
            if (chip_dir / "11_log_index.tsv").is_file():
                out.append((module_dir.name, chip_dir))
    return out


def check_index(chip_dir: Path, c: Contract, f: Findings) -> dict:
    """로그 인덱스의 컬럼 선언과 실제를 대조하고 지표를 뽑는다."""
    path = chip_dir / "11_log_index.tsv"
    lines = read_text(path).splitlines()
    if not lines:
        f.error(f"{path}: 비어 있다")
        return {}

    header = lines[0].split("\t")
    if header != c.columns:
        f.error(
            f"{path}: 컬럼이 선언과 다르다\n"
            f"    선언: {c.columns}\n"
            f"    실제: {header}"
        )
        return {}

    rows = [l.split("\t") for l in lines[1:] if l.strip()]
    rows = [r for r in rows if len(r) >= len(c.columns)]

    levels = Counter(r[2] for r in rows)
    unknown = sorted(set(levels) - set(c.known_levels))
    if unknown:
        f.warn(
            f"{path}: 모르는 level 어휘 {unknown} — "
            f"제외 대상({c.excluded_levels})에 포함시켜야 하는지 확인 필요"
        )

    usable = [r for r in rows if r[2] not in c.excluded_levels]
    return {
        "rows_total":   len(rows),
        "rows_usable":  len(usable),
        "levels":       dict(levels),
    }


def check_conceptual(module_root: Path, chip_dir: Path, c: Contract, f: Findings) -> dict:
    """개념 계층 선언이 실제 자료로 해소되는지 확인한다.

    왜 이 검사가 있나
    -----------------
    **이 검사가 없어서 실제로 놓쳤다.** 2026-08-02 자료 갱신으로 DP 에
    `log_analysis/` 가 신설됐는데, 우리가 경로에 번호를 박아 둔 탓에
    (`05_context_and_correlation.md` ↔ DP 는 `04_…`) 상관 문서가 계속 "없음"으로
    빠져 있었다. 검증기는 인덱스·문서 구조만 보고 **개념 계층 선언은 보지 않아서**
    파이프라인을 실제로 돌려 보기 전까지 드러나지 않았다.

    규약 검증기의 목적은 "선언과 실제가 맞는가"다. 개념 계층도 선언이다.
    """
    docs, missing = conceptual_docs(module_root, chip_dir, c)
    for m in missing:
        # 누락은 오류가 아니다 — 모듈마다 자료 구성이 다를 수 있다. 다만 **보이게**
        # 한다. 조용히 빠지는 것이 이 검사가 막으려는 바로 그것이다.
        f.warn(f"[{module_root.name}/{chip_dir.name}] 개념 계층 — {m}")

    declared = (1 if c.conceptual.get("glossary") else 0) \
        + len(c.conceptual.get("module_docs", [])) \
        + len(c.conceptual.get("chip_docs", []))
    if not docs:
        f.error(f"[{module_root.name}/{chip_dir.name}] 개념 계층이 **전부** 비었다 "
                f"(선언 {declared}종) — 용어·구조 없이 로그를 읽게 된다")
    return {
        "conceptual_docs": len(docs),
        "conceptual_chars": sum(len(t) for _, t in docs),
    }


def check_docs(chip_dir: Path, c: Contract, f: Findings) -> dict:
    """문서 구조·인용·통짜유지 파일 존재를 확인하고 지표를 뽑는다."""
    md_files = sorted(chip_dir.glob(c.doc_glob))
    if not md_files:
        f.error(f"{chip_dir}: `{c.doc_glob}` 에 해당하는 문서가 없다")
        return {}

    # 통짜 유지 대상이 실재하는가 — 없으면 조용히 발췌돼 맥락이 깨진다.
    names = {p.name for p in md_files}
    missing = [n for n in c.always_whole if n not in names]
    if missing:
        f.warn(
            f"{chip_dir}: 통짜 유지 대상 {missing} 가 없다 — "
            f"파일명 규약이 바뀌었다면 매니페스트를 갱신할 것"
        )

    # 아는 슬롯 번호가 아닌 문서 — 새 문서가 추가됐을 수 있다(사람 판단).
    unknown_docs = [
        p.name for p in md_files
        if not p.name.startswith(c.skip_prefix)
        and c.slot_of(p.name) not in c.known_slots
        and p.name not in c.always_whole
    ]
    if unknown_docs:
        f.warn(
            f"{chip_dir}: 모르는 문서 {unknown_docs} — "
            f"발췌 대상에 넣을지, 통짜로 둘지 판단 필요"
        )

    sections, whole, total_chars = load_docs(chip_dir)
    cite_count = sum(len(s.citations) for s in sections)

    if not sections:
        f.error(f"{chip_dir}: 섹션 파싱 결과가 0개 — 헤딩 구조가 바뀌었을 수 있다")
    elif cite_count == 0:
        f.error(
            f"{chip_dir}: 인용이 하나도 파싱되지 않았다 — 인용 형식이 바뀌었을 수 있다. "
            f"이 상태로는 § 발췌가 전부 실패한다"
        )

    # 헤딩이 전혀 없어 문서 전체가 § 하나가 된 경우 — 동작은 하나 발췌가 무의미해진다.
    flat = [s.doc for s in sections if s.heading == "(전체)"]
    if flat:
        f.warn(f"{chip_dir}: 헤딩이 없어 통짜 § 가 된 문서 {sorted(set(flat))}")

    return {
        "docs":        len(md_files),
        "sections":    len(sections),
        "citations":   cite_count,
        "chars_total": total_chars,
        "whole_chars": sum(w["chars"] for w in whole),
    }


def compare_baseline(current: dict, baseline: dict | None, f: Findings) -> None:
    """기준선과 현재 지표를 대조해 변화를 드러낸다.

    변화가 곧 오류는 아니다 — 자료 갱신은 대체로 개선이다. 다만 **모르는 사이**
    달라지는 것을 막는 것이 목적이므로 경고로 올린다.
    """
    if baseline is None:
        f.note("기준선이 없다 — `--update-baseline` 으로 현재 상태를 기록해 두면 "
               "다음 갱신 때 변화를 감지할 수 있다")
        return

    for key in sorted(current):
        cur = current[key]
        old = baseline.get(key)
        if old is None:
            f.warn(f"[{key}] 기준선에 없던 대상이다 (새 모듈/칩 추가?)")
            continue
        deltas = []
        for metric in ("rows_total", "rows_usable", "docs", "sections",
                       "citations", "chars_total",
                       # 개념 계층도 기준선에 넣는다 — 종수가 줄어드는 것은
                       # "자료가 있는데 못 읽는" 상태의 신호다(2026-08-02 실제 사례).
                       "conceptual_docs", "conceptual_chars"):
            a, b = old.get(metric), cur.get(metric)
            if a is not None and b is not None and a != b:
                deltas.append(f"{metric} {a}→{b}")
        if deltas:
            f.warn(f"[{key}] 기준선 대비 변화: {', '.join(deltas)}")

    for key in sorted(set(baseline) - set(current)):
        f.warn(f"[{key}] 기준선에 있으나 지금은 없다 (모듈/칩 제거?)")


def main() -> None:
    ap = argparse.ArgumentParser(description="분석자료 규약 검증기")
    ap.add_argument("--material-root", type=Path, default=None,
                    help="자료 저장소 루트 (analysis/ 를 담고 있는 디렉토리)")
    ap.add_argument("--baseline", type=Path, default=None,
                    help=f"기준선 파일 (기본: 스크립트 옆 {BASELINE_NAME})")
    ap.add_argument("--update-baseline", action="store_true",
                    help="현재 상태를 기준선으로 기록한다 (변화를 받아들일 때)")
    ap.add_argument("--emit-manifest", type=Path, default=None,
                    help="내장 기본값을 매니페스트 초안으로 내보내고 종료")
    args = ap.parse_args()

    if args.emit_manifest:
        write_default_manifest(args.emit_manifest)
        print(f"매니페스트 초안 기록: {args.emit_manifest}")
        print("자료 저장소 루트(또는 analysis/)에 두면 소비자가 이를 읽는다.")
        return

    if not args.material_root:
        sys.exit("--material-root 가 필요하다 (또는 --emit-manifest)")
    if not args.material_root.is_dir():
        sys.exit(f"자료 루트를 찾을 수 없음: {args.material_root}")

    f = Findings()
    contract = load_contract(args.material_root)
    for w in contract.warnings:
        # schema_version 불일치는 구조 파싱의 신뢰를 깨므로 오류로 올린다.
        (f.error if "schema_version" in w else f.warn)(w)

    print(f"규약 출처: {contract.source} (schema_version={contract.schema_version}, "
          f"지원={SUPPORTED_SCHEMA_VERSIONS})")

    chips = discover_chips(args.material_root)
    if not chips:
        f.error(f"{args.material_root}/{ANALYSIS_SUBDIR} 아래에서 칩 디렉토리를 찾지 못했다")

    current: dict = {}
    for module, chip_dir in chips:
        key = f"{module}/{chip_dir.name}"
        idx = check_index(chip_dir, contract, f)
        doc = check_docs(chip_dir, contract, f)
        con = check_conceptual(chip_dir.parent, chip_dir, contract, f)
        current[key] = {**idx, **doc, **con}
        tag = contract.driver_tag_for(chip_dir.parent)
        if tag is None:
            f.warn(f"[{key}] 모듈 `{module}` 의 driver_tag 선언이 없다 — "
                   f"모집단 기준 매칭률을 낼 수 없다")

    baseline_path = args.baseline or (Path(__file__).resolve().parent / BASELINE_NAME)
    baseline = None
    if baseline_path.is_file():
        try:
            baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        except Exception as e:
            f.warn(f"기준선 파싱 실패({e}) — 비교를 건너뛴다")

    if args.update_baseline:
        baseline_path.write_text(
            json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        print(f"기준선 기록: {baseline_path} ({len(current)}개 대상)")
    else:
        compare_baseline(current, baseline, f)

    print()
    for label, items in (("오류", f.errors), ("경고", f.warnings), ("참고", f.notes)):
        for m in items:
            print(f"[{label}] {m}")

    print()
    print(f"검사 대상 {len(current)}개 | 오류 {len(f.errors)} · 경고 {len(f.warnings)}")
    if not f.errors and not f.warnings:
        print("이상 없음 — 소비자 가정이 자료와 일치한다.")

    sys.exit(f.exit_code())


if __name__ == "__main__":
    main()
