#!/usr/bin/env python3
"""§ 발췌 추출기 — 관측된 file:line 으로 분석 문서의 관련 섹션만 뽑는다.

설계 문서: `../신규 문제 분석 파이프라인 설계.md` §4 Stage 2 (3단계 중 3번)

성격
----
**이것은 측정 전용 도구가 아니라 Stage 2 의 실제 부품이다.** 지금은 CLI 로
발췌 크기·축소율을 재는 데 쓰고, Phase 2 에서 파이프라인이 같은 모듈을
호출한다 — 같은 로직을 두 번 만들지 않기 위해서다. 신규 코드베이스 디렉토리가
정해지면(Phase 0) 그쪽으로 옮긴다.

왜 발췌인가
-----------
문서 통짜 주입은 실측에서 예산을 초과했다(rheam 258,595자 vs 입력 여유 약
133k 토큰). 문서에 `file:line` 인용이 풍부하므로(rheam 총 1,141건) 관측된
`file:line` 과 겹치는 § 만 뽑으면 결정론적으로 축소할 수 있다 — 로그 앵커
매칭에서 이미 91.6% 로 검증된 것과 같은 조인 키다.

인용 형식 (실제 rheam 문서 집계 기준)
------------------------------------
- 전체 경로 + 라인      `sdp_frc/rheam/sdp_frc_irq.c:519`   (1,090건, 주류)
- 범위                  `sdp_frc/rheam/sdp_frc_drv.c:1709-1712`  (543건 — 필수 처리)
- 파일명만 + 라인       `clk_rheam.c:104`                   (51건)
- 축약형                `(:1609)`                           (27건, 직전 파일 문맥)

라인 동등 비교만으로는 대부분 실패한다 — 범위 포함 판정과 근접도(window)가 필요하다.

사용법
------
    # 로그에서 관측 집합을 직접 도출 (권장 — 사내에서 이대로 실행)
    ./excerpt.py --log dmesg.txt \
                 --index /path/analysis/sdp_frc/rheam/11_log_index.tsv \
                 --docs-dir /path/analysis/sdp_frc/rheam \
                 --driver-tag '\[S_F\]' --out excerpt_report.md

    # 관측 집합을 파일로 주는 경우 ("경로:라인" 한 줄에 하나)
    ./excerpt.py --observed observed.txt --docs-dir /path/.../rheam

    # 발췌된 § 본문까지 저장 (프롬프트 주입 형태 확인용)
    ./excerpt.py ... --dump-excerpt excerpt_body.md

의존성: Python 3 표준 라이브러리만. 같은 디렉토리의 `probe_match_rate.py` 를 재사용한다.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from probe_match_rate import (  # noqa: E402
    EXCLUDE_LEVELS_DEFAULT, load_index, measure, read_text,
)

# 발췌하지 않고 통짜로 유지하는 문서.
# 근거: 인용이 적지만(rheam 기준 각 15·17건) 큐레이션된 의미 해석이라 쪼개면
# 맥락이 깨진다. 둘 다 작아서(rheal 기준 6.9k+6.1k자) 예산 부담도 없다.
ALWAYS_WHOLE = ("10_summary_and_findings.md", "11_log_triage.md")

# 라인 근접 허용 폭 기본값.
#
# 실측(rheam, 관측집합 149개)에서 window 가 주입량을 크게 좌우한다:
#   window=0   → 2.8배 축소 (93,685자, § 46/226)
#   window=10  → 2.1배 축소 (121,784자, § 64/226)
#   window=40  → 1.8배 축소 (143,405자, § 81/226)
#   window=100 → 1.7배 축소 (153,212자, § 92/226)
#
# 0 이 가장 작지만, 분석 문서와 로그인덱스는 각각 생성되므로 소스 변경 시
# 라인 번호가 어긋날 수 있다(설계 문서가 "소스가 바뀌면 인덱스·라인이
# 어긋난다"고 경고하는 그 문제). 약간의 여유로 드리프트를 흡수하면서
# 예산에도 들어오는 10 을 기본값으로 둔다.
DEFAULT_WINDOW = 10

_RE_HEADING  = re.compile(r"^(#{2,6})\s+(.*)$")
# 전체경로/파일명 + 라인(+범위). 앞에 경로 구분자가 있어도 되고 없어도 된다.
_RE_CITATION = re.compile(r"([A-Za-z0-9_][A-Za-z0-9_/.\-]*\.[ch])[:\s]*:?(\d+)(?:-(\d+))?")
# 축약형 (:1609) / (:1709-1712) — 직전에 언급된 파일에 귀속시킨다.
_RE_SHORTHAND = re.compile(r"\(:(\d+)(?:-(\d+))?\)")


@dataclass(frozen=True)
class Citation:
    """문서가 인용한 코드 위치. `end`는 단일 라인이면 `start`와 같다."""
    path: str        # 문서에 쓰인 그대로 (전체 경로일 수도, 파일명만일 수도)
    start: int
    end: int

    @property
    def basename(self) -> str:
        return self.path.rsplit("/", 1)[-1]


@dataclass
class Section:
    """`##`/`###` 단위 섹션 — 발췌의 단위."""
    doc: str
    heading: str
    level: int
    text: str
    citations: list[Citation] = field(default_factory=list)

    @property
    def chars(self) -> int:
        return len(self.text)

    @property
    def lines(self) -> int:
        return len(self.text.splitlines())

    def label(self) -> str:
        return f"{self.doc} § {self.heading}"


def parse_citations(text: str) -> list[Citation]:
    """섹션 본문에서 인용을 추출한다. 축약형은 직전 파일에 귀속시킨다.

    본문을 앞에서 뒤로 훑으며 마지막으로 등장한 명시적 파일을 기억해,
    `(:1609)` 형태를 그 파일의 인용으로 해석한다 — 문서가 연속 참조를 그렇게
    쓰기 때문이다.
    """
    cites: list[Citation] = []
    # (위치, 종류, 매치) 로 모아 문서 순서대로 처리해야 축약형 귀속이 맞다.
    events: list[tuple[int, str, re.Match]] = []
    for m in _RE_CITATION.finditer(text):
        events.append((m.start(), "full", m))
    for m in _RE_SHORTHAND.finditer(text):
        events.append((m.start(), "short", m))
    events.sort(key=lambda e: e[0])

    last_path: str | None = None
    for _pos, kind, m in events:
        if kind == "full":
            path  = m.group(1)
            start = int(m.group(2))
            end   = int(m.group(3)) if m.group(3) else start
            last_path = path
        else:
            if last_path is None:
                continue          # 문맥 없는 축약형 — 귀속 불가, 버린다
            path  = last_path
            start = int(m.group(1))
            end   = int(m.group(2)) if m.group(2) else start
        if end < start:
            start, end = end, start
        cites.append(Citation(path=path, start=start, end=end))
    return cites


def parse_sections(doc_name: str, text: str) -> list[Section]:
    """문서를 `##` 이상 헤딩 기준으로 섹션 분할한다.

    첫 헤딩 앞의 도입부(제목·날짜·대상 등)는 `(도입부)` 섹션으로 보존한다 —
    빌드 설정 전제가 여기 있는 경우가 있어 버리면 안 된다.
    """
    lines = text.splitlines()
    marks: list[tuple[int, int, str]] = []   # (라인 idx, level, heading)
    for i, line in enumerate(lines):
        m = _RE_HEADING.match(line)
        if m:
            marks.append((i, len(m.group(1)), m.group(2).strip()))

    sections: list[Section] = []

    def _add(heading: str, level: int, body: list[str]) -> None:
        body_text = "\n".join(body).strip()
        if not body_text:
            return
        sections.append(Section(
            doc=doc_name, heading=heading, level=level,
            text=body_text, citations=parse_citations(body_text),
        ))

    if not marks:
        _add("(전체)", 0, lines)
        return sections

    if marks[0][0] > 0:
        _add("(도입부)", 0, lines[:marks[0][0]])

    for idx, (start, level, heading) in enumerate(marks):
        end = marks[idx + 1][0] if idx + 1 < len(marks) else len(lines)
        _add(heading, level, lines[start:end])

    return sections


def load_docs(docs_dir: Path) -> tuple[list[Section], list[dict], int]:
    """문서 디렉토리를 로드해 (발췌대상 섹션, 통짜유지 문서, 전체 자수)를 반환한다.

    `_` 접두 파일은 생산 중간 산출물이라 제외한다(설계 §4).
    """
    sections: list[Section] = []
    whole: list[dict] = []
    total_chars = 0

    for p in sorted(docs_dir.glob("*.md")):
        if p.name.startswith("_"):
            continue
        text = read_text(p)
        total_chars += len(text)
        if p.name in ALWAYS_WHOLE:
            whole.append({"name": p.name, "chars": len(text), "lines": len(text.splitlines())})
        else:
            sections.extend(parse_sections(p.name, text))

    return sections, whole, total_chars


def _same_file(cite_path: str, obs_path: str) -> bool:
    """두 경로가 같은 파일을 가리키는지 판정한다.

    **경로 경계를 지켜 비교한다.** 단순 `endswith` 는 오탐을 낸다 —
    `sdp_dp_drv.c`.endswith(`drv.c`) 가 True 이지만 서로 다른 파일이다.
    `sdp_drm-dp/README.md` §4.2 가 기록한 실제 사고(`drv.c:542` 프레임워크와
    `drv.c:3098` 디바이스가 한 문서에서 같은 표기로 다른 파일을 지칭)가 바로
    이 부류다. 접미 일치는 반드시 `/` 경계에서만 인정한다.

    양쪽 다 디렉토리를 가지면 경계 있는 접미 일치, 한쪽이라도 파일명뿐이면
    파일명 완전 일치로 판정한다(부분 일치는 인정하지 않는다).
    """
    c_base = cite_path.rsplit("/", 1)[-1]
    o_base = obs_path.rsplit("/", 1)[-1]
    if "/" in cite_path and "/" in obs_path:
        if cite_path == obs_path:
            return True
        return (cite_path.endswith("/" + obs_path)
                or obs_path.endswith("/" + cite_path))
    return c_base == o_base


def _match_tier(cite: Citation, obs_path: str, obs_line: int, window: int) -> str | None:
    """인용과 관측 위치의 일치 등급. 불일치면 None.

    경로 비교: 양쪽 모두 경로를 가지면 접미 일치(suffix)로 본다 — 문서 인용은
    모듈 루트부터, 로그인덱스도 같은 규격이지만 축약 인용이 섞여 있어
    파일명만 있는 경우가 있다(rheam 51건). 그때는 파일명 일치로 떨어뜨린다.
    """
    if not _same_file(cite.path, obs_path):
        return None

    if cite.start <= obs_line <= cite.end:
        return "exact"
    if cite.start - window <= obs_line <= cite.end + window:
        return "near"
    return "file_only"


def select_sections(
    sections: list[Section],
    observed: set[tuple[str, int]],
    window: int,
) -> tuple[list[Section], dict]:
    """관측된 file:line 과 겹치는 § 를 고른다.

    등급: exact(인용 범위 안) > near(window 내) > file_only(같은 파일, 먼 라인).
    exact/near 를 1차로 채택하고, **exact/near 가 하나도 없는 파일**에 대해서만
    file_only 를 fallback 으로 채택한다 — 같은 파일이라는 이유로 전부 끌어오면
    발췌 의미가 사라지므로, 건질 게 없을 때만 쓴다.
    """
    best: dict[int, str] = {}                       # section idx -> 최고 등급
    by_file_hit: dict[str, bool] = defaultdict(bool)  # 파일별 exact/near 존재 여부
    file_only_idx: dict[str, set[int]] = defaultdict(set)

    rank = {"exact": 3, "near": 2, "file_only": 1}

    for i, sec in enumerate(sections):
        for cite in sec.citations:
            for obs_path, obs_line in observed:
                tier = _match_tier(cite, obs_path, obs_line, window)
                if tier is None:
                    continue
                key = obs_path.rsplit("/", 1)[-1]
                if tier in ("exact", "near"):
                    by_file_hit[key] = True
                    if rank[tier] > rank.get(best.get(i, ""), 0):
                        best[i] = tier
                else:
                    file_only_idx[key].add(i)

    selected_idx = set(best)

    # fallback: exact/near 로 아무 § 도 못 건진 파일만 file_only 를 쓴다.
    fallback_files: list[str] = []
    for fname, idxs in file_only_idx.items():
        if not by_file_hit.get(fname):
            fallback_files.append(fname)
            selected_idx |= idxs
            for i in idxs:
                best.setdefault(i, "file_only")

    observed_files = {p.rsplit("/", 1)[-1] for p, _ in observed}
    unmatched_files = sorted(
        f for f in observed_files
        if not by_file_hit.get(f) and f not in file_only_idx
    )

    selected = [sections[i] for i in sorted(selected_idx)]
    stats = {
        "tier_counts":     Counter(best[i] for i in sorted(selected_idx)),
        "fallback_files":  sorted(fallback_files),
        "unmatched_files": unmatched_files,
        "observed_files":  len(observed_files),
    }
    return selected, stats


def observed_from_log(args) -> tuple[set[tuple[str, int]], dict]:
    """로그 + 인덱스로 관측 file:line 집합을 만든다 (probe_match_rate 재사용)."""
    exclude = tuple(x.strip() for x in args.exclude_levels.split(",") if x.strip())
    index_used, _dropped, _lvl = load_index(args.index, args.min_key_len, exclude)
    log_lines = read_text(args.log).splitlines()
    tag = re.compile(args.driver_tag) if args.driver_tag else None
    m = measure(log_lines, index_used, tag)

    observed: set[tuple[str, int]] = set()
    for loc in m["file_line_hits"]:
        path, _, line = loc.rpartition(":")
        if path and line.isdigit():
            observed.add((path, int(line)))
    return observed, m


def observed_from_file(path: Path) -> set[tuple[str, int]]:
    """"경로:라인" 목록 파일에서 관측 집합을 읽는다."""
    observed: set[tuple[str, int]] = set()
    for raw in read_text(path).splitlines():
        raw = raw.strip()
        if not raw or raw.startswith("#"):
            continue
        p, _, ln = raw.rpartition(":")
        if p and ln.isdigit():
            observed.add((p, int(ln)))
    return observed


def build_report(args, observed, sections, whole, total_chars, selected, stats, m) -> str:
    L: list[str] = []
    add = L.append

    excerpt_chars = sum(s.chars for s in selected)
    whole_chars   = sum(w["chars"] for w in whole)
    injected      = excerpt_chars + whole_chars
    ratio         = total_chars / injected if injected else 0.0

    add("# § 발췌 크기 측정 결과")
    add("")
    add("설계 문서 `../신규 문제 분석 파이프라인 설계.md` §4 Stage 2 · §9 Phase 2 잔여 체크리스트.")
    add("")
    add("## 입력")
    add("")
    add(f"- 문서 디렉토리: `{args.docs_dir}`")
    if args.log:
        add(f"- 로그: `{args.log}`")
        add(f"- 인덱스: `{args.index}`")
        add(f"- 드라이버 태그: `{args.driver_tag or '(미지정)'}`")
    else:
        add(f"- 관측 집합 파일: `{args.observed}`")
    add(f"- 라인 근접 window: {args.window}")
    add(f"- 관측된 고유 `file:line`: **{len(observed)}개** (파일 {stats['observed_files']}종)")
    add("")

    add("## 핵심 — 축소율")
    add("")
    add("| 항목 | 자수 |")
    add("| --- | --- |")
    add(f"| 통짜 주입(전체 문서) | {total_chars:,} |")
    add(f"| 발췌된 § 합계 | {excerpt_chars:,} |")
    add(f"| 통짜 유지 문서(10·11) | {whole_chars:,} |")
    add(f"| **실제 주입량** | **{injected:,}** |")
    add(f"| **축소율** | **{ratio:.1f}배** |")
    add("")
    add(f"발췌된 § : **{len(selected)}개** / 전체 {len(sections)}개")
    add("")

    add("## 매칭 등급 분포")
    add("")
    add("| 등급 | § 수 | 의미 |")
    add("| --- | --- | --- |")
    tc = stats["tier_counts"]
    add(f"| exact | {tc.get('exact', 0)} | 관측 라인이 인용 범위 안 |")
    add(f"| near | {tc.get('near', 0)} | window({args.window}줄) 내 |")
    add(f"| file_only | {tc.get('file_only', 0)} | 같은 파일이지만 라인이 멀다 (fallback) |")
    add("")
    if stats["fallback_files"]:
        add(f"**fallback 발생 파일 {len(stats['fallback_files'])}종** — exact/near 로 § 를 "
            f"못 건져 같은 파일 § 를 끌어온 경우. 비중이 높으면 발췌 정밀도가 낮다는 뜻이다.")
        add("")
        add("```text")
        for f in stats["fallback_files"][:20]:
            add(f)
        add("```")
        add("")
    if stats["unmatched_files"]:
        add(f"**문서에 인용이 전혀 없는 관측 파일 {len(stats['unmatched_files'])}종** — "
            f"이 파일의 로그는 발췌로 맥락을 얻지 못한다.")
        add("")
        add("```text")
        for f in stats["unmatched_files"][:20]:
            add(f)
        add("```")
        add("")

    if m:
        add("## 로그 매칭 (참고 — probe_match_rate 와 동일 지표)")
        add("")
        add(f"- 총 라인 {m['total']} / 모집단 {m['in_population']} / 매칭 {m['matched_total']}")
        add(f"- 위치 모호(복수 file:line): {m['ambiguous_multi_loc']}건")
        add("")

    add("## 발췌된 § 목록")
    add("")
    add("| 문서 | § | 줄 | 자 |")
    add("| --- | --- | --- | --- |")
    for s in selected:
        h = s.heading if len(s.heading) <= 50 else s.heading[:47] + "..."
        add(f"| {s.doc} | {h} | {s.lines} | {s.chars:,} |")
    add("")

    add("## 해석")
    add("")
    add("- 목표는 실제 주입량이 컨텍스트 예산 안에 드는 것이다 — 기존 설정 기준 "
        "`num_ctx: 198000` 에서 `max_tokens: 65535` 를 빼면 입력 여유가 약 133k 토큰. "
        "한국어 혼합 텍스트는 대략 1.5~2.5자/토큰이므로 자수를 그 범위로 나눠 가늠한다.")
    add("- **fallback·미인용 비중이 높으면** 발췌가 정밀하지 않다는 뜻이다. 이때는 "
        "window 조정으로 개선되는지 먼저 보고, 안 되면 문서 내 chunk 검색(임베딩)을 재검토한다(설계 §4).")
    add("- 발췌가 예산에 들어오더라도 **필요한 맥락이 잘려나갔는지**는 자수로 알 수 없다 — "
        "발췌된 § 목록을 사람이 훑어 타당성을 확인할 것(§9 Phase 2 체크리스트).")
    add("")
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser(description="§ 발췌 추출기 — 관측 file:line 으로 관련 섹션만 뽑는다")
    ap.add_argument("--docs-dir", required=True, type=Path, help="칩 문서 디렉토리")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--log", type=Path, help="로그 파일 (인덱스와 함께 관측 집합 도출)")
    src.add_argument("--observed", type=Path, help='"경로:라인" 목록 파일')
    ap.add_argument("--index", type=Path, help="11_log_index.tsv (--log 와 함께 필수)")
    ap.add_argument("--driver-tag", default=None, help=r"드라이버 로그 식별 정규식. 예: '\[S_F\]'")
    ap.add_argument("--min-key-len", type=int, default=8, help="match_key 최소 길이 (기본 8)")
    ap.add_argument("--exclude-level", dest="exclude_levels",
                    default=",".join(EXCLUDE_LEVELS_DEFAULT),
                    help="매칭에서 제외할 level 값(쉼표 구분). 기본 T2D — CONFIG_T2D_DEBUGD "
                         "게이트라 dmesg 기대 로그가 아니다 (sdp_drm-dp README §4.6)")
    ap.add_argument("--window", type=int, default=DEFAULT_WINDOW,
                    help=f"라인 근접 허용 폭 (기본 {DEFAULT_WINDOW})")
    ap.add_argument("--out", type=Path, default=None, help="결과 마크다운 경로 (생략 시 stdout)")
    ap.add_argument("--dump-excerpt", type=Path, default=None,
                    help="발췌된 § 본문을 저장 (프롬프트 주입 형태 확인용)")
    args = ap.parse_args()

    if args.log and not args.index:
        sys.exit("--log 를 쓸 때는 --index 도 필요하다")
    if not args.docs_dir.is_dir():
        sys.exit(f"문서 디렉토리를 찾을 수 없음: {args.docs_dir}")

    sections, whole, total_chars = load_docs(args.docs_dir)
    if not sections:
        sys.exit(f"섹션을 찾지 못했다: {args.docs_dir}")

    m = None
    if args.log:
        observed, m = observed_from_log(args)
    else:
        observed = observed_from_file(args.observed)
    if not observed:
        sys.exit("관측된 file:line 이 없다 — 로그 매칭이 0건이거나 관측 파일이 비었다")

    selected, stats = select_sections(sections, observed, args.window)
    report = build_report(args, observed, sections, whole, total_chars, selected, stats, m)

    if args.out:
        args.out.write_text(report, encoding="utf-8")
        print(f"결과 기록: {args.out}", file=sys.stderr)
    else:
        print(report)

    excerpt_chars = sum(s.chars for s in selected)
    injected = excerpt_chars + sum(w["chars"] for w in whole)
    if injected:
        print(f"축소율: {total_chars / injected:.1f}배  "
              f"(통짜 {total_chars:,}자 → 주입 {injected:,}자, § {len(selected)}/{len(sections)}개)",
              file=sys.stderr)
    else:
        # 주입량 0 = 발췌도 통짜유지도 아무것도 안 걸렸다. 이는 자료 규약이
        # 어긋났을 때 정확히 나타나는 상태이므로(인용 형식 변경, 통짜유지 파일명
        # 변경 등) 경고해야 할 순간이다 — 크래시로 끝내면 안 된다.
        # 원인 진단은 verify_material.py 가 한다.
        print("경고: 주입량이 0이다 — 발췌된 § 도, 통짜 유지 문서도 없다. "
              "관측 위치가 문서와 전혀 안 걸렸거나 자료 규약이 어긋났을 수 있다. "
              "`verify_material.py` 로 규약을 확인할 것.", file=sys.stderr)

    if args.dump_excerpt:
        parts = [f"<!-- 발췌 § {len(selected)}개 -->"]
        for s in selected:
            parts.append(f"\n<!-- {s.label()} -->\n{s.text}")
        for w in whole:
            wp = args.docs_dir / w["name"]
            parts.append(f"\n<!-- {w['name']} (통짜) -->\n{read_text(wp)}")
        args.dump_excerpt.write_text("\n".join(parts), encoding="utf-8")
        print(f"발췌 본문 기록: {args.dump_excerpt}", file=sys.stderr)


if __name__ == "__main__":
    main()
