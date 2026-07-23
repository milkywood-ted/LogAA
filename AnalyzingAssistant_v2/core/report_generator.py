"""
core/report_generator.py

Stage 5 — Report Generation (Qwen3-14B)

판정(verdict)은 두 개의 독립된 축으로 이뤄진다. 이 둘을 하나의 값으로 섞지
않는다(2026-07-23, 실사용 중 재현·확정) — 섞으면 "score 100% 인데 불확실"
같은, 사람이 납득할 수 없는 결과가 나온다.

축 1 — 유사도 판정(verdict, 이 모듈이 늘 계산해오던 것 — 값 이름만 정정):
  로그가 매칭 케이스의 패턴 시그니처를 얼마나 재현하는가만 본다.
  score 는 순수 패턴 커버리지이지 결함 확률이 아니다. "문제"류 단어를 쓰면
  유사도와 결함확정을 같은 뜻으로 오독하게 되므로 유사도 어휘로만 표기한다.
    유사도 높음 : score ≥ definite_threshold
    유사도 중간 : matched 패턴은 있으나 score < definite_threshold (그레이존)
    유사도 낮음 : matched 패턴이 하나도 없음 (이미 탈락 — 신규 문제 취급)
  이 계산은 fallback(전체 패턴 재매칭) 여부와 **무관**하다 — fallback 으로
  얻은 score 도 똑같이 "패턴이 이만큼 재현됐다"는 사실이며, 그 사실 자체는
  거짓이 아니다.

축 2 — 케이스 원 판정 "인용"(별도 판정 로직 아님):
  matched_case.case_verdict(defect/no_defect/undetermined)를 있는 그대로
  프롬프트에 인용한다. 계산이나 분기가 아니라 그냥 값을 옮기는 것 — 그래서
  verdict 값에 전혀 영향을 주지 않는다. 단, fallback 채택 시(evidence 의
  출처가 matched_case 자신의 패턴이 아니게 됨, §4 불변식 1)에는 인용 자체를
  생략한다 — 케이스 이름조차 노출하지 않는다. 안 그러면 무관한 evidence 옆에
  케이스 정체성만 남아 "케이스 제목과 패턴이 섞여 보이는" D2 완화형이 재발한다.

경로별 처리:
  유사도 높음 → LLM: 매칭 케이스/패턴/evidence 기반 구조화 리포트 (+ 케이스
                원 판정 인용, fallback 미채택 시)
  유사도 중간 → LLM: 부분 매칭 정보 포함 리포트. 그레이존 — 사용자가 수동
                분석할지 신규 문제 파이프라인으로 넘길지 선택(프론트)
  유사도 낮음 → LLM: L_common 직접 분석 리포트
                + PatternGenerator: 새 케이스/패턴 후보 생성 (KB 추가 제안)

Output: ReportResult
  .verdict       : "유사도 높음" | "유사도 중간" | "유사도 낮음"  (축 1만)
  .report_md     : Markdown 리포트 문자열 (케이스 원 판정 인용은 본문에 포함)
  .kb_suggestion : GenerationResult | None  (유사도 낮음 경로만)
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
    defect_area_text,
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

# 판정·조치 토큰의 표기 문구는 core.case_report 가 단일 출처다 (frontend 라벨과 동기, C6).


# ── 결과 타입 ─────────────────────────────────────────────────────────────────

@dataclass
class ReportResult:
    verdict: str                            # "유사도 높음" | "유사도 중간" | "유사도 낮음" (축 1)
    report_md: str                          # Markdown 리포트 (케이스 원 판정 인용은 본문에 포함)
    kb_suggestion: GenerationResult | None = field(default=None)
    """유사도 낮음 경로에서 PatternGenerator 가 생성한 KB 추가 후보."""


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
        verdict = self._determine_verdict(match_result)

        # ── 컨텍스트 전략 적용 ─────────────────────────────────────────────────
        sg, ag, kc = self._apply_context_strategy(
            system_analysis_guidelines, analysis_guidelines, knowledge_context,
            verdict, match_result, l_common, matched_case, fallback_original_score,
        )

        md = self._generate_report(
            verdict, problem_text, match_result, matched_case, sg, ag, kc, l_common,
            fallback_original_score, cancel_event,
        )

        # KB 추가 제안은 매칭이 전무한 "유사도 낮음" 경로에만 의미가 있다.
        kb = (
            self._try_kb_suggestion(problem_text)
            if verdict == "유사도 낮음" and self.suggest_kb
            else None
        )
        return ReportResult(verdict=verdict, report_md=md, kb_suggestion=kb)

    # ── 판정 ──────────────────────────────────────────────────────────────────

    def _determine_verdict(self, r: MatchResult) -> str:
        """유사도 판정(축 1) — 순수 score 기반, 원본 그대로.

        matched_case 나 fallback 여부를 절대 참조하지 않는다. 케이스 원 판정
        "인용"(축 2)은 이 함수와 완전히 독립이며 프롬프트 빌더에서만 다룬다
        (2026-07-23 정정 — 두 축을 한 값으로 섞으면 안 됨).
        """
        if not r.matched:
            return "유사도 낮음"
        if r.score >= self.definite_threshold:
            return "유사도 높음"
        return "유사도 중간"

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
        matched_case: MatchedCase | None = None,
        fallback_original_score: float | None = None,
    ) -> int:
        """verdict 경로별 고정 데이터(evidence/로그/프롬프트 골격)의 토큰 수 추정.

        D7 — R4/R5/R6(rationale/actions/scope) 프롬프트 섹션도 실제 주입 조건과
        동일한 규칙으로 추정에 반영한다(프롬프트 빌더의 주입 규칙과 항상 같이
        움직여야 함 — 여기서만 어긋나면 truncation 계산이 과소평가된다).
        """
        from core.context_strategy import estimate_tokens
        if verdict == "유사도 낮음":
            log_text = render_lines(l_common[:self.max_log_lines])
            return estimate_tokens(log_text) + 300

        evidence_text   = _fmt_evidence(match_result.matched)
        guidelines_text = _fmt_pattern_guidelines(match_result.matched)
        case_text = ""
        if matched_case is not None and fallback_original_score is None:
            if verdict == "유사도 높음":
                case_text = (
                    _fmt_case_verdict_citation(matched_case)
                    + _fmt_case_scope(matched_case)
                    + _fmt_verdict_rationale(matched_case)
                    + _fmt_case_actions(matched_case)
                )
            elif verdict == "유사도 중간":
                case_text = (
                    _fmt_case_verdict_line(matched_case)
                    + _fmt_verdict_rationale(matched_case)
                )
        return estimate_tokens(evidence_text + guidelines_text + case_text) + 300

    def _apply_context_strategy(
        self,
        system_guidelines: str,
        analysis_guidelines: str,
        knowledge_context: str,
        verdict: str,
        match_result: MatchResult,
        l_common: list[LogLine],
        matched_case: MatchedCase | None = None,
        fallback_original_score: float | None = None,
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

        fixed = self._estimate_fixed_tokens(
            verdict, match_result, l_common, matched_case, fallback_original_score,
        )
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

            fixed = self._estimate_fixed_tokens(
                verdict, match_result, l_common, matched_case, fallback_original_score,
            )

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

            fixed = self._estimate_fixed_tokens(
                verdict, match_result, l_common, matched_case, fallback_original_score,
            )
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
        """verdict(유사도 판정) 별 프롬프트 문자열을 반환한다.

        케이스 원 판정 인용(축 2)은 verdict 분기와 무관하게 각 프롬프트
        빌더 내부에서 fallback_original_score 로만 게이팅한다.
        """
        if verdict == "유사도 높음":
            return _prompt_matched(problem_text, match_result, matched_case, profile_ctx, fallback_original_score)
        if verdict == "유사도 중간":
            return _prompt_uncertain(problem_text, match_result, matched_case, profile_ctx, fallback_original_score)
        # 유사도 낮음
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

    "유사도 중간" 경로에서는 케이스 자체가 확정되지 않았으므로 참고 정보로만
    제시하고, 이 판정을 결론으로 채택하지 않도록 명시한다(C4).
    """
    if case is None or case.case_verdict is None:
        return ""
    label = verdict_label(case.case_verdict)
    return f"참고 케이스의 원 분석 판정 : {label} (일치도가 낮으므로 결론으로 채택하지 말 것)\n"


