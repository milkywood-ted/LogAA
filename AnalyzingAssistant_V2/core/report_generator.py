"""
core/report_generator.py

Stage 5 — Report Generation (Qwen3-14B)

판정은 독립된 두 축의 조합으로 결정된다.

축 1 — 일치도(match_level) : 로그가 매칭 케이스의 패턴 시그니처를 얼마나 재현하는가
  높음 : score ≥ definite_threshold (config.get_float("pipeline.definite_threshold"))
  부분 : 일부 패턴만 매칭 (score < threshold)
  없음 : matched 패턴이 하나도 없음 (완전히 새로운 문제)

축 2 — 원 분석 판정(MatchedCase.case_verdict) : 그 케이스가 애초에 결함으로 결론났는가
  'defect' | 'no_defect' | 'undetermined' | None(스키마 도입 전 레거시 행)

일치도만으로는 "이 로그가 케이스 X 와 닮았다" 까지만 말할 수 있고 그것이 결함인지는
케이스 자신의 판정에 달려 있다. 특히 일치도가 높은데 원 분석이 'no_defect' 면
"문제 없음이 이미 확인된 패턴" 이라는 뜻이므로 문제로 보고해서는 안 된다.

최종 판정(verdict) — _determine_verdict 참조:
  일치도 없음                         → 알 수 없음
  일치도 부분                         → 불확실
  일치도 높음 + defect / None(레거시)  → 문제
  일치도 높음 + no_defect             → 문제 아님
  일치도 높음 + undetermined          → 판정 불가

경로별 처리:
  문제      → LLM: 매칭 케이스/패턴/evidence 기반 구조화 리포트
  문제 아님  → LLM: 원 분석의 무결함 판정 근거 기반 리포트
  판정 불가  → LLM: 과거 미판정 사유 + 추가 확인 항목 리포트
  불확실    → LLM: 부분 매칭 정보 포함 불확실 리포트
  알 수 없음 → LLM: L_common 직접 분석 리포트
              + PatternGenerator: 새 케이스/패턴 후보 생성 (KB 추가 제안)

Output: ReportResult
  .verdict       : "문제" | "문제 아님" | "판정 불가" | "불확실" | "알 수 없음"
  .match_level   : "높음" | "부분" | "없음"
  .report_md     : Markdown 리포트 문자열
  .kb_suggestion : GenerationResult | None  (알 수 없음 경로만)
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import core.config as config
from core.case_report import (
    action_lines,
    undetermined_reason_text,
    verdict_label,
)
from core.db import DB_PATH
from core.llm import chat, chat_stream
from core.log_refiner import LogLine, render_lines
from core.pattern_matcher import MatchResult, PatternResult

if TYPE_CHECKING:
    from core.kb_search import MatchedCase
    from core.pattern_generator import GenerationResult

logger = logging.getLogger(__name__)

# ── 상수 ──────────────────────────────────────────────────────────────────────

MAX_EVIDENCE_LINES = 30     # 패턴당 evidence 최대 라인 수


# ── 결과 타입 ─────────────────────────────────────────────────────────────────

@dataclass
class ReportResult:
    verdict: str                            # "문제"|"문제 아님"|"판정 불가"|"불확실"|"알 수 없음"
    report_md: str                          # Markdown 리포트
    kb_suggestion: GenerationResult | None = field(default=None)
    """알 수 없음 경로에서 PatternGenerator 가 생성한 KB 추가 후보."""
    match_level: str = "없음"               # "높음" | "부분" | "없음"
    """패턴 일치도 축 — verdict 와 달리 결함 여부를 뜻하지 않는다."""


# 일치도가 "높음" 일 때 원 분석 판정을 최종 판정으로 옮기는 표.
# 레거시 행(case_verdict=None)과 'defect' 는 기존 동작인 "문제" 를 유지한다.
_CASE_VERDICT_TO_VERDICT = {
    "defect":       "문제",
    "no_defect":    "문제 아님",
    "undetermined": "판정 불가",
}

# 판정·조치 토큰의 표기 문구는 core.case_report 가 단일 출처다 (frontend 라벨과 동기).


# ── ReportGenerator ───────────────────────────────────────────────────────────

class ReportGenerator:
    """
    Stage 5 실행기.

        generator = ReportGenerator()
        result = generator.generate(
            problem_text  = "ATA 드라이브 타임아웃 반복",
            l_common      = l_common,
            match_result  = match_result,
            matched_case  = matched_case,   # Stage 2 결과 (MISS 면 None)
        )
    """

    def __init__(
        self,
        model: str | None              = None,
        db_path: Path                  = DB_PATH,
        definite_threshold: float | None = None,
        max_log_lines: int | None      = None,
        suggest_kb: bool               = True,
        context_strategy: str | None   = None,
        hybrid_overflow_ratio: float | None = None,
        num_ctx: int | None            = None,
    ) -> None:
        # None 이면 호출 시점의 config 값 사용 (import 시 고정 방지)
        self._model               = model if model is not None else config.active_llm().get("model", "")
        self.db_path              = db_path
        self.definite_threshold   = definite_threshold if definite_threshold is not None else config.get_float("pipeline.definite_threshold", 0.50)
        self.max_log_lines        = max_log_lines if max_log_lines is not None else config.get_int("pipeline.max_log_lines", 200)
        self.suggest_kb           = suggest_kb
        self._context_strategy    = context_strategy    if context_strategy    is not None else config.get_str("pipeline.context_strategy", "truncation")
        self._hybrid_overflow     = hybrid_overflow_ratio if hybrid_overflow_ratio is not None else config.get_float("pipeline.hybrid_overflow_ratio", 0.3)
        self._num_ctx             = num_ctx             if num_ctx             is not None else config.get("pipeline.num_ctx")

    # ── 공개 API ──────────────────────────────────────────────────────────────

    def generate(
        self,
        problem_text: str,
        l_common: list[LogLine],
        match_result: MatchResult,
        matched_case: MatchedCase | None = None,
        analysis_guidelines: str = "",
        knowledge_context: str = "",
        system_analysis_guidelines: str = "",
        fallback_original_score: float | None = None,
        cancel_event: threading.Event | None = None,
    ) -> ReportResult:
        """
        Stage 5 전체 실행.

        Parameters
        ----------
        problem_text               : 사용자가 입력한 문제 설명
        l_common                   : Stage 1 출력 (알 수 없음 경로에서 직접 분석)
        match_result               : Stage 4 출력
        matched_case               : Stage 2 출력 (MISS 면 None)
        analysis_guidelines        : 병합된 프로파일 분석 지침 (system prompt 주입)
        knowledge_context          : 병합된 SQLite 사전지식 컨텍스트 (system prompt 주입)
        system_analysis_guidelines : 시스템 분석 지침 (모든 프롬프트 최상단에 주입)
        """
        match_level = self._determine_match_level(match_result)
        verdict     = self._determine_verdict(match_level, matched_case)

        # ── 컨텍스트 전략 적용 ─────────────────────────────────────────────────
        sg, ag, kc = self._apply_context_strategy(
            system_analysis_guidelines, analysis_guidelines, knowledge_context,
            verdict, match_result, l_common,
        )

        md = self._generate_report(
            verdict, problem_text, match_result, matched_case, sg, ag, kc, l_common,
            fallback_original_score, cancel_event,
        )

        # KB 추가 제안은 매칭이 전무한 "알 수 없음" 경로에만 의미가 있다.
        kb = (
            self._try_kb_suggestion(problem_text)
            if verdict == "알 수 없음" and self.suggest_kb
            else None
        )
        return ReportResult(
            verdict       = verdict,
            report_md     = md,
            kb_suggestion = kb,
            match_level   = match_level,
        )

    # ── 판정 ──────────────────────────────────────────────────────────────────

    def _determine_match_level(self, r: MatchResult) -> str:
        """축 1 — 로그가 케이스 패턴 시그니처를 얼마나 재현하는가."""
        if not r.matched:
            return "없음"
        if r.score >= self.definite_threshold:
            return "높음"
        return "부분"

    def _determine_verdict(
        self, match_level: str, matched_case: MatchedCase | None = None
    ) -> str:
        """축 1(일치도) 과 축 2(원 분석 판정) 를 합쳐 최종 판정을 낸다.

        일치도가 "높음" 일 때만 원 분석 판정이 결과를 좌우한다. "부분"·"없음"
        에서는 어느 케이스인지 자체가 확정되지 않았으므로 그 케이스의 판정을
        끌어와도 근거가 되지 않는다.
        """
        if match_level == "없음":
            return "알 수 없음"
        if match_level == "부분":
            return "불확실"
        case_verdict = matched_case.case_verdict if matched_case else None
        return _CASE_VERDICT_TO_VERDICT.get(case_verdict, "문제")

    # ── LLM 호출 ──────────────────────────────────────────────────────────────

    def _call_llm(
        self,
        prompt: str,
        cancel_event: threading.Event | None = None,
    ) -> str:
        return chat_stream(
            messages     = [{"role": "user", "content": prompt}],
            model        = self._model,
            temperature  = config.active_llm().get("report_temperature", 0.2),
            cancel_event = cancel_event,
        )

    # ── 컨텍스트 전략 ─────────────────────────────────────────────────────────

    def _estimate_fixed_tokens(
        self,
        verdict: str,
        match_result: MatchResult,
        l_common: list[LogLine],
    ) -> int:
        """verdict 경로별 고정 데이터(evidence/로그/프롬프트 골격)의 토큰 수 추정."""
        from core.context_strategy import estimate_tokens
        if verdict == "알 수 없음":
            log_text = render_lines(l_common[:self.max_log_lines])
            return estimate_tokens(log_text) + 300
        else:
            evidence_text    = _fmt_evidence(match_result.matched)
            guidelines_text  = _fmt_pattern_guidelines(match_result.matched)
            return estimate_tokens(evidence_text + guidelines_text) + 300

    def _apply_context_strategy(
        self,
        system_guidelines: str,
        analysis_guidelines: str,
        knowledge_context: str,
        verdict: str,
        match_result: MatchResult,
        l_common: list[LogLine],
    ) -> tuple[str, str, str]:
        """
        num_ctx 와 context_strategy 설정에 따라 컨텍스트를 전처리한다.

        num_ctx 미설정 시 전략을 적용하지 않고 원본을 반환한다.

        Returns
        -------
        (system_guidelines, analysis_guidelines, knowledge_context)
        """
        if self._num_ctx is None:
            return system_guidelines, analysis_guidelines, knowledge_context

        from core.context_strategy import (
            truncate_context,
            calc_overflow_ratio,
        )

        fixed = self._estimate_fixed_tokens(verdict, match_result, l_common)
        strategy = self._context_strategy

        if strategy == "truncation":
            ctx = truncate_context(
                system_guidelines, analysis_guidelines, knowledge_context,
                fixed, self._num_ctx,
            )
            return ctx.system_guidelines, ctx.analysis_guidelines, ctx.knowledge_context

        if strategy == "hybrid":
            ratio = calc_overflow_ratio(
                system_guidelines, analysis_guidelines, knowledge_context,
                fixed, self._num_ctx,
            )
            if ratio < self._hybrid_overflow:
                # overflow 비율이 임계값 미만 → truncation 사용
                ctx = truncate_context(
                    system_guidelines, analysis_guidelines, knowledge_context,
                    fixed, self._num_ctx,
                )
                return ctx.system_guidelines, ctx.analysis_guidelines, ctx.knowledge_context
            # 임계값 이상 → split 경로로 넘어감 (원본 반환, _generate_report에서 처리)
            return system_guidelines, analysis_guidelines, knowledge_context

        # "split" 또는 미인식 전략 → 원본 반환 (_generate_report에서 split 처리)
        return system_guidelines, analysis_guidelines, knowledge_context

    def _generate_report(
        self,
        verdict: str,
        problem_text: str,
        match_result: MatchResult,
        matched_case,
        system_guidelines: str,
        analysis_guidelines: str,
        knowledge_context: str,
        l_common: list[LogLine],
        fallback_original_score: float | None = None,
        cancel_event: threading.Event | None = None,
    ) -> str:
        """
        verdict 별 프롬프트를 빌드하고 컨텍스트 전략(split/truncation)을 적용하여
        LLM을 호출한다.
        """
        # split 전략이 필요한지 판단
        use_split = (
            self._num_ctx is not None
            and self._context_strategy in ("split", "hybrid")
        )

        if use_split and self._num_ctx is not None:
            from core.context_strategy import split_knowledge_chunks, calc_overflow_ratio

            fixed = self._estimate_fixed_tokens(verdict, match_result, l_common)

            # hybrid: 실제 overflow 비율 재확인
            if self._context_strategy == "hybrid":
                ratio = calc_overflow_ratio(
                    system_guidelines, analysis_guidelines, knowledge_context,
                    fixed, self._num_ctx,
                )
                if ratio < self._hybrid_overflow:
                    # truncation 으로 충분 → 단일 호출
                    use_split = False

            if use_split:
                chunks = split_knowledge_chunks(
                    knowledge_context, system_guidelines, analysis_guidelines,
                    fixed, self._num_ctx,
                )
                if len(chunks) <= 1:
                    use_split = False
                else:
                    # 첫 번째 청크로 초기 리포트 생성
                    profile_ctx = _build_profile_context(
                        system_guidelines, analysis_guidelines, chunks[0]
                    )
                    current = self._call_llm(
                        self._build_prompt(verdict, problem_text, match_result,
                                           matched_case, profile_ctx, l_common,
                                           fallback_original_score),
                        cancel_event,
                    )
                    # 나머지 청크로 순차 보완
                    for chunk in chunks[1:]:
                        refine_prompt = _prompt_refine(problem_text, current, chunk)
                        current = self._call_llm(refine_prompt, cancel_event)
                    return current

        # summarize_split: 지식 컨텍스트를 먼저 요약한 뒤 단일 리포트 호출
        use_summarize_split = (
            self._num_ctx is not None
            and self._context_strategy == "summarize_split"
        )
        if use_summarize_split:
            from core.context_strategy import split_knowledge_chunks, calc_overflow_ratio

            fixed = self._estimate_fixed_tokens(verdict, match_result, l_common)
            ratio = calc_overflow_ratio(
                system_guidelines, analysis_guidelines, knowledge_context,
                fixed, self._num_ctx,
            )
            if ratio > 0.0:
                # 실제 초과가 있을 때만 요약 경로 진행
                chunks = split_knowledge_chunks(
                    knowledge_context, system_guidelines, analysis_guidelines,
                    fixed, self._num_ctx,
                )
                if len(chunks) > 1:
                    knowledge_context = self._summarize_knowledge(
                        knowledge_context=knowledge_context,
                        chunks=chunks,
                        problem_text=problem_text,
                        cancel_event=cancel_event,
                    )

        # 단일 호출 (truncation / hybrid-truncation / split 불필요 / summarize_split 요약 완료)
        profile_ctx = _build_profile_context(
            system_guidelines, analysis_guidelines, knowledge_context
        )
        return self._call_llm(
            self._build_prompt(verdict, problem_text, match_result,
                               matched_case, profile_ctx, l_common,
                               fallback_original_score),
            cancel_event,
        )

    def _build_prompt(
        self,
        verdict: str,
        problem_text: str,
        match_result: MatchResult,
        matched_case,
        profile_ctx: str,
        l_common: list[LogLine],
        fallback_original_score: float | None = None,
    ) -> str:
        """verdict 별 프롬프트 문자열을 반환한다."""
        if verdict == "문제":
            return _prompt_matched(problem_text, match_result, matched_case, profile_ctx, fallback_original_score)
        if verdict == "문제 아님":
            return _prompt_no_defect(problem_text, match_result, matched_case, profile_ctx)
        if verdict == "판정 불가":
            return _prompt_case_undetermined(problem_text, match_result, matched_case, profile_ctx)
        if verdict == "불확실":
            return _prompt_uncertain(problem_text, match_result, matched_case, profile_ctx, fallback_original_score)
        # 알 수 없음
        return _prompt_unknown(problem_text, l_common, self.max_log_lines, profile_ctx)

    # ── 사전지식 요약 (summarize_split 전략) ─────────────────────────────────

    def _summarize_knowledge(
        self,
        knowledge_context: str,
        chunks: list[str],
        problem_text: str,
        cancel_event: threading.Event | None = None,
    ) -> str:
        """
        knowledge_context 를 청크별로 순차 요약하여 압축된 단일 텍스트를 반환한다.

        - 각 청크는 독립적으로 요약되지 않고, 이전 요약에 누적 반영된다.
        - 요약 실패 시 해당 청크를 건너뛰고 경고만 남긴다.
        - 최종 요약이 비어있으면 원본 knowledge_context 를 반환(안전 폴백).
        """
        logger.info("summarize_split: %d개 청크 요약 시작", len(chunks))
        current_summary = ""
        for i, chunk in enumerate(chunks):
            try:
                current_summary = self._call_llm(
                    _prompt_summarize_knowledge(problem_text, current_summary, chunk, i + 1, len(chunks)),
                    cancel_event,
                )
            except InterruptedError:
                raise  # 취소 신호는 상위로 전파
            except Exception:
                logger.warning("summarize_split: 청크 %d/%d 요약 실패 — 건너뜀", i + 1, len(chunks))
        if not current_summary.strip():
            logger.warning("summarize_split: 요약 결과가 비어있음 — 원본 knowledge_context 사용")
            return knowledge_context
        logger.info("summarize_split: 요약 완료 (%d자 → %d자)", len(knowledge_context), len(current_summary))
        return current_summary

    # ── KB 추가 제안 (알 수 없음 경로) ───────────────────────────────────────

    def _try_kb_suggestion(self, problem_text: str) -> GenerationResult | None:
        """
        PatternGenerator 로 새 케이스/패턴 후보를 생성한다.
        실패해도 리포트 생성에는 영향을 주지 않는다.
        """
        try:
            from core.pattern_generator import PatternGenerator
            return PatternGenerator(
                db_path=self.db_path,
            ).generate(problem_text)
        except Exception:
            return None


# ── 프롬프트 빌더 ─────────────────────────────────────────────────────────────

def _build_profile_context(
    system_analysis_guidelines: str,
    analysis_guidelines: str,
    knowledge_context: str,
) -> str:
    """시스템 분석 지침·프로파일 분석 지침·사전지식을 프롬프트 주입용 텍스트로 조합한다."""
    parts: list[str] = []
    if system_analysis_guidelines.strip():
        parts.append(f"━━━ 시스템 분석 지침 ━━━\n{system_analysis_guidelines.strip()}")
    if analysis_guidelines.strip():
        parts.append(f"━━━ 분석 지침 ━━━\n{analysis_guidelines.strip()}")
    if knowledge_context.strip():
        parts.append(f"━━━ 사전지식 ━━━\n{knowledge_context.strip()}")
    return "\n\n".join(parts)


def _fmt_pattern_guidelines(patterns: list[PatternResult]) -> str:
    """매칭된 문제 패턴의 분석지침을 텍스트로 변환한다."""
    parts: list[str] = []
    for p in patterns:
        if p.analysis_guidelines.strip():
            parts.append(
                f"[{p.name} / {p.type}]\n{p.analysis_guidelines.strip()}"
            )
    return "\n\n".join(parts) if parts else "  (등록된 분석지침 없음)"

def _fmt_evidence(patterns: list[PatternResult], max_lines: int = MAX_EVIDENCE_LINES) -> str:
    """매칭된 패턴과 evidence 라인을 텍스트로 변환한다."""
    parts: list[str] = []
    for p in patterns:
        lines_txt = "\n".join(
            f"    {ll.render()}" for ll in p.evidence[:max_lines]
        )
        suffix = f"\n    ... ({len(p.evidence) - max_lines}줄 생략)" \
                 if len(p.evidence) > max_lines else ""
        parts.append(
            f"- **{p.name}** (weight={p.weight})\n"
            f"{lines_txt}{suffix}"
        )
    return "\n".join(parts) if parts else "  (없음)"


def _fmt_case_verdict_line(case: MatchedCase | None) -> str:
    """참고 케이스의 원 분석 판정을 한 줄로 표기한다. 미기재면 빈 문자열.

    일치도가 "부분" 인 경로에서는 케이스 자체가 확정되지 않았으므로 참고
    정보로만 제시하고, 이 판정을 결론으로 채택하지 않도록 명시한다.
    """
    if case is None or case.case_verdict is None:
        return ""
    label = verdict_label(case.case_verdict)
    return f"참고 케이스의 원 분석 판정 : {label} (일치도가 낮으므로 결론으로 채택하지 말 것)\n"


def _fmt_verdict_rationale(case: MatchedCase | None) -> str:
    """원 분석의 판정 근거를 프롬프트 섹션으로 변환한다. 없으면 빈 문자열."""
    if case is None or not case.verdict_rationale.strip():
        return ""
    return f"\n━━━ 원 분석의 판정 근거 ━━━\n{case.verdict_rationale.strip()}\n"


def _fmt_undetermined_reason(case: MatchedCase | None) -> str:
    """판정불가 사유 코드를 사람이 읽는 문구로 변환한다."""
    if case is None:
        return "사유 미기재"
    return undetermined_reason_text(case.undetermined_reason, case.undetermined_reason_note)


def _fmt_case_actions(case: MatchedCase | None) -> str:
    """원 분석의 조치 이력을 프롬프트 섹션으로 변환한다. 조치가 없으면 빈 문자열.

    일치도가 "부분" 인 경로에는 주입하지 않는다 — 케이스가 확정되지 않은
    상태에서 그 케이스의 대응 이력을 제시하면 이번 건의 조치로 오독된다.
    """
    if case is None:
        return ""
    lines = action_lines(case.actions)
    if not lines:
        return ""
    body = "\n".join(f"- {ln}" for ln in lines)
    return f"\n━━━ 원 분석의 조치 이력 ━━━\n{body}\n"


def _prompt_matched(
    problem_text: str,
    r: MatchResult,
    case: MatchedCase | None,
    profile_ctx: str = "",
    fallback_original_score: float | None = None,
) -> str:
    if case:
        if fallback_original_score is not None:
            case_info = (
                f"매칭 케이스 : {case.name} (관련성 {case.relevance_score:.0%})"
                f" ⚠️ 케이스 패턴 점수 낮음({fallback_original_score:.0%}), 전체 패턴으로 재시도\n"
            )
        else:
            case_info = f"매칭 케이스 : {case.name} (관련성 {case.relevance_score:.0%})\n"
    else:
        case_info = ""
    profile_section = f"\n{profile_ctx}\n" if profile_ctx else ""
    return f"""/no_think
