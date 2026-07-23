"""
core/reflection.py

Stage 6 — Reflection (Qwen3-14B)

Stage 5에서 생성된 리포트를 LLM으로 한 번 더 검증하는 자기 검증 단계.

검증 항목:
  - 각 분석 항목에 evidence 로그 라인이 실제로 존재하는가
  - 로그에 근거 없는 추측성 판단이 포함되어 있는가
  - 판정이 score 및 매칭 결과와 일관되는가

검증 결과에 따라 추측성 항목을 제거하거나 "[추정]" 접두어로 구분하여 최종 리포트를 출력한다.

풀 내 상충 후보 경고(P3, Document/케이스 판정 연계 재도입/PR36 요구사항 및
결함 리포트.md §6): winner 판정이 "문제 아님"/"판정 불가"인데 minority_reports
안에 `case_verdict='defect'`인 후보가 있으면 경고를 채운다. **레이블은 절대
바꾸지 않는다** — winner 선정 로직(P2, 폐기)을 대신하는 안전장치이므로 판정을
뒤집는 게 아니라 사람이 놓치지 않게 표시만 한다. 이 판단은 LLM 프롬프트가
아니라 코드에서 결정적으로 수행한다 — PR #36 의 D3(reflection 검증 기준을
프롬프트 문구로 완화했다가 안전망 자체가 무력화된 결함)를 반복하지 않기 위함.

Output: ReflectionResult
  .report_final           : 검증 완료된 Markdown 리포트
  .notes                  : 수정/제거된 항목 요약 (없으면 "변경 없음")
  .pool_conflict_warning  : P3 경고 (해당 없으면 None)
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

import core.config as config
from core.llm import chat_stream
from core.log_refiner import LogLine, render_lines
from core.pattern_matcher import MatchResult

if TYPE_CHECKING:
    from core.pipeline import MinorityReport

MAX_EVIDENCE_LINES = 30
MAX_LOG_SAMPLE = 100

# P3 경고를 검사하는 판정 방향 — winner 가 "문제 아님"/"판정 불가"일 때만.
# 반대 방향(winner="문제"인데 no_defect 후보 존재)은 검사하지 않는다:
# false negative(결함을 놓침)가 false positive 보다 비싼 오류라는 §2 판단을
# 그대로 따른다 — 노이즈를 늘리지 않기 위한 의도된 비대칭.
_CONFLICT_CHECK_VERDICTS = {"문제 아님", "판정 불가"}


@dataclass
class ReflectionResult:
    """Stage 6 검증 결과."""
    report_final: str
    notes: str = ""
    pool_conflict_warning: str | None = None


class Reflector:
    """
    Stage 6 실행기.

        reflector = Reflector()
        result = reflector.reflect(
            report_md   = stage5_report,
            verdict     = "문제",
            score       = 0.85,
            match_result = match_result,
            l_common    = l_common,
        )
        print(result.report_final)
    """

    def __init__(self, model: str | None = None) -> None:
        self._model = model if model is not None else config.active_llm().get("model", "")

    def reflect(
        self,
        report_md: str,
        verdict: str,
        score: float,
        match_result: MatchResult | None,
        l_common: list[LogLine],
        minority_reports: list[MinorityReport] | None = None,
        system_analysis_guidelines: str = "",
        cancel_event: threading.Event | None = None,
    ) -> ReflectionResult:
        """
        Stage 5 리포트를 검증하고 최종 리포트를 반환한다.

        Parameters
        ----------
        report_md                  : Stage 5 생성 리포트 (Markdown)
        verdict                    : 판정 ("문제"|"문제 아님"|"판정 불가"|"불확실"|"알 수 없음")
        score                      : 진단 점수 (0.0 ~ 1.0)
        match_result               : Stage 4 패턴 매칭 결과 (MISS 면 None)
        l_common                   : Stage 1 정제 로그
        minority_reports           : 풀 내 나머지 후보 (P3 상충 경고 검사용)
        system_analysis_guidelines : 시스템 분석 지침
        """
        prompt = _build_prompt(
            report_md=report_md,
            verdict=verdict,
            score=score,
            match_result=match_result,
            l_common=l_common,
            system_analysis_guidelines=system_analysis_guidelines,
        )
        raw = chat_stream(
            messages     = [{"role": "user", "content": prompt}],
            model        = self._model,
            temperature  = config.active_llm().get("report_temperature", 0.2),
            cancel_event = cancel_event,
        )
        result = _parse_response(raw, fallback=report_md)
        result.pool_conflict_warning = _check_pool_conflict(verdict, minority_reports or [])
        return result


# ── 내부 헬퍼 ─────────────────────────────────────────────────────────────────

def _fmt_evidence_flat(match_result: MatchResult | None) -> str:
    """매칭된 패턴의 evidence 로그 라인을 패턴명과 함께 평탄화한다."""
    if not match_result or not match_result.matched:
        return "  (없음)"
    parts: list[str] = []
    for p in match_result.matched:
        for ll in p.evidence[:MAX_EVIDENCE_LINES]:
            parts.append(f"  [{p.name}] {ll.render()}")
    return "\n".join(parts) if parts else "  (없음)"


def _build_prompt(
    report_md: str,
    verdict: str,
    score: float,
    match_result: MatchResult | None,
    l_common: list[LogLine],
    system_analysis_guidelines: str,
) -> str:
    evidence_txt = _fmt_evidence_flat(match_result)
    log_sample = render_lines(l_common[:MAX_LOG_SAMPLE])
    log_suffix = f"\n... ({len(l_common) - MAX_LOG_SAMPLE}줄 생략)" if len(l_common) > MAX_LOG_SAMPLE else ""
    sys_section = (
        f"━━━ 시스템 분석 지침 ━━━\n{system_analysis_guidelines.strip()}\n\n"
        if system_analysis_guidelines.strip() else ""
    )

    return f"""/no_think
