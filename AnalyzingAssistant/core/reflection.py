"""
core/reflection.py

Stage 6 — Reflection (Qwen3-14B)

Stage 5에서 생성된 리포트를 LLM으로 한 번 더 검증하는 자기 검증 단계.

검증 항목:
  - 각 분석 항목에 evidence 로그 라인이 실제로 존재하는가
  - 로그에 근거 없는 추측성 판단이 포함되어 있는가
  - 판정이 score 및 매칭 결과와 일관되는가

검증 결과에 따라 추측성 항목을 제거하거나 "[추정]" 접두어로 구분하여 최종 리포트를 출력한다.

Output: ReflectionResult
  .report_final : 검증 완료된 Markdown 리포트
  .notes        : 수정/제거된 항목 요약 (없으면 "변경 없음")
"""

from __future__ import annotations

from dataclasses import dataclass

from core.config import cfg
from core.llm import chat
from core.log_refiner import LogLine, render_lines
from core.pattern_matcher import MatchResult

MAX_EVIDENCE_LINES = 30
MAX_LOG_SAMPLE = 100


@dataclass
class ReflectionResult:
    """Stage 6 검증 결과."""
    report_final: str
    notes: str = ""


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
        self._model = model if model is not None else cfg.llm_model

    def reflect(
        self,
        report_md: str,
        verdict: str,
        score: float,
        match_result: MatchResult | None,
        l_common: list[LogLine],
        system_analysis_guidelines: str = "",
    ) -> ReflectionResult:
        """
        Stage 5 리포트를 검증하고 최종 리포트를 반환한다.

        Parameters
        ----------
        report_md                  : Stage 5 생성 리포트 (Markdown)
        verdict                    : 판정 ("문제" | "불확실" | "알 수 없음")
        score                      : 진단 점수 (0.0 ~ 1.0)
        match_result               : Stage 4 패턴 매칭 결과 (MISS 면 None)
        l_common                   : Stage 1 정제 로그
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
        raw = chat(
            messages=[{"role": "user", "content": prompt}],
            model=self._model,
            temperature=cfg.llm_report_temperature,
        )
        return _parse_response(raw, fallback=report_md)


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