def _fmt_case_verdict_citation(case: MatchedCase | None) -> str:
    """케이스 원 판정 "인용" 한 줄 — 판정 로직이 아니라 저장된 값을 그대로 옮기는
    것. 유사도 판정(축 1)과 독립이며 이 값 자체가 verdict 를 바꾸지 않는다.

    일치도가 높고(verdict=="유사도 높음") fallback 미채택(귀속 확실)일 때만 호출된다.
    undetermined 면 사유도 같이 인용한다.
    """
    if case is None or case.case_verdict is None:
        return ""
    label = verdict_label(case.case_verdict)
    line = f"케이스 원 판정(인용) : {label}\n"
    if case.case_verdict == "undetermined":
        line += f"판정불가 사유 : {_fmt_undetermined_reason(case)}\n"
    return line


def _fmt_verdict_rationale(case: MatchedCase | None) -> str:
    """원 분석의 판정 근거를 프롬프트 섹션으로 변환한다(R6). 없으면 빈 문자열."""
    if case is None or not case.verdict_rationale.strip():
        return ""
    return f"\n━━━ 원 분석의 판정 근거 ━━━\n{case.verdict_rationale.strip()}\n"


def _fmt_undetermined_reason(case: MatchedCase | None) -> str:
    """판정불가 사유 코드를 사람이 읽는 문구로 변환한다."""
    if case is None:
        return "사유 미기재"
    return undetermined_reason_text(case.undetermined_reason, case.undetermined_reason_note)