아래는 커널 로그 분석 파이프라인이 자동 생성한 진단 리포트입니다.
이 리포트를 검증하고 문제가 있는 부분을 수정하여 최종 리포트를 출력하세요.
{sys_section}
━━━ 검증 기준 ━━━
1. evidence 존재 여부: 각 분석 항목이 아래 "실제 evidence" 섹션의 로그 라인에 근거하는지 확인하세요.
   근거가 없는 항목은 제거하거나 "[근거 없음]" 접두어를 붙이세요.
2. 추측성 판단: 로그에 직접 근거가 없는 추측성 내용은 "[추정]" 접두어를 붙이거나
   별도 "추정 (미확인)" 섹션으로 이동하세요.
3. 판정 일관성: 판정({verdict})이 진단 점수({score:.0%}) 및 매칭 결과와 일관되는지 확인하세요.
   일관되지 않으면 판정 근거 설명만 수정하세요. 판정 레이블 자체는 변경하지 마세요.

━━━ 실제 evidence (패턴별 매칭 로그 라인) ━━━
{evidence_txt}

━━━ 정제 로그 (L_common 샘플, 최대 {MAX_LOG_SAMPLE}줄) ━━━
{log_sample}{log_suffix}

━━━ 검증 대상 리포트 ━━━
{report_md}

━━━ 출력 형식 ━━━
아래 두 섹션을 반드시 이 순서대로 출력하세요. 다른 내용은 추가하지 마세요.

### REFLECTION_NOTES
수정하거나 제거한 항목을 간략히 나열하세요.
수정한 것이 없으면 "변경 없음"이라고만 쓰세요.

### REPORT_FINAL
검증이 완료된 최종 Markdown 리포트를 출력하세요.
리포트의 전체 구조와 형식을 유지하고, 검증 기준에 따라 필요한 부분만 수정하세요.
"""


def _check_pool_conflict(verdict: str, minority_reports: list[MinorityReport]) -> str | None:
    """P3 — 풀 안에 winner 판정과 상충하는 defect 후보가 있는지 결정적으로 스캔한다.

    LLM 판단에 맡기지 않는다 — D3(검증 기준을 프롬프트 문구로 완화했다가
    안전망이 무력화된 결함)를 반복하지 않기 위함. minority_reports 는
    pipeline.py 에서 이미 score>0 로 필터링돼 들어오므로 여기서는 재확인만
    한다(신규 threshold 없음, §6 P3).
    """
    if verdict not in _CONFLICT_CHECK_VERDICTS:
        return None
    conflicting = [
        mr for mr in minority_reports
        if getattr(mr.matched_case, "case_verdict", None) == "defect"
        and mr.match_result.score > 0
    ]
    if not conflicting:
        return None
    names = ", ".join(mr.matched_case.name for mr in conflicting)
    return (
        f"⚠️ 이 판정({verdict})과 상충하는 다른 후보가 있습니다: {names}. "
        "판정 레이블은 유지하되, 위 후보들도 함께 검토하세요."
    )


def _parse_response(raw: str, *, fallback: str) -> ReflectionResult:
    """
    LLM 응답에서 REFLECTION_NOTES 와 REPORT_FINAL 섹션을 파싱한다.
    파싱 실패 시 fallback(원본 리포트)을 그대로 반환한다.
    """
    NOTES_MARKER  = "### REFLECTION_NOTES"
    REPORT_MARKER = "### REPORT_FINAL"

    notes_idx  = raw.find(NOTES_MARKER)
    report_idx = raw.find(REPORT_MARKER)

    if notes_idx == -1 or report_idx == -1 or notes_idx >= report_idx:
        return ReflectionResult(
            report_final=fallback,
            notes="(파싱 실패 — Stage 5 리포트 원본 사용)",
        )

    notes  = raw[notes_idx  + len(NOTES_MARKER)  : report_idx].strip()
    report = raw[report_idx + len(REPORT_MARKER) :].strip()

    if not report:
        return ReflectionResult(
            report_final=fallback,
            notes="(빈 REPORT_FINAL — Stage 5 리포트 원본 사용)",
        )

    return ReflectionResult(report_final=report, notes=notes)
