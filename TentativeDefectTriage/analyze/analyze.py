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

from excerpt import load_docs, select_sections  # noqa: E402
from probe_match_rate import load_index  # noqa: E402
from prompt import (  # noqa: E402
    PromptContext, available_questions, build_hypothesis_prompt,
    build_observation_prompt, build_single_prompt, extract_build_assumption,
)
from refine import RefineConfig, refine  # noqa: E402
from report import ExpertReport, build_report, parse_response  # noqa: E402

LLMCall = Callable[[str], str]


@dataclass
class Resolution:
    """정제 로그 한 줄의 코드 위치 해소 결과."""
    line: str
    locations: list[str] = field(default_factory=list)   # file:line (복수 = 모호)
    subsystems: list[str] = field(default_factory=list)

    @property
    def state(self) -> str:
        if not self.locations:
            return "미매칭"
        return "단일" if len(self.locations) == 1 else "복수후보"


def resolve_observations(lines: list[str], index: list[dict]) -> list[Resolution]:
    """정제된 로그 라인을 로그인덱스에 대조해 코드 위치를 붙인다 (Stage 2 1단계).

    매칭 방식은 부분문자열 포함 — `match_key` 는 포맷 문자열에서 변수부를 떼어낸
    리터럴 조각이므로 런타임 라인이 그것을 포함한다. 여러 키가 걸리면 **가장 긴
    키**를 대표로 삼는다(더 구체적인 쪽이 옳은 지목일 가능성이 높다).

    **위치를 하나로 단정하지 않는다** — 같은 `match_key` 가 여러 `file:line` 을
    가리키는 경우가 실측 31.2% 다. 후보를 전부 남겨 프롬프트가 그대로 전달한다.
    """
    by_key: dict[str, tuple[set[str], set[str]]] = {}
    for e in index:
        locs, subs = by_key.setdefault(e["match_key"], (set(), set()))
        locs.add(e["file_line"])
        subs.add(e["subsystem"])
    keys = list(by_key)

    out: list[Resolution] = []
    for line in lines:
        hits = [k for k in keys if k in line]
        if not hits:
            out.append(Resolution(line=line))
            continue
        best = max(hits, key=len)
        locs, subs = by_key[best]
        out.append(Resolution(line=line, locations=sorted(locs), subsystems=sorted(subs)))
    return out


def format_match_summary(res: list[Resolution]) -> str:
    """로그↔코드 매칭 요약 + 모호한 항목 나열. 로그 본문은 중복하지 않는다."""
    c = Counter(r.state for r in res)
    total = len(res)
    lines = [
        f"정제 로그 {total:,}줄 중 — 단일 위치 {c['단일']:,} / "
        f"복수 후보 {c['복수후보']:,} / 미매칭 {c['미매칭']:,}",
        "",
        "복수 후보는 로그만으로 위치가 특정되지 않는 것이다(같은 포맷 문자열이 여러 곳에 존재). "
        "그중 하나를 임의로 고르지 말고 후보 전체를 근거로 제시하라.",
        "미매칭은 인덱스에 없는 라인이다 — 타 모듈 로그이거나, 자료 스냅샷 이후 추가된 코드이거나, "
        "인덱스 누락일 수 있다.",
    ]

    amb = [r for r in res if r.state == "복수후보"]
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
        elif len(r.locations) == 1:
            sub = f" ({r.subsystems[0]})" if r.subsystems else ""
            out.append(f"{r.line}    → {r.locations[0]}{sub}")
        else:
            out.append(f"{r.line}    → 후보 {len(r.locations)}개: {', '.join(r.locations)}")
    if limit > 0 and len(res) > limit:
        out.append(f"… 이하 {len(res) - limit:,}줄 생략")
    return "\n".join(out)


@dataclass
class AnalysisInput:
    profile_name: str
    chip: str
    module_root: Path            # <material_root>/<analysis/sdp_frc> 등
    problem_text: str
    raw_logs: dict[str, str]
    keywords: list[str] = field(default_factory=list)


def prepare(inp: AnalysisInput, cfg: RefineConfig, window: int = 10) -> tuple[PromptContext, dict]:
    """LLM 호출 직전까지 — 정제·매칭·발췌·프롬프트 재료 조립. 결정론적이다."""
    chip_dir = inp.module_root / inp.chip
    if not chip_dir.is_dir():
        raise SystemExit(f"칩 자료를 찾을 수 없음: {chip_dir}")

    # Stage 1
    refined = refine(inp.raw_logs, cfg)

    # Stage 2-1 — 로그↔코드
    index, _dropped, _lvl = load_index(chip_dir / "11_log_index.tsv", 8)
    res = resolve_observations([l.render() for l in refined.lines], index)

    observed: set[tuple[str, int]] = set()
    for r in res:
        for loc in r.locations:
            path, _, ln = loc.rpartition(":")
            if path and ln.isdigit():
                observed.add((path, int(ln)))

    # Stage 2-2/3 — 문서 선택 + § 발췌
    sections, whole, total_chars = load_docs(chip_dir)
    selected, stats = select_sections(sections, observed, window)
    excerpt_text = "\n\n".join(f"[{s.doc} § {s.heading}]\n{s.text}" for s in selected)
    for w in whole:
        excerpt_text += f"\n\n[{w['name']} — 전문]\n" + \
            (chip_dir / w["name"]).read_bytes().decode("utf-8", errors="replace")

    omissions = list(refined.warnings)
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

    usable_q, skipped_q = available_questions(inp.module_root, chip_dir)
    ctx = PromptContext(
        profile_name=inp.profile_name,
        chip=inp.chip,
        module_root=inp.module_root.name,
        build_assumption=extract_build_assumption(chip_dir),
        problem_text=inp.problem_text,
        refined_log=format_annotated_log(res),
        excerpt=excerpt_text,
        observations=format_match_summary(res),
        omissions=omissions,
        skipped_questions=skipped_q,
    )

    meta = {
        "refine":        refined.stats,
        "resolutions":   dict(Counter(r.state for r in res)),
        "sections_total": len(sections),
        "sections_used": len(selected),
        "docs_chars_total": total_chars,
        "excerpt_chars": len(excerpt_text),
        "questions_used": [q.qid for q in usable_q],
        "questions_skipped": [q.qid for q, _ in skipped_q],
    }
    return ctx, meta


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
    ap.add_argument("--budget-tokens", type=int, default=50_000)
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
            print(f"[{k}] {len(v):,}자 ≈ {int(len(v)/1.5):,}토큰(추정)", file=sys.stderr)
        print(f"질문 사용 {meta['questions_used']} / 생략 {meta['questions_skipped']}", file=sys.stderr)
        print(f"발췌 § {meta['sections_used']}/{meta['sections_total']} · "
              f"{meta['excerpt_chars']:,}자 (통짜 {meta['docs_chars_total']:,}자)", file=sys.stderr)
        print(f"로그 매칭 {meta['resolutions']}", file=sys.stderr)
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