아래 커널 로그 분석 결과를 바탕으로 Markdown 형식의 진단 리포트를 작성하세요.
{profile_section}
━━━ 분석 요약 ━━━
문제 상황  : {problem_text}
{case_info}패턴 일치도 : {r.score:.0%}

━━━ 매칭된 문제 패턴 ━━━
{_fmt_evidence(r.matched)}

━━━ 미매칭 문제 패턴 ━━━
{chr(10).join(f'- {p.name}' for p in r.unmatched) or '  (없음)'}

━━━ 문제 패턴별 분석지침 ━━━
{_fmt_pattern_guidelines(r.matched)}
{_fmt_verdict_rationale(case)}{_fmt_case_actions(case)}
━━━ 출력 형식 ━━━
위 분석지침에 따라 아래 섹션을 포함한 Markdown 리포트를 작성하세요.
조치 이력이 주어졌다면 "권장 조치" 에서 그 이력을 먼저 밝히세요 — 이미 수정된
건인지, 결함으로 인정하되 보류된 건인지, 다른 주체로 이관된 건인지에 따라
이번에 필요한 조치가 달라집니다. 이미 종결된 대응을 다시 제안하지 마세요.
## 판정: 문제
## 원인 분석
## 근거 로그
## 권장 조치
"""


def _prompt_no_defect(
    problem_text: str,
    r: MatchResult,
    case: MatchedCase | None,
    profile_ctx: str = "",
) -> str:
    """일치도 높음 + 원 분석 'no_defect' — 이미 무결함으로 결론난 패턴의 재현."""
    case_info = f"매칭 케이스 : {case.name} (관련성 {case.relevance_score:.0%})\n" if case else ""
    profile_section = f"\n{profile_ctx}\n" if profile_ctx else ""
    return f"""/no_think
