"""
core/report_generator.py

Stage 5 — Report Generation (Qwen3-14B)

판정 기준 (score = 기존 KB 패턴과의 유사도, 결함 여부에 대한 최종 판정이 아님):
  유사문제      : score ≥ definite_threshold (config.get_float("pipeline.definite_threshold"))
  유사문제 없음 : matched 패턴이 하나도 없음 (완전히 새로운 문제)
  불확실        : 그 외 (부분 매칭, score 낮음)

경로별 처리:
  유사문제      → LLM: 매칭 케이스/패턴/evidence 기반 구조화 리포트
  불확실        → LLM: 부분 매칭 정보 포함 불확실 리포트
  유사문제 없음 → LLM: L_common 직접 분석 리포트
                 + PatternGenerator: 새 케이스/패턴 후보 생성 (KB 추가 제안)

Output: ReportResult
  .verdict       : "유사문제" | "불확실" | "유사문제 없음"
  .report_md     : Markdown 리포트 문자열
  .kb_suggestion : GenerationResult | None  (유사문제 없음 경로만)
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import core.config as config
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

# 케이스 자신의 판정(cases.verdict) → 리포트 표기 라벨.
# score-tier 판정("유사문제"/"불확실"/"유사문제 없음")과는 별개 축이다 —
# 여긴 케이스가 과거에 스스로 내린 결론을 참고로 인용할 때만 쓴다.
_CASE_VERDICT_LABEL = {
    "defect":       "문제",
    "no_defect":    "문제 아님",
    "undetermined": "판정 불가",
}


def _case_verdict_label(verdict: str | None) -> str:
    return _CASE_VERDICT_LABEL.get(verdict, "판정 미기재")


# ── 결과 타입 ─────────────────────────────────────────────────────────────────

@dataclass
class ReportResult:
    verdict: str                            # "유사문제" | "불확실" | "유사문제 없음"
    report_md: str                          # Markdown 리포트
    kb_suggestion: GenerationResult | None = field(default=None)
    """유사문제 없음 경로에서 PatternGenerator 가 생성한 KB 추가 후보."""


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
        l_common                   : Stage 1 출력 (유사문제 없음 경로에서 직접 분석)
        match_result               : Stage 4 출력
        matched_case               : Stage 2 출력 (MISS 면 None)
        analysis_guidelines        : 병합된 프로파일 분석 지침 (system prompt 주입)
        knowledge_context          : 병합된 SQLite 사전지식 컨텍스트 (system prompt 주입)
        system_analysis_guidelines : 시스템 분석 지침 (모든 프롬프트 최상단에 주입)
        """
        verdict = self._determine_verdict(match_result)

        # ── 컨텍스트 전략 적용 ─────────────────────────────────────────────────
        sg, ag, kc = self._apply_context_strategy(
            system_analysis_guidelines, analysis_guidelines, knowledge_context,
            verdict, match_result, l_common,
        )

        if verdict == "유사문제":
            md = self._generate_report(
                verdict, problem_text, match_result, matched_case, sg, ag, kc, l_common,
                fallback_original_score, cancel_event,
            )
            return ReportResult(verdict=verdict, report_md=md)

        if verdict == "불확실":
            md = self._generate_report(
                verdict, problem_text, match_result, matched_case, sg, ag, kc, l_common,
                fallback_original_score, cancel_event,
            )
            return ReportResult(verdict=verdict, report_md=md)

        # "유사문제 없음"
        md  = self._generate_report(
            verdict, problem_text, match_result, matched_case, sg, ag, kc, l_common,
            cancel_event=cancel_event,
        )
        kb  = self._try_kb_suggestion(problem_text) if self.suggest_kb else None
        return ReportResult(verdict=verdict, report_md=md, kb_suggestion=kb)

    # ── 판정 ──────────────────────────────────────────────────────────────────

    def _determine_verdict(self, r: MatchResult) -> str:
        if not r.matched:
            return "유사문제 없음"
        if r.score >= self.definite_threshold:
            return "유사문제"
        return "불확실"

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
        if verdict == "유사문제 없음":
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
        if verdict == "유사문제":
            return _prompt_matched(problem_text, match_result, matched_case, profile_ctx, fallback_original_score)
        if verdict == "불확실":
            return _prompt_uncertain(problem_text, match_result, matched_case, profile_ctx, fallback_original_score)
        # 유사문제 없음
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

    # ── KB 추가 제안 (유사문제 없음 경로) ───────────────────────────────────────

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