def _fmt_case_scope(case: MatchedCase | None) -> str:
    """원 분석이 확정한 문제 범위(증상 발현 영역 / 결함영역 / 특이사항) 섹션(R5).

    셋 다 비어 있으면 빈 문자열. 증상이 보이는 곳과 결함이 있는 곳이 다른
    케이스에서 이 구분이 원인 분석의 출발점이 된다.
    """
    if case is None:
        return ""
    rows: list[str] = []
    if case.symptom_module.strip():
        rows.append(f"- 문제현상 발현 영역: {case.symptom_module.strip()}")
    area = defect_area_text(
        case.defect_area_type, case.defect_area_module, case.defect_area_items
    )
    if area:
        rows.append(f"- 결함영역: {area}")
    if case.notes.strip():
        rows.append(f"- 특이사항: {case.notes.strip()}")
    if not rows:
        return ""
    body = "\n".join(rows)
    return f"\n━━━ 원 분석이 확정한 문제 범위 ━━━\n{body}\n"


def _fmt_case_actions(case: MatchedCase | None) -> str:
    """원 분석의 조치 이력을 프롬프트 섹션으로 변환한다(R4). 조치가 없으면 빈 문자열.

    "유사도 중간" 경로에는 주입하지 않는다 — 케이스가 확정되지 않은 상태에서
    그 케이스의 대응 이력을 제시하면 이번 건의 조치로 오독된다.
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
    """verdict == "유사도 높음" — 유사도 판정(축 1)만으로 도달, score ≥ threshold.

    이 판정 자체는 fallback 여부와 무관하다(유사도는 독립 축). 케이스 원
    판정 "인용"(축 2)만 fallback 으로 게이팅한다 — fallback 채택 시 evidence
    의 출처가 case 자신의 패턴이 아니므로(§4 불변식 1) 케이스 이름조차
    노출하지 않는다. 안 그러면 무관한 패턴 옆에 케이스 정체성만 남아 "제목과
    패턴이 섞여 보이는" 결과가 재발한다(2026-07-23 실사용 재현).
    """
    via_fallback = fallback_original_score is not None

    if via_fallback:
        case_info = (
            f"⚠️ 케이스 검색은 있었으나 케이스 고유 패턴 점수가 낮아"
            f"({fallback_original_score:.0%}) 전체 KB 패턴으로 재검색했습니다.\n"
            "아래 매칭 패턴은 특정 케이스에 귀속되지 않습니다.\n"
        )
        citation = ""
        attribution_notice = (
            "\n매칭 패턴은 전역 재검색 결과이며 특정 케이스에서 유래한 것이"
            " 아닙니다 — 리포트에 케이스명을 언급하거나 특정 케이스와"
            " 연관지어 서술하지 마세요."
        )
    elif case:
        case_info = f"매칭 케이스 : {case.name} (관련성 {case.relevance_score:.0%})\n"
        citation = (
            _fmt_case_verdict_citation(case)
            + _fmt_case_scope(case)
            + _fmt_verdict_rationale(case)
            + _fmt_case_actions(case)
        )
        attribution_notice = ""
    else:
        case_info = ""
        citation = ""
        attribution_notice = ""

    # citation 이 있을 때만 "판정"과 "인용"을 혼동하지 말라는 안내와 출력
    # 섹션을 추가한다 — citation 이 없으면 이 지시·섹션 자체가 없어야, LLM 이
    # 빈 섹션을 지어내거나 흔적을 남길 여지도 없다.
    if citation:
        no_mixup_notice = (
            '\n"케이스 원 판정(인용)"은 별개 정보 — 그 케이스가 과거에 결함/'
            "비결함/판정불가 중 무엇으로 종결됐는지를 그대로 전달하는 것이지, "
            '이번 판정을 바꾸는 게 아닙니다. 인용된 판정이 "비결함"이어도 '
            '"## 판정: 유사도 높음" 자체는 그대로 유효하니, 리포트에서 '
            "이 둘을 같은 의미인 것처럼 섞어 쓰지 마세요."
        )
        citation_heading = "\n## 케이스 원 판정 (참고)"
    else:
        no_mixup_notice = ""
        citation_heading = ""

    profile_section = f"\n{profile_ctx}\n" if profile_ctx else ""
    return f"""/no_think