아래 커널 로그 분석 결과를 바탕으로 Markdown 형식의 진단 리포트를 작성하세요.
{profile_section}
━━━ 분석 요약 ━━━
문제 상황  : {problem_text}
{case_info}패턴 일치도 : {r.score:.0%}

이 로그는 위 케이스의 패턴과 높은 일치도를 보이지만, 해당 케이스는 과거 분석에서
**결함 아님(no_defect)** 으로 결론된 건입니다. 따라서 아래 매칭된 패턴들은
결함의 증거가 아니라, 이미 무해한 것으로 확인된 현상이 재현된 것입니다.
사용자가 보고한 문제 상황의 원인은 다른 곳에 있을 가능성이 높습니다.

━━━ 매칭된 패턴 (무결함으로 확인된 현상) ━━━
{_fmt_evidence(r.matched)}

━━━ 미매칭 패턴 ━━━
{chr(10).join(f'- {p.name}' for p in r.unmatched) or '  (없음)'}
{_fmt_verdict_rationale(case)}{_fmt_case_actions(case)}
━━━ 출력 형식 ━━━
아래 섹션을 포함한 Markdown 리포트를 작성하세요. 매칭된 패턴을 결함으로
서술하지 말고, 무결함으로 판정된 근거를 그대로 전달하세요.
## 판정: 문제 아님
## 매칭된 현상과 무결함 판정 근거
## 근거 로그
## 보고된 문제 상황에 대한 검토 방향
"""


def _prompt_case_undetermined(
    problem_text: str,
    r: MatchResult,
    case: MatchedCase | None,
    profile_ctx: str = "",
) -> str:
    """일치도 높음 + 원 분석 'undetermined' — 과거에도 판정하지 못한 유형."""
    case_info = f"매칭 케이스 : {case.name} (관련성 {case.relevance_score:.0%})\n" if case else ""
    profile_section = f"\n{profile_ctx}\n" if profile_ctx else ""
    return f"""/no_think
