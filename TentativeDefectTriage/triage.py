#!/usr/bin/env python3
"""Stage 3 — 다중 전문가 병렬 실행 + 나열 제시.

설계 문서: `Document/신규 문제 분석 파이프라인/신규 문제 분석 파이프라인 설계.md`
           §3 아키텍처, §9 Phase 4

파이프라인의 최상위 진입점이다. 사용자가 **직접 지정한** 전문가(분석 프로파일)
각각에 대해 `analyze()` 를 독립 실행하고, 결과를 **승자 선정 없이 나열**한다.

세 가지 원칙
------------
**1. 승자를 고르지 않는다.** 신규 문제는 정답 데이터가 없으므로 하나로 수렴시키는
것이 오히려 위험하다. 순위·점수·"가장 유력한" 표시를 만들지 않으며, 제시 순서는
**사용자가 지정한 순서** 그대로다(품질순으로 정렬하면 그것이 곧 순위가 된다).

**2. 전문가를 자동 선정하지 않는다.** 어떤 전문가가 관련 있는지 판단하는 것 자체가
분석적 통찰이므로 전적으로 사용자 지정에 맡긴다(설계 §2).

**3. 한 전문가의 실패가 다른 전문가를 죽이지 않는다.** 자료 누락·LLM 오류·파싱
실패는 그 전문가의 결과만 실패로 표시하고 나머지는 그대로 낸다.

관점 차이 지표
--------------
이 설계의 핵심 가치는 "여러 관점"인데, 전문가들이 **같은 얘기만 반복하면 다중
전문가 구조 자체가 무의미**하다(§9 Phase 4 체크리스트). 그래서 리포트가 인용한
코드 위치의 겹침을 계산해 표면에 올린다 — **판정하지 않고 드러내기만 한다.**
겹침이 크다는 것이 곧 나쁘다는 뜻은 아니다(같은 원인을 두 관점이 확인한 것일 수도
있다). 사람이 판단할 재료다.

의존성: 표준 라이브러리 + pyyaml(설정) + (실제 호출 시) AnalyzingAssistant_v2.
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE / "analyze"))
sys.path.insert(0, str(_HERE / "material"))
sys.path.insert(0, str(_HERE / "refine"))

from analyze import AnalysisInput, analyze, default_llm_call  # noqa: E402
from refine import RefineConfig  # noqa: E402
from report import ExpertReport, render_markdown  # noqa: E402

DEFAULT_CONFIG = _HERE / "config" / "material.yaml"


# ── 설정 ──────────────────────────────────────────────────────────────────────

@dataclass
class MaterialConfig:
    material_root: Path
    profiles: dict[str, str]          # 프로파일 이름 → module_root 상대경로

    def module_root(self, profile: str) -> Path | None:
        rel = self.profiles.get(profile)
        return (self.material_root / rel) if rel else None


def load_config(path: Path) -> MaterialConfig:
    """`config/material.yaml` 을 읽는다.

    프로파일↔자료 매핑은 이름으로 자동 추론할 수 없다(`DTV_DP` → `sdp_drm-dp`)
    — 설정에 명시된 것만 쓴다(설계 §4).
    """
    try:
        import yaml
    except ImportError:
        sys.exit("pyyaml 이 필요하다 — LogAA 공유 가상환경(.venv)에서 실행할 것")

    if not path.is_file():
        sys.exit(f"설정 파일을 찾을 수 없음: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    root = data.get("material_root")
    if not root:
        sys.exit(f"{path}: `material_root` 가 없다")
    profiles = data.get("profiles") or {}
    if not profiles:
        sys.exit(f"{path}: `profiles` 매핑이 비었다")

    return MaterialConfig(
        material_root=Path(str(root)).expanduser(),
        profiles={str(k): str(v) for k, v in profiles.items()},
    )


# ── 결과 ──────────────────────────────────────────────────────────────────────

@dataclass
class ExpertOutcome:
    """전문가 1명의 실행 결과. 실패해도 자리를 지킨다 — 누가 빠졌는지 보이도록."""
    profile_name: str
    report: ExpertReport | None = None
    error: str = ""
    meta: dict = field(default_factory=dict)
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return self.report is not None and not self.error


@dataclass
class TriageResult:
    outcomes: list[ExpertOutcome]     # 사용자 지정 순서 유지
    chip: str
    problem_text: str

    @property
    def succeeded(self) -> list[ExpertOutcome]:
        return [o for o in self.outcomes if o.ok]


# ── 관점 차이 ─────────────────────────────────────────────────────────────────

def cited_locations(rep: ExpertReport) -> set[str]:
    """리포트가 근거로 인용한 코드 위치 집합(후보 포함)."""
    out: set[str] = set()
    for h in rep.hypotheses:
        for e in h.evidence:
            if e.type == "doc_citation":
                if e.value:
                    out.add(e.value)
                out.update(e.candidates)
    return out


def perspective_overlap(res: TriageResult) -> dict:
    """전문가 간 인용 위치 겹침을 계산한다. **판정하지 않고 드러내기만 한다.**

    겹침이 크다 = 나쁘다가 아니다. 같은 원인을 두 관점이 독립적으로 짚은 것일 수도
    있고, 실제로 중복인 것일 수도 있다 — 구분은 사람이 한다.
    """
    ok = res.succeeded
    if len(ok) < 2:
        return {}

    by: dict[str, set[str]] = {o.profile_name: cited_locations(o.report) for o in ok}
    files: dict[str, set[str]] = {
        n: {loc.rsplit(":", 1)[0] for loc in locs} for n, locs in by.items()
    }

    pairs = []
    names = list(by)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            shared_loc = by[a] & by[b]
            shared_file = files[a] & files[b]
            union_file = files[a] | files[b]
            pairs.append({
                "pair": (a, b),
                "shared_locations": len(shared_loc),
                "shared_files": sorted(shared_file),
                "file_overlap_ratio": (len(shared_file) / len(union_file)) if union_file else 0.0,
            })
    return {"by_expert": {n: len(v) for n, v in by.items()}, "pairs": pairs}


# ── 실행 ──────────────────────────────────────────────────────────────────────

def run_triage(
    profiles: list[str],
    chip: str,
    problem_text: str,
    raw_logs: dict[str, str],
    cfg: MaterialConfig,
    llm_call: Callable[[str], str],
    *,
    keywords: dict[str, list[str]] | None = None,
    mode: str = "two_stage",
    window: int = 10,
    budget_tokens: int = 28_000,
    max_workers: int = 4,
) -> TriageResult:
    """지정된 전문가들을 **독립 병렬** 실행한다.

    전문가 1명이어도 같은 경로를 탄다 — 특별 분기를 두지 않는다(§9 Phase 4).
    LLM 호출은 I/O 대기가 지배적이라 스레드로 병렬화한다.
    """
    keywords = keywords or {}

    def _one(profile: str) -> ExpertOutcome:
        t0 = time.monotonic()
        try:
            module_root = cfg.module_root(profile)
            if module_root is None:
                raise ValueError(
                    f"설정에 프로파일 '{profile}' 의 module_root 가 없다 "
                    f"(config/material.yaml 의 profiles 에 추가할 것)"
                )
            if not (module_root / chip).is_dir():
                raise ValueError(f"자료 없음: {module_root / chip}")

            inp = AnalysisInput(
                profile_name=profile, chip=chip, module_root=module_root,
                problem_text=problem_text, raw_logs=raw_logs,
                keywords=keywords.get(profile, []),
            )
            rcfg = RefineConfig(keywords=inp.keywords, budget_tokens=budget_tokens)
            rep, meta = analyze(inp, llm_call, rcfg, mode=mode, window=window)
            return ExpertOutcome(profile, report=rep, meta=meta,
                                 seconds=time.monotonic() - t0)
        except Exception as e:
            # 한 전문가의 실패가 다른 전문가를 죽이지 않는다(§9 Phase 4).
            return ExpertOutcome(profile, error=f"{type(e).__name__}: {e}",
                                 seconds=time.monotonic() - t0)

    if len(profiles) == 1:
        outcomes = [_one(profiles[0])]
    else:
        with ThreadPoolExecutor(max_workers=min(max_workers, len(profiles))) as ex:
            # 지정 순서를 유지한다 — 완료 순서로 두면 그것이 곧 순위처럼 읽힌다.
            outcomes = list(ex.map(_one, profiles))

    return TriageResult(outcomes=outcomes, chip=chip, problem_text=problem_text)


# ── 제시 ──────────────────────────────────────────────────────────────────────

def render(res: TriageResult) -> str:
    """전문가별 리포트를 나열한다. 승자 선정도 순위도 없다."""
    L: list[str] = []
    add = L.append

    add("# 신규 문제 분석 — 전문가별 참고자료")
    add("")
    add(f"- 칩: `{res.chip}`")
    add(f"- 지정된 전문가: {', '.join(o.profile_name for o in res.outcomes)}")
    add("")
    add("> **이 문서는 확정 진단이 아니다.** 각 전문가가 자기 관점에서 독립적으로 낸 "
        "가설과 근거이며, 서로 다를 수 있다. 우열을 매기지 않았으므로 최종 판단은 "
        "읽는 사람이 한다.")
    add("")
    add("## 문제 상황")
    add("")
    add(res.problem_text or "(입력되지 않음)")
    add("")

    # ── 훑어보기용 색인 (순위 아님 — 지정 순서) ──────────────────────────────
    add("## 전문가별 요약")
    add("")
    add("| 전문가 | 결과 | 가설 | 확신도 | 소요 |")
    add("| --- | --- | --- | --- | --- |")
    for o in res.outcomes:
        if not o.ok:
            add(f"| {o.profile_name} | **실패** | — | — | {o.seconds:.1f}s |")
            continue
        r = o.report
        heads = " / ".join(h.confidence or "?" for h in r.hypotheses) or "—"
        first = r.hypotheses[0].summary if r.hypotheses else "가설 없음"
        if len(first) > 45:
            first = first[:42] + "…"
        add(f"| {o.profile_name} | {len(r.hypotheses)}개 | {first} | {heads} | {o.seconds:.1f}s |")
    add("")
    add("*표의 순서는 지정 순서이며 우열이 아니다.*")
    add("")

    failed = [o for o in res.outcomes if not o.ok]
    if failed:
        add("### 실패한 전문가")
        add("")
        for o in failed:
            add(f"- **{o.profile_name}** — {o.error}")
        add("")
        add("실패한 전문가의 관점은 이 문서에 없다. 그 관점이 필요하면 원인을 해결하고 다시 실행할 것.")
        add("")

    # ── 관점 차이 ───────────────────────────────────────────────────────────
    ov = perspective_overlap(res)
    if ov:
        add("### 관점 차이")
        add("")
        for p in ov["pairs"]:
            a, b = p["pair"]
            ratio = p["file_overlap_ratio"]
            if p["shared_locations"] == 0 and not p["shared_files"]:
                note = "**겹치는 인용이 없다** — 서로 다른 곳을 보고 있다"
            elif ratio >= 0.8:
                note = ("**인용이 대부분 겹친다** — 같은 원인을 독립 확인한 것인지, "
                        "실질적으로 중복인지 확인이 필요하다")
            else:
                note = f"일부 겹침 (파일 기준 {ratio:.0%})"
            add(f"- `{a}` ↔ `{b}`: {note}")
            if p["shared_files"]:
                add(f"  - 공통 파일: {', '.join(f'`{f}`' for f in p['shared_files'][:6])}")
        add("")
        add("*겹침이 크다고 나쁜 것은 아니다 — 판정하지 않고 드러내기만 한다.*")
        add("")

    add("---")
    add("")
    for o in res.outcomes:
        if not o.ok:
            continue
        add(render_markdown(o.report))
        add("---")
        add("")

    return "\n".join(L)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description="신규 문제 분석 — 지정된 전문가들을 병렬 실행해 참고자료를 낸다")
    ap.add_argument("logs", nargs="+", type=Path)
    ap.add_argument("--profiles", required=True,
                    help="전문가(분석 프로파일) 이름, 쉼표 구분. **사용자가 직접 지정한다**")
    ap.add_argument("--chip", required=True)
    ap.add_argument("--problem", default="", help="문제 상황 설명")
    ap.add_argument("--keywords", default="",
                    help="프로파일별 정제 키워드. 'DTV_FRC=S_F;DTV_DP=DRM-DP' 형식")
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("--mode", choices=["two_stage", "single"], default="two_stage")
    ap.add_argument("--window", type=int, default=10)
    ap.add_argument("--budget-tokens", type=int, default=28_000)
    ap.add_argument("--model", default=None)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    profiles = [p.strip() for p in args.profiles.split(",") if p.strip()]
    if not profiles:
        sys.exit("--profiles 에 전문가를 하나 이상 지정할 것")

    keywords: dict[str, list[str]] = {}
    for chunk in args.keywords.split(";"):
        if "=" in chunk:
            name, kws = chunk.split("=", 1)
            keywords[name.strip()] = [k.strip() for k in kws.split(",") if k.strip()]

    raw = {}
    for p in args.logs:
        if not p.is_file():
            sys.exit(f"파일을 찾을 수 없음: {p}")
        raw[p.name] = p.read_bytes().decode("utf-8", errors="replace")

    cfg = load_config(args.config)
    res = run_triage(
        profiles, args.chip, args.problem, raw, cfg, default_llm_call(args.model),
        keywords=keywords, mode=args.mode, window=args.window,
        budget_tokens=args.budget_tokens,
    )

    md = render(res)
    if args.out:
        args.out.write_text(md + "\n", encoding="utf-8")
        print(f"리포트 기록: {args.out}", file=sys.stderr)
    else:
        print(md)

    ok = len(res.succeeded)
    print(f"\n전문가 {len(res.outcomes)}명 중 {ok}명 성공", file=sys.stderr)
    for o in res.outcomes:
        if not o.ok:
            print(f"  실패: {o.profile_name} — {o.error}", file=sys.stderr)


if __name__ == "__main__":
    main()