아래 커널 로그 분석 결과를 바탕으로 Markdown 형식의 진단 리포트를 작성하세요.
{profile_section}
━━━ 분석 요약 ━━━
문제 상황  : {problem_text}
{case_info}일치도    : {r.score:.0%}

━━━ 매칭된 문제 패턴 ━━━
{_fmt_evidence(r.matched)}

━━━ 미매칭 문제 패턴 ━━━
{chr(10).join(f'- {p.name}' for p in r.unmatched) or '  (없음)'}

━━━ 문제 패턴별 분석지침 ━━━
{_fmt_pattern_guidelines(r.matched)}
{citation}
━━━ 출력 형식 ━━━
위 분석지침에 따라 아래 섹션을 포함한 Markdown 리포트를 작성하세요.{attribution_notice}
"판정: 유사도 높음"은 로그가 이 패턴들과 얼마나 일치하는지를 뜻합니다.{no_mixup_notice}
조치 이력이 주어졌다면 "권장 조치" 에서 그 이력을 먼저 밝히세요 — 이미 수정된
건인지, 결함으로 인정하되 보류된 건인지, 다른 주체로 이관된 건인지에 따라
이번에 필요한 조치가 달라집니다. 이미 종결된 대응을 다시 제안하지 마세요.
## 판정: 유사도 높음{citation_heading}
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
    """verdict == "유사도 중간" — 유사도 판정(축 1)만으로 도달, score < threshold.
    fallback 여부와 무관하게 이 verdict 에 이르며, fallback 채택 여부는 오직
    케이스 원 판정 "인용"(축 2)을 넣을지 뺄지에만 쓰인다.

    fallback 채택 시(fallback_original_score is not None)에는 evidence 의
    출처가 matched_case 자신의 패턴이 아니라 전역 재매칭이다(§4 불변식 1).
    이 경로에서는 케이스 판정·근거 텍스트뿐 아니라 **케이스 이름 자체도
    evidence 와 나란히 노출하지 않는다** — 이름만 남아도 LLM 이 "이 패턴들이
    그 케이스 것"처럼 서술해 완화된 형태의 D2(케이스 제목과 무관한 패턴이
    한 리포트에서 섞여 보이는 것)가 재발한다. 사용자가 실사용 중 재현·보고한
    문제(2026-07-23).
    """
    via_fallback = fallback_original_score is not None

    if via_fallback:
        case_info = (
            f"⚠️ 케이스 검색은 있었으나 케이스 고유 패턴 점수가 낮아"
            f"({fallback_original_score:.0%}) 전체 KB 패턴으로 재검색했습니다.\n"
            "아래 매칭 패턴은 특정 케이스에 귀속되지 않습니다.\n"
        )
        verdict_line = ""
        rationale    = ""
        attribution_notice = (
            "\n매칭 패턴은 전역 재검색 결과이며 특정 케이스에서 유래한 것이"
            " 아닙니다 — 리포트에 케이스명을 언급하거나 특정 케이스와"
            " 연관지어 서술하지 마세요."
        )
    elif case:
        case_info = f"참고 케이스 : {case.name} (관련성 {case.relevance_score:.0%})\n"
        verdict_line = _fmt_case_verdict_line(case)
        rationale    = _fmt_verdict_rationale(case)
        attribution_notice = ""
    else:
        case_info = ""
        verdict_line = ""
        rationale    = ""
        attribution_notice = ""

    profile_section = f"\n{profile_ctx}\n" if profile_ctx else ""
    return f"""/no_think
아래 커널 로그 분석 결과를 바탕으로 Markdown 형식의 진단 리포트를 작성하세요.
{profile_section}
━━━ 분석 요약 ━━━
문제 상황  : {problem_text}
{case_info}유사도 중간 사유 : 일치도 낮음 ({r.score:.0%})
{verdict_line}
━━━ 부분 매칭된 문제 패턴 ━━━
{_fmt_evidence(r.matched)}

━━━ 미매칭 문제 패턴 ━━━
{chr(10).join(f'- {p.name}' for p in r.unmatched) or '  (없음)'}

━━━ 문제 패턴별 분석지침 ━━━
{_fmt_pattern_guidelines(r.matched)}
{rationale}
━━━ 출력 형식 ━━━
위 분석지침에 따라 아래 섹션을 포함한 Markdown 리포트를 작성하세요.{attribution_notice}
## 판정: 유사도 중간
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
## 판정: 유사도 낮음 (신규 문제 가능성)
## 관찰된 현상
## 가능한 원인
## 권장 조치
## KB 추가 권고
  (이 문제를 KB 에 등록하면 향후 자동 진단이 가능합니다)
"""