아래 커널 로그 분석 결과를 바탕으로 Markdown 형식의 진단 리포트를 작성하세요.
{profile_section}
━━━ 분석 요약 ━━━
문제 상황  : {problem_text}
{case_info}패턴 일치도 : {r.score:.0%}

이 로그는 위 케이스의 패턴과 높은 일치도를 보이지만, 해당 케이스는 과거 분석에서
결함 여부를 확정하지 못하고 **판정 불가(undetermined)** 로 남은 건입니다.
과거 판정불가 사유: {_fmt_undetermined_reason(case)}

━━━ 매칭된 패턴 ━━━
{_fmt_evidence(r.matched)}

━━━ 미매칭 패턴 ━━━
{chr(10).join(f'- {p.name}' for p in r.unmatched) or '  (없음)'}

━━━ 패턴별 분석지침 ━━━
{_fmt_pattern_guidelines(r.matched)}
{_fmt_verdict_rationale(case)}{_fmt_case_actions(case)}
━━━ 출력 형식 ━━━
아래 섹션을 포함한 Markdown 리포트를 작성하세요. 결함으로 단정하지 말고,
과거에 판정을 막았던 사유가 이번 로그에서도 해소되지 않았는지 확인하여
무엇을 더 확보해야 판정이 가능한지 구체적으로 제시하세요.
조치 이력에 "추가 조치 필요" 항목이 있다면 그것이 이번 로그에서 충족되었는지
먼저 확인하고, 남은 항목을 추가 확보 항목에 반영하세요.
## 판정: 판정 불가
## 관찰된 현상
## 과거 판정불가 사유와 이번 로그의 상태
## 근거 로그
## 판정에 필요한 추가 확보 항목
"""


def _prompt_uncertain(
    problem_text: str,
    r: MatchResult,
    case: MatchedCase | None,
    profile_ctx: str = "",
    fallback_original_score: float | None = None,
) -> str:
    if case:
        if fallback_original_score is not None:
            case_info = (
                f"참고 케이스 : {case.name} (관련성 {case.relevance_score:.0%})"
                f" ⚠️ 케이스 패턴 점수 낮음({fallback_original_score:.0%}), 전체 패턴으로 재시도\n"
            )
        else:
            case_info = f"참고 케이스 : {case.name} (관련성 {case.relevance_score:.0%})\n"
    else:
        case_info = ""
    profile_section = f"\n{profile_ctx}\n" if profile_ctx else ""
    return f"""/no_think