def _fmt_case_verdict_section(case: MatchedCase | None) -> str:
    """매칭된 케이스 자신의 판정을 참고 섹션으로 포맷한다.

    상황·패턴이 모두 일치해 케이스가 특정된 경우에만 의미가 있다 — 케이스가
    특정 안 됐거나(§4 불변식) 케이스에 판정이 아직 기재되지 않은 레거시
    케이스면 빈 문자열을 반환해 프롬프트에서 통째로 생략된다.

    이 값은 score-tier 판정("## 판정: 유사문제")을 절대 대체하지 않는다 —
    LLM이 참고할 추가 정보일 뿐이다.
    """
    if not case or not case.verdict:
        return ""
    actions_text = json.dumps(case.actions, ensure_ascii=False) if case.actions else "(기록된 조치 없음)"
    return f"""
━━━ 매칭 케이스의 원 판정 (참고용) ━━━
이 케이스가 과거에 등록될 때 내려진 판정입니다. 위 진단 점수와는 별개이니,
아래 "원인 분석"·"권장 조치" 작성 시 참고만 하고 그대로 베끼지 마세요.
판정      : {_case_verdict_label(case.verdict)}
판정 근거 : {case.verdict_rationale or "(근거 미기재)"}
조치      : {actions_text}
비고      : {case.notes or "(없음)"}
"""


def _prompt_matched(
    problem_text: str,
    r: MatchResult,
    case: MatchedCase | None,
    profile_ctx: str = "",
    fallback_original_score: float | None = None,
) -> str:
    if case:
        case_info = f"매칭 케이스 : {case.name} (관련성 {case.relevance_score:.0%})\n"
    elif fallback_original_score is not None:
        case_info = (
            f"⚠️ 케이스 검색은 있었으나 케이스 고유 패턴 점수가 낮아"
            f"({fallback_original_score:.0%}) 전체 KB 패턴으로 재검색했습니다.\n"
        )
    else:
        case_info = ""
    case_verdict_section = _fmt_case_verdict_section(case)
    profile_section = f"\n{profile_ctx}\n" if profile_ctx else ""
    return f"""/no_think
아래 커널 로그 분석 결과를 바탕으로 Markdown 형식의 진단 리포트를 작성하세요.
{profile_section}
━━━ 분석 요약 ━━━
문제 상황  : {problem_text}
{case_info}진단 점수  : {r.score:.0%}
{case_verdict_section}
━━━ 매칭된 문제 패턴 ━━━
{_fmt_evidence(r.matched)}

━━━ 미매칭 문제 패턴 ━━━
{chr(10).join(f'- {p.name}' for p in r.unmatched) or '  (없음)'}

━━━ 문제 패턴별 분석지침 ━━━
{_fmt_pattern_guidelines(r.matched)}

━━━ 출력 형식 ━━━
위 분석지침에 따라 아래 섹션을 포함한 Markdown 리포트를 작성하세요.
## 판정: 유사문제
## 원인 분석
## 근거 로그
## 권장 조치
"""


def _prompt_uncertain(
    problem_text: str,
    r: MatchResult,
    case: MatchedCase | None,
    profile_ctx: str = "",
    fallback_original_score: float | None = None,
) -> str:
    if case:
        case_info = f"참고 케이스 : {case.name} (관련성 {case.relevance_score:.0%})\n"
    elif fallback_original_score is not None:
        case_info = (
            f"⚠️ 케이스 검색은 있었으나 케이스 고유 패턴 점수가 낮아"
            f"({fallback_original_score:.0%}) 전체 KB 패턴으로 재검색했습니다.\n"
        )
    else:
        case_info = ""
    profile_section = f"\n{profile_ctx}\n" if profile_ctx else ""
    return f"""/no_think
아래 커널 로그 분석 결과를 바탕으로 Markdown 형식의 진단 리포트를 작성하세요.
{profile_section}
━━━ 분석 요약 ━━━
문제 상황  : {problem_text}
{case_info}불확실 이유 : 진단 점수 낮음 ({r.score:.0%})

━━━ 부분 매칭된 문제 패턴 ━━━
{_fmt_evidence(r.matched)}

━━━ 미매칭 문제 패턴 ━━━
{chr(10).join(f'- {p.name}' for p in r.unmatched) or '  (없음)'}

━━━ 문제 패턴별 분석지침 ━━━
{_fmt_pattern_guidelines(r.matched)}

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
## 판정: 유사문제 없음 (신규 문제 가능성)
## 관찰된 현상
## 가능한 원인
## 권장 조치
## KB 추가 권고
  (이 문제를 KB 에 등록하면 향후 자동 진단이 가능합니다)
"""
