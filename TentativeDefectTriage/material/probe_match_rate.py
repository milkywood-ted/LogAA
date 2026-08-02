#!/usr/bin/env python3
"""로그인덱스 매칭률 실측 프로브 — 신규 문제 분석 파이프라인 Phase 2 go/no-go 선행 검증.

설계 문서: `../신규 문제 분석 파이프라인 설계.md` §4 Stage 2, §9 Phase 2

무엇을 재는가
-------------
Stage 2 의 핵심 전제는 "정제된 로그 라인을 `11_log_index.tsv` 의 match_key 에
리터럴 매칭해 코드 위치(file:line)와 subsystem 을 얻을 수 있다"는 것이다.
이 전제가 실제 로그에서 성립하는지를 파이프라인을 구현하기 **전에** 측정한다.

분모를 나누는 이유: dmesg 전체를 분모로 쓰면 무관한 커널 라인이 대부분이라
매칭률이 무의미하게 낮게 나온다. 의미 있는 질문은 "이 드라이버에 속한 라인
중 몇 %를 특정할 수 있나"이므로 `--driver-tag` 로 모집단을 좁혀 함께 보고한다.

같이 재는 것: §4 의 두 번째 가정 — "문서가 짧아 통짜 주입 가능"이 성립하는지.
로그가 여러 subsystem 에 걸치면 선택 문서가 늘어 컨텍스트가 커진다.

사용법
------
    # FRC
    ./probe_match_rate.py \
        --log /path/to/dmesg.txt \
        --index /path/to/DTV_soc_driver/analysis/sdp_frc/rheal/11_log_index.tsv \
        --docs-dir /path/to/DTV_soc_driver/analysis/sdp_frc/rheal \
        --driver-tag '\[S_F\]' \
        --out result_frc_rheal.md

    # DP (dmesg 접두가 다르다 — sdp_drm-dp README §4.6)
    ./probe_match_rate.py --log ... --index .../sdp_drm-dp/rheal/11_log_index.tsv \
        --docs-dir .../sdp_drm-dp/rheal --driver-tag '\[DRM-DP' --out result_dp_rheal.md
    # ※ DP dmesg 접두는 '[DRM-DP] ' 이고 **oscarp 만 '[DRM-DP:I]'** 다.
    #    여는 괄호까지만 잡는 '\[DRM-DP' 로 두 변형을 모두 커버한다.
    #    '[sdp_dp]' 는 접두가 아니라 일부 포맷 문자열의 일부이므로 태그로 쓰면 안 된다.

주의 — 사내 로그 취급
--------------------
기본값은 미매칭 라인 샘플을 **출력하지 않는다**(`--samples 0`). 원문 일부를
리포트에 남기려면 `--samples N` 을 명시하되, **업로드 전에 반드시 내용을
확인**할 것. 매칭률 수치 자체에는 로그 원문이 포함되지 않는다.

의존성: Python 3 표준 라이브러리만 사용한다(DTV_soc_driver `analysis/tools` 관례와 동일).
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

# match_key 최소 길이 기본값.
# 근거: rheal 인덱스 532개 키 중 최소 길이가 1이며 ':' '.' '[' '%s' 같은 키가
# 존재한다. 이런 키를 그대로 쓰면 거의 모든 라인에 오탐이 걸려 매칭률이
# 무의미하게 100%에 가까워진다. 중앙값이 21이므로 8은 보수적인 하한이다.
# 제외된 키는 리포트에 별도 집계해 "특정 불가 키" 규모를 드러낸다.
DEFAULT_MIN_KEY_LEN = 8

# 매칭 대상에서 기본 제외하는 `level` 값.
#
# 근거: `analysis/sdp_drm-dp/README.md` §4.6 — "`level` 열이 `T2D` 인 행은 dmesg
# 기대 로그로 세면 안 된다." 전부 `#ifdef CONFIG_T2D_DEBUGD` 안이라 커널 설정에
# 따라 컴파일 자체가 빠지고, `PRINT_T2D` 의 실제 정의도 이 저장소 밖(커널
# t2ddebugd)이라 출력 경로가 미확인이다.
#
# 규모가 작지 않다 — FRC rheam 904행 중 362행(40%), DP rheam 1,791행 중 216행.
# 특히 FRC 의 `debug_t2d` subsystem 은 377행으로 최대 규모라, 제외하지 않으면
# subsystem 분포가 실제 dmesg 와 무관하게 왜곡된다.
EXCLUDE_LEVELS_DEFAULT = ("T2D",)


def read_text(path: Path) -> str:
    """바이트로 읽고 관용적으로 디코드한다.

    DTV_soc_driver 소스·산출물은 ISO-8859/EUC-KR 이 섞여 있어(analysis/tools/README.md)
    strict 디코드는 실패한다. errors='replace' 로 읽어 라인 수를 잃지 않는다.
    """
    return path.read_bytes().decode("utf-8", errors="replace")


def load_index(
    path: Path,
    min_key_len: int,
    exclude_levels: tuple[str, ...] = EXCLUDE_LEVELS_DEFAULT,
) -> tuple[list[dict], list[dict], list[dict]]:
    """11_log_index.tsv 를 로드해 (사용할 항목, 짧아서 제외, 레벨로 제외)을 반환한다.

    컬럼: match_key, format, level, file:line, subsystem
    """
    used: list[dict] = []
    dropped: list[dict] = []
    excluded_by_level: list[dict] = []

    lines = read_text(path).splitlines()
    if not lines:
        sys.exit(f"빈 인덱스 파일: {path}")

    header = lines[0].split("\t")
    if header[:1] != ["match_key"]:
        sys.exit(f"예상과 다른 헤더(첫 컬럼이 match_key 가 아님): {header!r}")

    for raw in lines[1:]:
        if not raw.strip():
            continue
        cols = raw.split("\t")
        if len(cols) < 5:
            continue
        entry = {
            "match_key":  cols[0],
            # 포맷 전문. 같은 `match_key` 가 여러 위치를 가리킬 때 런타임 값을
            # 뽑아 소스와 대조하는 데 쓴다(`disambiguate.py`).
            "format":     cols[1],
            "level":      cols[2],
            "file_line":  cols[3],
            "subsystem":  cols[4],
        }
        # 레벨 제외가 길이 검사보다 먼저다 — T2D 는 애초에 dmesg 후보가 아니므로
        # "짧아서 제외"로 세면 제외 사유가 뒤섞인다.
        if entry["level"] in exclude_levels:
            excluded_by_level.append(entry)
        elif len(entry["match_key"].strip()) < min_key_len:
            # 공백만 있는 키, 길이 미달 키는 오탐원이라 제외한다.
            dropped.append(entry)
        else:
            used.append(entry)

    return used, dropped, excluded_by_level


def measure(log_lines: list[str], index: list[dict], driver_tag: re.Pattern | None) -> dict:
    """라인별 match_key 매칭을 수행하고 집계를 반환한다.

    매칭 방식은 부분문자열 포함이다 — match_key 는 포맷 문자열에서 변수부(%s/%d/%x)를
    떼어낸 리터럴 조각이므로, 런타임 라인은 그 조각을 포함한다
    (`analysis/sdp_frc/README.md` §4-7).
    """
    total = len(log_lines)
    in_population = 0          # driver_tag 를 가진 라인 수 (모집단)
    matched_total = 0          # 전체 기준 매칭 라인 수
    matched_in_population = 0  # 모집단 기준 매칭 라인 수
    ambiguous_multi_key = 0    # 한 라인에 서로 다른 match_key 가 2개 이상 걸린 경우
    ambiguous_multi_loc = 0    # 걸린 키가 복수 file:line 을 가리키는 경우

    subsystem_hits: Counter = Counter()
    file_line_hits: Counter = Counter()
    unmatched_in_population: list[str] = []

    # match_key -> 그 키가 가리키는 file:line 집합 (동일 키가 여러 위치에 존재할 수 있음)
    key_locations: dict[str, set[str]] = defaultdict(set)
    key_subsystems: dict[str, set[str]] = defaultdict(set)
    for e in index:
        key_locations[e["match_key"]].add(e["file_line"])
        key_subsystems[e["match_key"]].add(e["subsystem"])

    keys = list(key_locations)

    for line in log_lines:
        is_pop = bool(driver_tag.search(line)) if driver_tag else False
        if is_pop:
            in_population += 1

        hit_keys = [k for k in keys if k in line]

        if not hit_keys:
            if is_pop:
                unmatched_in_population.append(line)
            continue

        matched_total += 1
        if is_pop:
            matched_in_population += 1

        if len(hit_keys) > 1:
            ambiguous_multi_key += 1

        # 가장 긴 키를 대표로 채택한다 — 짧은 키가 긴 키의 부분문자열인 경우가 있어
        # 더 구체적인(긴) 쪽이 올바른 지목일 가능성이 높다.
        best = max(hit_keys, key=len)
        locs = key_locations[best]
        if len(locs) > 1:
            ambiguous_multi_loc += 1
        for s in key_subsystems[best]:
            subsystem_hits[s] += 1
        for loc in locs:
            file_line_hits[loc] += 1

    return {
        "total":                   total,
        "in_population":           in_population,
        "matched_total":           matched_total,
        "matched_in_population":   matched_in_population,
        "ambiguous_multi_key":     ambiguous_multi_key,
        "ambiguous_multi_loc":     ambiguous_multi_loc,
        "subsystem_hits":          subsystem_hits,
        "file_line_hits":          file_line_hits,
        "unmatched_in_population": unmatched_in_population,
    }


# 번호슬롯 → subsystem 매핑은 설계 §4 의 규칙을 아직 코드로 확정하지 않았으므로,
# 이 프로브는 "어떤 문서가 선택될지"를 추정하지 않고 **후보 문서 전체의 크기**만
# 보고한다. 즉 컨텍스트 상한을 재는 것이며, 실제 선택은 이보다 작다.
ALWAYS_INCLUDED = ("10_summary_and_findings.md", "12_event_sequences.md")


def measure_context(docs_dir: Path) -> dict:
    """문서 통짜 주입 시 컨텍스트 크기(상한)를 측정한다 — §4 두 번째 가정 검증용."""
    if not docs_dir.is_dir():
        return {}

    docs: list[dict] = []
    for p in sorted(docs_dir.glob("*.md")):
        # 생산 중간 산출물(_ 접두)과 로그인덱스 TSV 는 주입 대상이 아니다(§4).
        if p.name.startswith("_"):
            continue
        text = read_text(p)
        docs.append({
            "name":  p.name,
            "lines": len(text.splitlines()),
            "chars": len(text),
            "always": p.name in ALWAYS_INCLUDED,
        })
    return {
        "docs":        docs,
        "total_lines": sum(d["lines"] for d in docs),
        "total_chars": sum(d["chars"] for d in docs),
        "always_chars": sum(d["chars"] for d in docs if d["always"]),
    }


def pct(n: int, d: int) -> str:
    return f"{n / d * 100:.1f}%" if d else "N/A"


def build_report(args, index_used, index_dropped, index_lvl, m, ctx) -> str:
    L: list[str] = []
    add = L.append

    add("# 로그인덱스 매칭률 실측 결과")
    add("")
    add("설계 문서 `../신규 문제 분석 파이프라인 설계.md` §9 Phase 2 go/no-go 검증용 실측치.")
    add("")
    add("## 입력")
    add("")
    add(f"- 로그: `{args.log}`")
    add(f"- 인덱스: `{args.index}`")
    add(f"- 문서 디렉토리: `{args.docs_dir or '(미지정)'}`")
    add(f"- 드라이버 태그: `{args.driver_tag or '(미지정 — 모집단 분모 없음)'}`")
    add(f"- match_key 최소 길이: {args.min_key_len}")
    add("")

    add("## 인덱스 상태")
    add("")
    add(f"- 사용된 match_key: **{len(index_used)}개**")
    add(f"- 레벨로 제외된 행: **{len(index_lvl)}개** "
        f"(`{args.exclude_levels}` — dmesg 기대 로그가 아님, sdp_drm-dp README §4.6)")
    add(f"- 너무 짧아 제외된 키: **{len(index_dropped)}개** "
        f"(길이 < {args.min_key_len} — `:` `.` `%s` 류는 오탐원이라 제외)")
    if index_dropped:
        sample = ", ".join(f"`{e['match_key']}`" for e in index_dropped[:8])
        add(f"  - 제외 예: {sample}")
    add("")
    add("> 제외된 키가 많다면 그만큼 특정 불가한 로그가 존재한다는 뜻이다 — "
        "매칭률과 함께 읽어야 한다.")
    add("")

    add("## 매칭률")
    add("")
    add("| 항목 | 값 |")
    add("| --- | --- |")
    add(f"| 로그 총 라인 | {m['total']} |")
    if args.driver_tag:
        add(f"| 드라이버 태그 보유 라인 (모집단) | {m['in_population']} "
            f"({pct(m['in_population'], m['total'])}) |")
    add(f"| match_key 매칭 라인 (전체 기준) | {m['matched_total']} "
        f"({pct(m['matched_total'], m['total'])}) |")
    if args.driver_tag:
        add(f"| **match_key 매칭 라인 (모집단 기준)** | **{m['matched_in_population']} "
            f"({pct(m['matched_in_population'], m['in_population'])})** |")
    add("")
    add("> **모집단 기준 비율이 핵심 지표다.** 전체 기준 비율은 무관한 커널 로그가 "
        "분모에 섞여 있어 낮게 나오는 것이 정상이다.")
    add("")

    add("## 모호성 (매칭됐지만 위치가 하나로 특정되지 않은 경우)")
    add("")
    add(f"- 한 라인에 복수 match_key 매칭: **{m['ambiguous_multi_key']}건** "
        f"({pct(m['ambiguous_multi_key'], m['matched_total'])} of 매칭)")
    add(f"- 매칭 키가 복수 `file:line` 을 가리킴: **{m['ambiguous_multi_loc']}건** "
        f"({pct(m['ambiguous_multi_loc'], m['matched_total'])} of 매칭)")
    add("")
    add("> 후자는 인덱스 자체의 성질이다(동일 포맷 문자열이 여러 곳에 존재). "
        "`11_log_triage.md` 는 이런 경우 컨텍스트로 구분하라고 안내한다 — "
        "비율이 높으면 Stage 2 가 위치를 단정하지 않도록 설계에 반영해야 한다.")
    add("")

    add("## subsystem 분포 (Stage 2 문서 선택의 입력)")
    add("")
    if m["subsystem_hits"]:
        add("| subsystem | 매칭 라인 수 |")
        add("| --- | --- |")
        for s, c in m["subsystem_hits"].most_common():
            add(f"| {s} | {c} |")
        add("")
        add(f"→ 관측된 subsystem 종류: **{len(m['subsystem_hits'])}종**")
    else:
        add("(매칭 없음)")
    add("")
    add("> 종류가 많으면 Stage 2 가 선택할 문서가 늘어 컨텍스트가 커진다 — "
        "아래 컨텍스트 크기와 함께 읽을 것.")
    add("")

    if m["file_line_hits"]:
        add("## 상위 지목 코드 위치")
        add("")
        add("| file:line | 매칭 라인 수 |")
        add("| --- | --- |")
        for loc, c in m["file_line_hits"].most_common(15):
            add(f"| `{loc}` | {c} |")
        add("")

    if ctx:
        add("## 컨텍스트 크기 (§4 \"문서 통짜 주입\" 가정 검증)")
        add("")
        add(f"- 후보 문서 전체: **{len(ctx['docs'])}개, {ctx['total_lines']}줄, "
            f"{ctx['total_chars']:,}자** ← 전부 주입 시의 상한")
        add(f"- 항상 포함되는 문서(10·12)만: **{ctx['always_chars']:,}자**")
        add("")
        add("| 문서 | 줄 | 자 | 항상포함 |")
        add("| --- | --- | --- | --- |")
        for d in ctx["docs"]:
            add(f"| {d['name']} | {d['lines']} | {d['chars']:,} | {'✓' if d['always'] else ''} |")
        add("")
        add("> 이 프로브는 번호슬롯→subsystem 매핑을 아직 구현하지 않으므로 "
            "**상한만** 보고한다. 실제 선택은 이보다 작다. "
            "상한이 이미 컨텍스트 한계를 넘으면 통짜 주입 가정을 재검토해야 한다.")
        add("")

    if args.samples and m["unmatched_in_population"]:
        add(f"## 미매칭 라인 샘플 (모집단 내, 최대 {args.samples}건)")
        add("")
        add("⚠️ **업로드 전 내용을 확인할 것** — 사내 로그 원문이 포함된다.")
        add("")
        add("```text")
        for line in m["unmatched_in_population"][:args.samples]:
            add(line)
        add("```")
        add("")
    elif m["unmatched_in_population"]:
        add(f"## 미매칭 라인")
        add("")
        add(f"모집단 내 미매칭 **{len(m['unmatched_in_population'])}건**. "
            f"원문 샘플은 `--samples N` 으로 요청할 수 있다(기본 비출력 — 사내 로그 보호).")
        add("")

    add("## 해석 기준 (측정 전에 미리 정해둔 것)")
    add("")
    add("사후 합리화를 막기 위해 데이터를 보기 전에 기준을 적어 둔다. "
        "경험적 근거가 없는 **(추정)** 값이므로 실측 후 조정될 수 있다.")
    add("")
    add("- 모집단 기준 매칭률이 **높다(추정 80%↑)** → Stage 2 전제 성립. Phase 3 으로 진행.")
    add("- **중간(추정 50~80%)** → 성립하나 미매칭 처리(Q4)가 중요해진다. 진행하되 "
        "미매칭 라인을 LLM 에 어떻게 넘길지 설계 보강.")
    add("- **낮다(추정 50% 미만)** → 전제가 흔들린다. 원인 구분 필요: "
        "(a) 인덱스 커버리지 부족 → `extract_logs.py` 재생성/보강, "
        "(b) 로그가 다른 빌드/칩 → 자료 갱신, "
        "(c) 구조적 한계 → 보류했던 임베딩 레이어(§4) 재검토.")
    add("")
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="로그인덱스 매칭률 실측 프로브 (신규 문제 분석 파이프라인 Phase 2 선행 검증)",
    )
    ap.add_argument("--log", required=True, type=Path, help="측정할 로그 파일 (dmesg 또는 inlog)")
    ap.add_argument("--index", required=True, type=Path, help="11_log_index.tsv 경로")
    ap.add_argument("--docs-dir", type=Path, default=None,
                    help="칩 문서 디렉토리 (컨텍스트 크기 측정용, 생략 가능)")
    ap.add_argument("--driver-tag", default=None,
                    help=r"드라이버 로그 식별 정규식. 예: FRC '\[S_F\]', DP '\[DRM-DP'")
    ap.add_argument("--min-key-len", type=int, default=DEFAULT_MIN_KEY_LEN,
                    help=f"match_key 최소 길이 (기본 {DEFAULT_MIN_KEY_LEN}) — 짧은 키는 오탐원")
    ap.add_argument("--exclude-level", dest="exclude_levels",
                    default=",".join(EXCLUDE_LEVELS_DEFAULT),
                    help=f"매칭에서 제외할 level 값(쉼표 구분). 기본 "
                         f"'{','.join(EXCLUDE_LEVELS_DEFAULT)}' — T2D 는 CONFIG_T2D_DEBUGD "
                         f"게이트라 dmesg 기대 로그가 아니다. 빈 문자열이면 제외 없음")
    ap.add_argument("--samples", type=int, default=0,
                    help="미매칭 라인 원문 샘플 수 (기본 0 = 미출력, 사내 로그 보호)")
    ap.add_argument("--out", type=Path, default=None, help="결과 마크다운 출력 경로 (생략 시 stdout)")
    args = ap.parse_args()

    for p in (args.log, args.index):
        if not p.is_file():
            sys.exit(f"파일을 찾을 수 없음: {p}")

    exclude = tuple(x.strip() for x in args.exclude_levels.split(",") if x.strip())
    index_used, index_dropped, index_lvl = load_index(args.index, args.min_key_len, exclude)
    if not index_used:
        sys.exit(f"사용 가능한 match_key 가 없음 (min-key-len={args.min_key_len} 이 너무 큰가?)")

    log_lines = read_text(args.log).splitlines()
    driver_tag = re.compile(args.driver_tag) if args.driver_tag else None

    m = measure(log_lines, index_used, driver_tag)
    ctx = measure_context(args.docs_dir) if args.docs_dir else {}

    report = build_report(args, index_used, index_dropped, index_lvl, m, ctx)

    if args.out:
        args.out.write_text(report, encoding="utf-8")
        print(f"결과 기록: {args.out}", file=sys.stderr)
        # 콘솔에도 핵심 수치만 — 파일을 열지 않고 즉시 확인할 수 있게.
        if driver_tag:
            print(f"모집단 기준 매칭률: {pct(m['matched_in_population'], m['in_population'])} "
                  f"({m['matched_in_population']}/{m['in_population']})", file=sys.stderr)
        else:
            print(f"전체 기준 매칭률: {pct(m['matched_total'], m['total'])} "
                  f"({m['matched_total']}/{m['total']})", file=sys.stderr)
    else:
        print(report)


if __name__ == "__main__":
    main()