아래 커널 로그 분석 결과를 바탕으로 Markdown 형식의 진단 리포트를 작성하세요.
{profile_section}
━━━ 분석 요약 ━━━
문제 상황  : {problem_text}
{case_info}불확실 이유 : 패턴 일치도 낮음 ({r.score:.0%})
{_fmt_case_verdict_line(case)}
━━━ 부분 매칭된 문제 패턴 ━━━
{_fmt_evidence(r.matched)}

━━━ 미매칭 문제 패턴 ━━━
{chr(10).join(f'- {p.name}' for p in r.unmatched) or '  (없음)'}

━━━ 문제 패턴별 분석지침 ━━━
{_fmt_pattern_guidelines(r.matched)}
{_fmt_verdict_rationale(case)}
━━━ 출력 형식 ━━━
위 분석지침에 따라 아래 섹션을 포함한 Markdown 리포트를 작성하세요.
## 판정: 불확실
## 관찰된 현상
## 가능한 원인 (확정되지 않음)
## 권장 추가 확인 항목
## 권장 조치
"""


def _prompt_refine(
    problem_text: str,
    previous_report: str,
    additional_knowledge: str,
) -> str:
    """split 전략의 후속 청크용 — 이전 리포트를 추가 사전지식으로 보완한다."""
    return f"""/no_think
