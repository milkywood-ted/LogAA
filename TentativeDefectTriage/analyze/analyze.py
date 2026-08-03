#!/usr/bin/env python3
"""Stage 1~3 배선 — 한 전문가의 분석을 끝까지 실행한다.

설계 문서: `Document/신규 문제 분석 파이프라인/신규 문제 분석 파이프라인 설계.md`
           §3 아키텍처, §4 Stage 2, §9 Phase 2~3

    Stage 1 정제 → 로그↔코드 매칭 → § 발췌 → 프롬프트 → LLM → ExpertReport

LLM 호출은 **주입 가능**하다(`llm_call` 인자). 기본값은 기존
`AnalyzingAssistant_v2/core/llm.py` 를 재사용한다 — 목적이 동일한 인프라라
재구현할 이유가 없고, LLM 프로필 설정을 한곳에서 관리하는 편이 낫다. 기존 코드를
**수정하지 않고 import 만** 하므로 제약(§2)에 어긋나지 않는다.

주입 가능하게 둔 이유: `--dry-run` 으로 LLM·네트워크 없이 프롬프트를 만들어
크기와 내용을 검증할 수 있다. 실제 호출 전에 예산을 확인하는 용도이기도 하다.

의존성: 표준 라이브러리 + (실제 호출 시에만) AnalyzingAssistant_v2.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_ROOT / "material"))
sys.path.insert(0, str(_ROOT / "refine"))

from disambiguate import narrow  # noqa: E402
from excerpt import expand_one_hop, load_docs, parse_sections, select_sections  # noqa: E402
from material_contract import conceptual_docs, load_contract, resolve_doc  # noqa: E402
from probe_match_rate import load_index  # noqa: E402
from source_slice import format_slices, slice_functions  # noqa: E402
from prompt import (  # noqa: E402
    PromptContext, available_questions, build_hypothesis_prompt,
    build_observation_prompt, build_single_prompt, extract_build_assumption,
)
from refine import RefineConfig, refine  # noqa: E402
from report import ExpertReport, build_report, parse_response  # noqa: E402

LLMCall = Callable[[str], str]


# 후보가 이보다 많은데 소스 대조로도 좁혀지지 않으면 그 위치들을 **관측 집합에
# 넣지 않는다**(발췌·호출체인·소스 발췌를 몰지 않는다). 로그 라인과 후보 분포는
# 그대로 보고하므로 정보가 사라지지는 않는다.
#
# 값의 근거 — 실측 분포(자료 `0fe6b7c97`, T2D 제외·키 길이 8 이상):
#   FRC rheam/rheal : 모호 키 31~38종, **최대 5곳** — 이 임계값은 FRC 를 건드리지 않는다
#   DP  4칩          : 모호 키 137~148종 중 10곳 초과는 **6~8종뿐**
# 그 6~8종은 `No Permission to Modeset`·`copy error....` 처럼 **포맷에 변수부가
# 아예 없어** 단일 라인으로는 구조적으로 환원 불가한 것들이거나, 소스 대조로
# 해소되는 것들이다. 즉 임계값이 잘라내는 것은 "좁힐 방법이 없는 꼬리"다.
MAX_OBSERVED_CANDIDATES = 10


@dataclass
class Resolution:
    """정제 로그 한 줄의 코드 위치 해소 결과."""
    line: str
    locations: list[str] = field(default_factory=list)   # file:line (복수 = 모호)
    subsystems: list[str] = field(default_factory=list)
    narrowed_from: int = 0        # 소스 대조 전 후보 수 (0 = 좁히지 않았음)
    narrow_note: str = ""         # 어떻게 좁혔는지 / 왜 못 좁혔는지
    in_observed: bool = True      # 관측 집합(발췌 구동)에 넣었는가

    @property
    def state(self) -> str:
        if not self.locations:
            return "미매칭"
        if not self.in_observed:
            # "복수후보"와 섞으면 안 된다 — 복수후보는 후보 전체를 근거로 쓰라는
            # 뜻이지만, 이쪽은 아예 근거로 쓰지 않은 것이다.
            return "후보과다"
        return "단일" if len(self.locations) == 1 else "복수후보"


def resolve_observations(
    lines: list[str], index: list[dict], material_root: Path | None = None,
) -> list[Resolution]:
    """정제된 로그 라인을 로그인덱스에 대조해 코드 위치를 붙인다 (Stage 2 1단계).

    매칭 방식은 부분문자열 포함 — `match_key` 는 포맷 문자열에서 변수부를 떼어낸
    리터럴 조각이므로 런타임 라인이 그것을 포함한다. 여러 키가 걸리면 **가장 긴
    키**를 대표로 삼는다(더 구체적인 쪽이 옳은 지목일 가능성이 높다).

    **위치를 하나로 단정하지 않는다** — 같은 `match_key` 가 여러 `file:line` 을
    가리키는 경우가 실측 31.2% 다. 후보를 전부 남겨 프롬프트가 그대로 전달한다.

    후보가 여럿이면 두 단계를 더 거친다:

    1. `material_root` 가 주어지면 **소스와 대조해 좁힌다**(`disambiguate.narrow`).
       런타임 값이 소스에 실제로 있는지만 보므로 추측이 아니며, 자료가 지시한
       절차이기도 하다(`log_analysis/01_log_grammar.md §3.1`).
    2. 그래도 `MAX_OBSERVED_CANDIDATES` 를 넘으면 위치를 **관측 집합에서 뺀다**.
       후보 175곳을 발췌 조인에 넣으면 § 이 39% 까지 선택돼 근거가 희석된다.
    """
    by_key: dict[str, list[dict]] = {}
    for e in index:
        by_key.setdefault(e["match_key"], []).append(e)
    keys = list(by_key)

    out: list[Resolution] = []
    for line in lines:
        hits = [k for k in keys if k in line]
        if not hits:
            out.append(Resolution(line=line))
            continue
        best = max(hits, key=len)
        entries = by_key[best]
        locs = sorted({e["file_line"] for e in entries})
        subs = sorted({e["subsystem"] for e in entries})

        narrowed_from, note = 0, ""
        if len(locs) > 1 and material_root is not None:
            got, note = narrow(line, entries, material_root)
            if got:
                narrowed_from = len(locs)
                locs = got
                keep = set(got)
                subs = sorted({e["subsystem"] for e in entries
                               if e["file_line"] in keep})

        out.append(Resolution(
            line=line, locations=locs, subsystems=subs,
            narrowed_from=narrowed_from, narrow_note=note,
            in_observed=len(locs) <= MAX_OBSERVED_CANDIDATES,
        ))
    return out


def format_match_summary(res: list[Resolution]) -> str:
    """로그↔코드 매칭 요약 + 모호한 항목 나열. 로그 본문은 중복하지 않는다."""
    c = Counter(r.state for r in res)
    total = len(res)
    lines = [
        f"정제 로그 {total:,}줄 중 — 단일 위치 {c['단일']:,} / "
        f"복수 후보 {c['복수후보']:,} / 후보 과다 {c['후보과다']:,} / 미매칭 {c['미매칭']:,}",
        "",
        "복수 후보는 로그만으로 위치가 특정되지 않는 것이다(같은 포맷 문자열이 여러 곳에 존재). "
        "그중 하나를 임의로 고르지 말고 후보 전체를 근거로 제시하라.",
        "후보 과다는 후보가 너무 많아 **위치를 근거로 쓰지 않은** 것이다 — 복수 후보와 다르다. "
        "그 라인에 대해 코드 위치를 단정하지 말라.",
        "미매칭은 인덱스에 없는 라인이다 — 타 모듈 로그이거나, 자료 스냅샷 이후 추가된 코드이거나, "
        "인덱스 누락일 수 있다.",
    ]

    # 소스 대조로 좁힌 것은 **인덱스가 준 위치와 구별해서** 알린다 — 유도된
    # 결과를 같은 확정도로 내놓으면 읽는 쪽이 오판한다.
    narrowed = [r for r in res if r.narrowed_from]
    if narrowed:
        lines += ["", f"소스 대조로 좁힌 항목 ({len(narrowed):,}건) — "
                      "인덱스가 직접 준 위치가 아니라 **런타임 값을 소스에서 확인해 유도한** 것이다:"]
        for r in narrowed[:20]:
            lines.append(f"  - {r.narrow_note} → {', '.join(r.locations)}")
        if len(narrowed) > 20:
            lines.append(f"  … 외 {len(narrowed) - 20:,}건")

    # 후보가 너무 많아 관측 집합에서 뺀 것 — 파일 분포까지는 남긴다.
    dropped = [r for r in res if not r.in_observed]
    if dropped:
        lines += ["", f"후보 과다로 코드 위치를 쓰지 않은 항목 ({len(dropped):,}건) — "
                      f"후보가 {MAX_OBSERVED_CANDIDATES}곳을 넘고 소스 대조로도 좁혀지지 "
                      "않아 **참고자료 발췌에 반영하지 않았다.** 이 라인이 어디서 왔는지는 "
                      "아래 파일 분포까지만 말할 수 있다:"]
        for r in dropped[:10]:
            dist = Counter(loc.rpartition(":")[0] for loc in r.locations)
            top = ", ".join(f"{f}({n})" for f, n in dist.most_common(4))
            lines.append(f"  - 후보 {len(r.locations)}곳 · {top}"
                         + (f" · {r.narrow_note}" if r.narrow_note else ""))
        if len(dropped) > 10:
            lines.append(f"  … 외 {len(dropped) - 10:,}건")

    amb = [r for r in res if r.state == "복수후보" and r.in_observed]
    if amb:
        lines += ["", f"복수 후보 항목 ({len(amb):,}건):"]
        seen: set[tuple] = set()
        for r in amb:
            key = tuple(r.locations)
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"  - {', '.join(r.locations)}")
            if len(seen) >= 40:
                lines.append(f"  … 외 {len(amb) - len(seen):,}건 (같은 후보 조합은 1회만 표기)")
                break
    return "\n".join(lines)


def format_annotated_log(res: list[Resolution], limit: int = 0) -> str:
    """로그 라인에 코드 위치를 인라인으로 붙인다."""
    out: list[str] = []
    shown = res if limit <= 0 else res[:limit]
    for r in shown:
        if not r.locations:
            out.append(f"{r.line}    [미매칭]")
        elif not r.in_observed:
            # 후보를 나열하면 라인 하나가 화면을 덮는다. 개수만 남기고 근거는
            # 관측 요약의 "후보 과다" 절로 넘긴다.
            out.append(f"{r.line}    → 후보 {len(r.locations)}곳 (과다 — 위치 미사용)")
        elif len(r.locations) == 1:
            sub = f" ({r.subsystems[0]})" if r.subsystems else ""
            src = " [소스 대조로 특정]" if r.narrowed_from else ""
            out.append(f"{r.line}    → {r.locations[0]}{sub}{src}")
        else:
            src = f" (소스 대조로 {r.narrowed_from}→{len(r.locations)})" if r.narrowed_from else ""
            out.append(f"{r.line}    → 후보 {len(r.locations)}개{src}: {', '.join(r.locations)}")
    if limit > 0 and len(res) > limit:
        out.append(f"… 이하 {len(res) - limit:,}줄 생략")
    return "\n".join(out)


def _basis_body(path: Path, subsystems: set[str]) -> tuple[str, str]:
    """질문 근거 문서에서 관련 부분을 고른다. 반환: (본문, 방식 표기).

    `file:line` 조인이 아무것도 못 건진 문서라 통짜로 넣으면 비싸다(07 은 32k자).
    대신 **관측된 subsystem 이름을 언급하는 § 만** 고른다 — 최초 설계의
    "subsystem 기준 § 선택" fallback 이 여기서 제 역할을 한다. 그래도 아무것도
    안 걸리면 그때는 전문을 넣는다(근거 없이 질문만 던지는 것보다 낫다).
    """
    text = path.read_bytes().decode("utf-8", errors="replace")
    if not subsystems:
        return text, "전문"

    secs = parse_sections(path.name, text)
    hit = [x for x in secs
           if any(sub in x.text or sub in x.heading for sub in subsystems)]
    if not hit:
        return text, "전문(subsystem 매칭 없음)"

    body = "\n\n".join(f"§ {x.heading}\n{x.text}" for x in hit)
    # 축소 효과가 없으면 통짜가 낫다 — 조각내면 맥락만 깨진다.
    if len(body) >= len(text) * 0.8:
        return text, "전문"
    return body, f"§ {len(hit)}/{len(secs)} 발췌"


@dataclass
class AnalysisInput:
    profile_name: str
    chip: str
    module_root: Path            # <material_root>/<analysis/sdp_frc> 등
    problem_text: str
    raw_logs: dict[str, str]
    keywords: list[str] = field(default_factory=list)


def prepare(inp: AnalysisInput, cfg: RefineConfig, window: int = 10,
            *, hop_budget: int = 12_000, source_budget: int = 12_000
            ) -> tuple[PromptContext, dict]:
    """LLM 호출 직전까지 — 정제·매칭·발췌·프롬프트 재료 조립. 결정론적이다."""
    chip_dir = inp.module_root / inp.chip
    if not chip_dir.is_dir():
        raise SystemExit(f"칩 자료를 찾을 수 없음: {chip_dir}")

    # 소스 트리는 자료 저장소 루트 아래 있다 — module_root 에서 위로 찾는다.
    material_root = next(
        (par for par in inp.module_root.parents if (par / "tztv-media-sec").is_dir()),
        inp.module_root.parent,
    )

    # Stage 1
    refined = refine(inp.raw_logs, cfg)

    # Stage 2-1 — 로그↔코드
    index, _dropped, _lvl = load_index(chip_dir / "11_log_index.tsv", 8)
    res = resolve_observations([l.render() for l in refined.lines], index, material_root)

    # 후보 과다로 제외된 라인의 위치는 넣지 않는다 — 근거를 좁히지 못한 위치
    # 수백 개가 발췌를 몰면 관련 § 이 묻힌다(`MAX_OBSERVED_CANDIDATES`).
    observed: set[tuple[str, int]] = set()
    for r in res:
        if not r.in_observed:
            continue
        for loc in r.locations:
            path, _, ln = loc.rpartition(":")
            if path and ln.isdigit():
                observed.add((path, int(ln)))

    observed_subsystems = {sub for r in res if r.in_observed for sub in r.subsystems}

    # Stage 2-2/3 — 문서 선택 + § 발췌
    sections, whole, total_chars = load_docs(chip_dir)
    selected, stats = select_sections(sections, observed, window)
    # ── 호출 체인 1홉 확장 ───────────────────────────────────────────────────
    # 발췌는 "로그를 남긴 곳"에 앵커링돼 있어 **로그를 안 남기는 중간 함수가
    # 보이지 않는다**(실측 피드백 ③). 선택된 § 이 인용하는 다른 위치로 한 걸음
    # 넓히되, 무제한이면 폭발하므로(§ 8→64개, 11.3k→99.6k자) 예산까지만 넣는다.
    hop_sections, hop_stats = expand_one_hop(
        sections, selected, observed, window, hop_budget)

    excerpt_text = "\n\n".join(f"[{s.doc} § {s.heading}]\n{s.text}" for s in selected)
    if hop_sections:
        excerpt_text += "\n\n" + "\n\n".join(
            f"[{s.doc} § {s.heading} — 호출 체인 확장]\n{s.text}" for s in hop_sections)
    for w in whole:
        excerpt_text += f"\n\n[{w['name']} — 전문]\n" + \
            (chip_dir / w["name"]).read_bytes().decode("utf-8", errors="replace")

    # ── 질문 근거 문서 보충 ──────────────────────────────────────────────────
    #
    # 질문 게이팅은 "근거 파일이 존재하는가"만 보는데, § 발췌는 **관측된 코드
    # 위치**를 기준으로 고른다. 둘이 어긋나면 **근거 없이 질문만 던져지고**
    # LLM 이 추측으로 메우게 된다 — 이 설계가 막으려던 바로 그것이다.
    # 실제로 두 구멍이 있었다:
    #   - Q3 근거(상태 모델)는 칩 디렉토리 밖이라 애초에 로드되지 않아
    #     **한 번도 제공되지 않았다**.
    #   - Q7 근거(`07_debug_interfaces.md`)는 관측 위치와 겹치지 않으면 빠진다.
    #
    # 그래서 던지는 질문의 근거 문서가 발췌에 **전혀 기여하지 못했으면** 전문을
    # 보충한다. 이미 § 가 뽑혔으면 그쪽이 관련 부분이므로 중복하지 않는다.
    # ── 개념 계층 ────────────────────────────────────────────────────────────
    # 인용이 없어 § 발췌에 안 걸리지만 없으면 로그의 의미·구조를 읽을 수 없는
    # 자료(용어집·구조 전체상·로그 문법·상관 키). 실측 피드백으로 추가했다.
    contract = load_contract(inp.module_root)
    concept_docs, concept_missing = conceptual_docs(inp.module_root, chip_dir, contract)
    background = "\n\n".join(f"[{label}]\n{body}" for label, body in concept_docs)

    # ── 관측 지점의 소스 함수 ────────────────────────────────────────────────
    # 벌크 소스 주입이 아니다 — 로그가 어디서 찍혔는지 이미 알고(로그인덱스),
    # 그 지점을 감싸는 함수 하나씩만 뽑는다. 레시피(02_triage_recipe §1-3)가 말하는
    # "무슨 상태에서 찍히는가"(감싼 함수·if 조건)를 보기 위함이며, 양이 관측 위치
    # 수로 묶여 있다. 함수 경계는 중괄호 짝맞춤으로 구하고, 범위 밖이면 귀속하지
    # 않는다(자료 README §4-1 이 기록한 오판 방지).
    src_slices, src_stats = slice_functions(
        material_root, sorted(observed), budget_chars=source_budget)
    source_text = format_slices(src_slices)

    usable_q, skipped_q = available_questions(inp.module_root, chip_dir)
    contributed = {s.doc for s in selected} | {w["name"] for w in whole}
    # 개념 계층에 이미 들어간 문서는 근거 보충에서 중복하지 않는다.
    contributed |= {Path(label.split(" ")[0]).name for label, _ in concept_docs}

    supplemented: list[tuple[str, int]] = []
    seen_basis: set[Path] = set()
    for q in usable_q:
        for base, pat in ((chip_dir, q.requires_chip), (inp.module_root, q.requires_module)):
            if not pat:
                continue
            # 번호가 아니라 이름으로 찾는다(`resolve_doc`). 여러 개가 걸리면
            # **정렬 첫 번째**만 보충한다 — 번호가 작은 쪽이 기반 문서이고
            # (FRC `06_cross_module_edges` vs 그 위에 로그를 붙인 `08`),
            # 근거 보충은 질문을 성립시키는 최소 자료면 충분하기 때문이다.
            found = resolve_doc(base, pat)
            if not found:
                continue
            path = found[0]
            if path.name in contributed:
                continue                      # 발췌가 이미 관련 § 를 가져왔다
            if path in seen_basis:
                continue
            seen_basis.add(path)
            rel = path.relative_to(base)
            body, how = _basis_body(path, observed_subsystems)
            excerpt_text += f"\n\n[{rel} — {how} ({q.qid} 근거)]\n" + body
            supplemented.append((f"{rel}({how})", len(body)))

    omissions = list(refined.warnings)
    omissions += concept_missing
    excluded = [r for r in res if not r.in_observed]
    if excluded:
        omissions.append(
            f"로그 {len(excluded):,}줄은 후보가 {MAX_OBSERVED_CANDIDATES}곳을 넘고 "
            f"소스 대조로도 좁혀지지 않아 **코드 위치를 참고자료 발췌에 반영하지 "
            f"않았다**(최대 {max(len(r.locations) for r in excluded)}곳). "
            f"그 라인들에 대해서는 위치를 특정한 근거가 없다."
        )
    if hop_stats.get('skipped_over_budget'):
        omissions.append(
            f"호출 체인 확장 § {hop_stats['skipped_over_budget']}개는 예산 때문에 제외됐다")
    for n in src_stats.get('notes', [])[:5]:
        omissions.append(f"소스 함수 추출 실패 — {n}")
    if len(selected) < len(sections):
        omissions.append(
            f"분석 문서 § {len(sections) - len(selected):,}개는 관측된 코드 위치와 "
            f"겹치지 않아 제외됐다(전체 {len(sections):,}개 중 {len(selected):,}개 발췌)"
        )
    if stats["unmatched_files"]:
        omissions.append(
            f"관측된 파일 {len(stats['unmatched_files'])}종은 분석 문서에 인용이 없어 "
            f"관련 설명을 제공하지 못한다: {', '.join(stats['unmatched_files'][:8])}"
        )

    ctx = PromptContext(
        profile_name=inp.profile_name,
        chip=inp.chip,
        module_root=inp.module_root.name,
        build_assumption=extract_build_assumption(chip_dir),
        problem_text=inp.problem_text,
        refined_log=format_annotated_log(res),
        excerpt=excerpt_text,
        background=background,
        source_excerpt=source_text,
        observations=format_match_summary(res),
        omissions=omissions,
        skipped_questions=skipped_q,
    )

    meta = {
        "refine":        refined.stats,
        "resolutions":   dict(Counter(r.state for r in res)),
        "narrowed":      sum(1 for r in res if r.narrowed_from),
        "over_candidates": sum(1 for r in res if not r.in_observed),
        "sections_total": len(sections),
        "sections_used": len(selected),
        "docs_chars_total": total_chars,
        "excerpt_chars": len(excerpt_text),
        "background_docs": [(l, len(b)) for l, b in concept_docs],
        "hop_expansion": hop_stats,
        "source_slices": src_stats,
        "background_chars": len(background),
        "questions_used": [q.qid for q in usable_q],
        "questions_skipped": [q.qid for q, _ in skipped_q],
        "basis_supplemented": supplemented,
    }
    return ctx, meta


# ── 입력 예산 ────────────────────────────────────────────────────────────────
#
# 모델의 입력 여유. `num_ctx` 198,000 − `max_tokens` 65,535 에서 왔다.
INPUT_BUDGET_TOKENS = 132_000

# 자↔토큰 환산. 한국어·코드가 섞인 프롬프트 실측에서 얻은 거친 계수다.
_CHARS_PER_TOKEN = 1.5


def est_tokens(text: str) -> int:
    return int(len(text) / _CHARS_PER_TOKEN)


def budget_summary(prompts: dict[str, str], *,
                   input_budget: int = INPUT_BUDGET_TOKENS) -> dict:
    """호출별 입력 사용량을 낸다. **합산하지 않는다.**

    2단계 모드의 관측·가설 프롬프트는 **독립된 LLM 호출**이다(`analyze()` 가
    `llm_call` 을 두 번 부른다). 둘을 더해 입력 예산과 비교하면 실사용량의 2배가
    되어, 여유가 있는데도 초과 경고가 뜬다 — 진짜 초과와 구분되지 않으므로
    계기판으로 쓸 수 없다. 제약은 **가장 큰 단일 호출**이다.

    반환 키: per_stage(호출별 토큰), worst(이름, 토큰), budget, headroom, ratio.
    """
    per_stage = {k: est_tokens(v) for k, v in prompts.items()}
    name, worst = max(per_stage.items(), key=lambda kv: kv[1])
    return {
        "per_stage": per_stage,
        "worst": (name, worst),
        "budget": input_budget,
        "headroom": input_budget - worst,
        "ratio": worst / input_budget if input_budget else 0.0,
    }


def analyze(
    inp: AnalysisInput,
    llm_call: LLMCall,
    cfg: RefineConfig | None = None,
    *,
    mode: str = "two_stage",
    window: int = 10,
) -> tuple[ExpertReport, dict]:
    """한 전문가의 분석을 끝까지 실행한다.

    mode: "two_stage"(기본, 관측→가설) | "single"(1회 호출 — 비교용 대조군)
    """
    cfg = cfg or RefineConfig(keywords=inp.keywords)
    ctx, meta = prepare(inp, cfg, window)

    if mode == "single":
        prompts = {"single": build_single_prompt(ctx)}
        final_raw = llm_call(prompts["single"])
    else:
        p1 = build_observation_prompt(ctx)
        obs = llm_call(p1)
        p2 = build_hypothesis_prompt(ctx, obs)
        prompts = {"observation": p1, "hypothesis": p2}
        final_raw = llm_call(p2)
        meta["observation_result_chars"] = len(obs)

    meta["prompt_chars"] = {k: len(v) for k, v in prompts.items()}
    meta["mode"] = mode

    data, err = parse_response(final_raw)
    if data is None:
        rep = ExpertReport(
            profile_name=inp.profile_name, chip=inp.chip,
            module_root=ctx.module_root, build_assumption=ctx.build_assumption,
            raw_response=final_raw,
        )
        rep.errors.append(f"LLM 응답을 파싱하지 못했다 — {err}")
        return rep, meta

    rep = build_report(
        data, profile_name=inp.profile_name, chip=inp.chip,
        module_root=ctx.module_root, build_assumption=ctx.build_assumption,
        raw_response=final_raw,
    )
    return rep, meta


def default_llm_call(model: str | None = None) -> LLMCall:
    """기존 `AnalyzingAssistant_v2` 의 LLM 클라이언트를 재사용한다.

    import 시점을 늦춘다 — `--dry-run` 은 이 의존성 없이 동작해야 하기 때문이다.
    """
    aa = Path(__file__).resolve().parents[2] / "AnalyzingAssistant_v2"
    sys.path.insert(0, str(aa))
    from core import config as aa_config  # noqa: E402
    from core.llm import chat_with_profile  # noqa: E402

    profile = dict(aa_config.active_llm())
    if model:
        profile["model"] = model

    def _call(prompt: str) -> str:
        return chat_with_profile(
            profile=profile,
            messages=[{"role": "user", "content": prompt}],
            json_mode=False,   # 1차는 산문이라 전역 JSON 강제는 쓰지 않는다
            temperature=0.0,   # 재현성 우선
        )
    return _call


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="한 전문가의 신규 문제 분석 실행")
    ap.add_argument("logs", nargs="+", type=Path)
    ap.add_argument("--module-root", required=True, type=Path,
                    help="예: <자료>/analysis/sdp_frc")
    ap.add_argument("--chip", required=True)
    ap.add_argument("--profile", required=True, help="전문가(분석 프로파일) 이름")
    ap.add_argument("--problem", default="", help="문제 상황 설명")
    ap.add_argument("--keywords", default="", help="프로파일 prefilter_keywords (쉼표 구분)")
    ap.add_argument("--mode", choices=["two_stage", "single"], default="two_stage")
    ap.add_argument("--window", type=int, default=10)
    ap.add_argument("--budget-tokens", type=int, default=28_000,
                    help="정제 로그 상한. 개념 계층·소스 함수·호출체인 확장이 들어오면서 "
                         "50k→28k 로 낮췄다 — 로그는 입력 중 중복이 가장 많아 "
                         "줄여도 손실이 적은 반면, 개념·소스가 없으면 해석 자체가 안 된다")
    ap.add_argument("--dry-run", action="store_true",
                    help="LLM 호출 없이 프롬프트만 생성 — 크기·내용 확인용")
    ap.add_argument("--model", default=None, help="LLM 모델 오버라이드")
    ap.add_argument("--out", type=Path, default=None, help="리포트 마크다운 저장")
    ap.add_argument("--dump-prompt", type=Path, default=None, help="생성된 프롬프트 저장")
    args = ap.parse_args()

    raw = {}
    for p in args.logs:
        if not p.is_file():
            sys.exit(f"파일을 찾을 수 없음: {p}")
        raw[p.name] = p.read_bytes().decode("utf-8", errors="replace")

    inp = AnalysisInput(
        profile_name=args.profile, chip=args.chip, module_root=args.module_root,
        problem_text=args.problem, raw_logs=raw,
        keywords=[k.strip() for k in args.keywords.split(",") if k.strip()],
    )
    cfg = RefineConfig(keywords=inp.keywords, budget_tokens=args.budget_tokens)

    if args.dry_run:
        ctx, meta = prepare(inp, cfg, args.window)
        if args.mode == "single":
            prompts = {"single": build_single_prompt(ctx)}
        else:
            prompts = {
                "observation": build_observation_prompt(ctx),
                "hypothesis": build_hypothesis_prompt(ctx, "(1차 결과 자리 — dry-run)"),
            }
        for k, v in prompts.items():
            print(f"[{k}] {len(v):,}자 ≈ {est_tokens(v):,}토큰(추정)", file=sys.stderr)
        print(f"질문 사용 {meta['questions_used']} / 생략 {meta['questions_skipped']}", file=sys.stderr)
        print(f"발췌 § {meta['sections_used']}/{meta['sections_total']} · "
              f"{meta['excerpt_chars']:,}자 (통짜 {meta['docs_chars_total']:,}자)", file=sys.stderr)
        # 개념 계층은 자르지 않으므로 판정하지 않는다 — 크기와 비중만 보고한다.
        bg = meta["background_chars"]
        if bg:
            biggest = max(meta["background_docs"], key=lambda kv: kv[1])
            print(f"개념 계층 {len(meta['background_docs'])}종 · {bg:,}자 "
                  f"(최대 {biggest[0]} {biggest[1]:,}자)", file=sys.stderr)
        print(f"로그 매칭 {meta['resolutions']}", file=sys.stderr)
        # 예산 여유를 눈에 보이게 한다 — 조각들이 더해지면 조용히 넘칠 수 있다.
        # **호출별로 본다** — 2단계의 두 프롬프트는 독립 호출이다(`budget_summary`).
        b = budget_summary(prompts)
        log_tok = meta["refine"]["est_tokens"]
        wname, worst = b["worst"]
        print(f"예산: 최대 호출 {worst:,} 토큰 ({wname}, 로그 {log_tok:,} 포함) / "
              f"입력 여유 약 {b['budget']:,} 토큰 → 잔여 {b['headroom']:,}",
              file=sys.stderr)
        if args.mode != "single":
            print("  주: 가설 호출은 실행 시 1차 결과만큼 더 커진다"
                  "(dry-run 은 자리표시자라 이 수치는 하한이다).", file=sys.stderr)
        if b["ratio"] > 0.9:
            print("  ⚠️ 예산의 90%를 넘었다 — --budget-tokens 나 --window 를 줄일 것",
                  file=sys.stderr)
        if args.dump_prompt:
            args.dump_prompt.write_text(
                "\n\n" + ("=" * 70 + "\n").join(f"### {k}\n{v}" for k, v in prompts.items()),
                encoding="utf-8")
            print(f"프롬프트 기록: {args.dump_prompt}", file=sys.stderr)
        return

    rep, meta = analyze(inp, default_llm_call(args.model), cfg,
                        mode=args.mode, window=args.window)

    from report import render_markdown
    md = render_markdown(rep)
    if args.out:
        args.out.write_text(md + "\n", encoding="utf-8")
        print(f"리포트 기록: {args.out}", file=sys.stderr)
    else:
        print(md)

    print(f"\n모드 {meta['mode']} · 프롬프트 {meta['prompt_chars']}", file=sys.stderr)
    print(f"가설 {len(rep.hypotheses)}개 · 구조오류 {len(rep.errors)} · "
          f"품질신호 {len(rep.quality_flags)}", file=sys.stderr)


if __name__ == "__main__":
    main()