이전 분석 리포트를 아래 추가 사전지식을 참조하여 필요한 경우 보완하세요.
변경이 필요 없으면 이전 리포트를 그대로 반환하세요.

━━━ 이전 분석 리포트 ━━━
{previous_report}

━━━ 추가 사전지식 ━━━
{additional_knowledge}

━━━ 문제 상황 ━━━
{problem_text}
"""


def _prompt_summarize_knowledge(
    problem_text: str,
    previous_summary: str,
    chunk: str,
    chunk_idx: int,
    total_chunks: int,
) -> str:
    """
    summarize_split 전략의 지식 요약 프롬프트.

    이전 요약에 현재 청크를 누적 반영하여 핵심 관계·인과 사슬을 보존한 요약을 생성한다.
    """
    prev_section = (
        f"━━━ 현재까지의 요약 ({chunk_idx - 1}/{total_chunks} 청크) ━━━\n{previous_summary}\n\n"
        if previous_summary.strip() else ""
    )
    return f"""/no_think
아래 사전지식을 분석 문제 맥락에 맞게 핵심 내용만 요약하세요.
항목 간 인과 관계와 의존성이 있으면 반드시 보존하세요.
{prev_section}━━━ 추가 사전지식 ({chunk_idx}/{total_chunks} 청크) ━━━
{chunk}

━━━ 분석 문제 상황 ━━━
{problem_text}

━━━ 요약 지침 ━━━
- 문제 상황과 관련 없는 내용은 제외한다
- 인과 관계·순서·조건 정보는 그대로 유지한다
- 이전 요약과 중복되는 내용은 병합한다
- 출력은 요약 텍스트만, 다른 설명 없이 작성한다
"""


def _prompt_unknown(
    problem_text: str,
    l_common: list[LogLine],
    max_lines: int,
    profile_ctx: str = "",
) -> str:
    log_txt = render_lines(l_common[:max_lines])
    suffix  = (f"\n... ({len(l_common) - max_lines}줄 생략)"
               if len(l_common) > max_lines else "")
    profile_section = f"\n{profile_ctx}\n" if profile_ctx else ""
    return f"""/no_think
기존 KB 에 일치하는 케이스가 없습니다. 아래 커널 로그를 직접 분석하여
Markdown 형식의 진단 리포트를 작성하세요.
{profile_section}
━━━ 문제 상황 ━━━
{problem_text}

━━━ 커널 로그 ━━━
{log_txt}{suffix}

━━━ 출력 형식 ━━━
아래 섹션을 포함한 Markdown 리포트를 작성하세요.
## 판정: 알 수 없음 (신규 문제 가능성)
## 관찰된 현상
## 가능한 원인
## 권장 조치
## KB 추가 권고
  (이 문제를 KB 에 등록하면 향후 자동 진단이 가능합니다)
"""
