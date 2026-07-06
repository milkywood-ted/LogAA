"""
core/pipeline.py

Stage 1 → 2 → 3 → 4 → 5 통합 파이프라인

사용 예시:
    pipeline = Pipeline()
    result   = pipeline.run(
        problem_text = "ATA 드라이브 타임아웃 반복 발생",
        raw_logs     = {"dmesg.txt": "<log content>"},
        config       = RefineConfig(anchors=["ata.*error"]),
    )
    print(result.report_md)
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

from core.db import DB_PATH, get_conn
import core.config as config
import core.config as cfg_module   # config 파라미터에 가려지지 않는 모듈 참조용 별칭
from core.master_rule import apply as apply_master_rules, load_rules as load_master_rules
from core.observability import AnalysisLogger
from core.profile import MergedProfile, merge_profiles
from core.knowledge import (
    search_knowledge_context,
    get_all_chromadb_knowledge_ids,
    filter_knowledge_ids,
)
from core.llm import embed
from core.log_refiner import (
    LogLine,
    LogRefiner,
    RefineConfig,
    RefinedEntry,
    refine_for_case,
    refine_for_patterns,
    prefilter_by_keywords,
)
from core.kb_search import KBSearch, MatchedCase
from core.chip_filter import filter_patterns_by_chip
from core.pattern_matcher import PatternMatcher, MatchResult
from core.report_generator import ReportGenerator, ReportResult

# progress 단계 분모.
#   step 1 = Stage 1 정제, step 2 = Master Rule, step 3 = Stage 2 KB,
#   step 4 = Stage 3/4 매칭, step 5 = Fallback(조건부), step 6 = Stage 5 리포트,
#   step 7 = Stage 6 Reflection(조건부).
# Fallback 은 HIT 이지만 score 가 부족할 때만 발생하므로 미발생 시 step 5 가 빈다.
_BASE_STAGES   = 6   # Stage 6 미사용 시 분모 (Fallback 미발생 흐름 기준)
_REFLECT_STAGE = 7   # Stage 6 사용 시 분모


# ── 결과 타입 ─────────────────────────────────────────────────────────────────

@dataclass
class MinorityReport:
    """메인 결과 이외의 후보 케이스 패턴 매칭 요약 — Stage 5 LLM 호출 없이 생성."""
    matched_case: MatchedCase
    match_result: MatchResult
    source_profile_names: list[str] = field(default_factory=list)
    knowledge_similarity: float | None = None


@dataclass
class _Candidate:
    """MoE 앙상블 후보 풀의 단일 항목 (pipeline 내부 전용 — 직렬화 대상 아님).

    각 후보는 자신을 발견한 전문가(프로파일)의 증강 컨텍스트를 함께 들고 있어
    winner 채택 시 올바른 컨텍스트로 Stage 5 를 실행할 수 있다.
    """
    matched_case: MatchedCase
    expert_profile_names: list[str]                 # 이 케이스를 발견한 전문가(들)
    merged_profile_after: MergedProfile | None      # 그 전문가 run 의 증강 프로파일
    knowledge_context_after: str                    # 그 전문가 run 의 증강 knowledge_context
    router_score: float | None                      # 라우터 유사도 점수 (Seed 는 None 가능)
    match_result: MatchResult | None = None         # Stage 3/4 후 채워짐 (§4)
    refined_entries: list[RefinedEntry] = field(default_factory=list)  # winner Stage 5 입력용
    # 전문가별 Stage 1 정제 로그 (moe_per_expert_stage1 경로에서 채워짐).
    # winner 채택 시 Stage 3/4·5/6 을 이 전문가의 정제 로그로 실행한다.
    l_common_after: list[LogLine] = field(default_factory=list)
    l_normalized_after: list[LogLine] = field(default_factory=list)


@dataclass
class PipelineResult:
    """파이프라인 전체 실행 결과."""

    # Stage 5 최종 출력
    verdict: str        # "문제" | "불확실" | "알 수 없음"
    report_md: str      # Markdown 리포트

    # 각 Stage 중간 결과 (UI·디버깅용)
    l_common: list[LogLine]             = field(default_factory=list)
    l_normalized: list[LogLine]         = field(default_factory=list)
    selected_logs: dict[str, str]       = field(default_factory=dict)   # 1-4 선별 후 파일 목록
    matched_case: MatchedCase | None    = field(default=None)
    refined_entries: list[RefinedEntry] = field(default_factory=list)
    match_result: MatchResult | None    = field(default=None)

    # 복수 케이스 후보 중 메인 외 나머지
    minority_reports: list[MinorityReport] = field(default_factory=list)

    # winner 를 발견한 전문가 프로파일 이름 목록 (ensemble 경로에서만 채워짐)
    winner_profile_names: list[str] = field(default_factory=list)

    # 알 수 없음 경로에서 생성된 KB 추가 후보
    kb_suggestion: object | None = field(default=None)    # GenerationResult | None

    # Stage 6 Reflection 결과
    reflection_notes: str = field(default="")

    # 이력 저장 후 할당되는 row id
    history_id: int | None = field(default=None)

    # 사용자에게 전달할 경고 메시지 목록 (앵커 미발견 등)
    warnings: list[str] = field(default_factory=list)


# ── Pipeline ──────────────────────────────────────────────────────────────────

class Pipeline:
    """
    Stage 1 ~ 5 를 순서대로 실행하는 통합 실행기.

    Streamlit 에서 st.cache_resource 로 싱글턴으로 사용한다.

        @st.cache_resource
        def get_pipeline():
            return Pipeline()

    모델·임계값 등을 커스터마이즈할 때:

        pipeline = Pipeline(
            llm_model        = "qwen3:14b",
            kb_threshold     = 0.70,
            definite_threshold = 0.50,
        )
    """

    def __init__(
        self,
        llm_model: str | None         = None,
        db_path: Path                 = DB_PATH,
        kb_top_k: int                 = 5,
        kb_threshold: float | None    = None,
        definite_threshold: float | None = None,
        max_log_lines: int | None     = None,
        suggest_kb: bool              = True,
        reflect: bool | None          = None,   # None → config.get_bool("pipeline.stage6_reflection_enabled")
        save_history: bool            = True,
        observability: bool | None    = None,   # None → config.get_bool("pipeline.observability_enabled")
    ) -> None:
        # None 이면 호출 시점의 config 값 사용 — import 시 고정되는 기본값 문제 방지
        llm_model          = llm_model          if llm_model          is not None else config.active_llm().get("model", "")
        kb_threshold       = kb_threshold       if kb_threshold       is not None else config.get_float("pipeline.kb_threshold", 0.70)
        definite_threshold = definite_threshold if definite_threshold is not None else config.get_float("pipeline.definite_threshold", 0.50)
        max_log_lines      = max_log_lines      if max_log_lines      is not None else config.get_int("pipeline.max_log_lines", 200)
        reflect            = reflect            if reflect            is not None else config.get_bool("pipeline.stage6_reflection_enabled", True)
        observability      = observability      if observability      is not None else config.get_bool("pipeline.observability_enabled", False)

        self.db_path            = db_path
        self.save_history       = save_history
        self._reflect_enabled   = reflect
        self._obs_enabled       = observability

        # Stage 2: KB 검색 (BGE-M3 + Qwen3 Reranker) — 무거우므로 여기서 한 번만 로드
        self._kb_search = KBSearch(
            llm_model  = llm_model,
            db_path    = db_path,
            top_k      = kb_top_k,
            threshold  = kb_threshold,
        )

        # Stage 4
        self._matcher = PatternMatcher()

        # Stage 5
        self._reporter = ReportGenerator(
            model              = llm_model,
            db_path            = db_path,
            definite_threshold = definite_threshold,
            max_log_lines      = max_log_lines,
            suggest_kb         = suggest_kb,
        )

        # Stage 6 (선택)
        if self._reflect_enabled:
            from core.reflection import Reflector
            self._reflector: Reflector | None = Reflector(model=llm_model)
        else:
            self._reflector = None

        # Stage 1
        self._refiner = LogRefiner()

    # ── 공개 API ──────────────────────────────────────────────────────────────

    def run(
        self,
        problem_text: str,
        raw_logs: dict[str, str],
        config: RefineConfig | None = None,
        merged_profile: MergedProfile | None = None,
        on_progress: Callable[[int, int, str, str], None] | None = None,
        pinned_case_name: str | None = None,
        cancel_event: threading.Event | None = None,
        chip: list[str] | str | None = None,
        defect_id: str | None = None,
    ) -> PipelineResult:
        """
        파이프라인 전체를 실행한다.

        Parameters
        ----------
        problem_text     : 사용자가 입력한 문제 설명 (Stage 2, 5 에 사용)
        raw_logs         : {파일명: 내용} 딕셔너리  (log_loader 출력을 그대로 전달)
        config           : Stage 1 설정. None 이면 기본값 사용.
        merged_profile   : 병합된 분석 프로파일. None 이면 프로파일 미적용.
        pinned_case_name : 사용자가 직접 지정한 케이스 이름.
                           주어지면 Stage 2 벡터 검색·Reranker 를 건너뛰고
                           해당 케이스를 그대로 matched_case 로 사용한다.
                           이름이 DB 에 없으면 자동 검색으로 폴백.
        chip             : defect 의 칩 정보. Stage 3/4 패턴을 chip_tags 기준으로
                           필터링한다 (HIT·MISS 양 경로). 비어있으면 필터링하지 않는다.

        Returns
        -------
        PipelineResult
        """
        _total_stages = _REFLECT_STAGE if self._reflect_enabled else _BASE_STAGES

        def _notify(step: int, name: str, detail: str = "") -> None:
            if on_progress is not None:
                on_progress(step, _total_stages, name, detail)

        logger = AnalysisLogger(enabled=self._obs_enabled, db_path=self.db_path)

        if config is None:
            config = RefineConfig()

        # ── per-expert Stage 1 분기 판정 ─────────────────────────────────────
        # ensemble/first_hit + moe_per_expert_stage1 ON 일 때만 전문가별 정제 경로.
        # 이 경우 메인 Stage 1 은 프로파일 prefilter 없이(base_config) 돌려 라우팅과
        # 전역 sentinel 전문가용 로그(정보량 최대)를 만든다. 프로파일 prefilter 는
        # _run_ensemble 안에서 전문가별로 적용한다.
        mode = cfg_module.get_str("pipeline.moe_traversal_mode", "single")
        per_expert = self._should_per_expert(mode, pinned_case_name)

        base_config = config   # 프로파일 prefilter 병합 전 원본 (전문가별 정제 기준)

        # ── 사전정제 키워드 병합 ────────────────────────────────────────────
        # per-expert 경로에서는 메인 정제에 프로파일 키워드를 적용하지 않는다.
        effective_config = base_config if per_expert \
            else self._apply_profile_keywords(base_config, merged_profile)

        # ── Stage 1 ─────────────────────────────────────────────────────────
        _notify(1, "Stage 1 — 로그 정제",
                f"{len(raw_logs)}개 파일 선별 및 노이즈 제거 중...")
        raw_total_lines = sum(len(c.splitlines()) for c in raw_logs.values())
        l_common, selected_logs, stage1_warnings = self._run_stage1(raw_logs, effective_config)
        logger.log("stage1", {
            "raw_files":         list(raw_logs.keys()),
            "raw_lines_total":   raw_total_lines,
            "selected_files":    list(selected_logs.keys()),
            "refined_lines":     len(l_common),
            "applied_keywords":  list(effective_config.input_keywords or []),
            "file_conditions":   list(effective_config.file_conditions or []),
            "anchors":           list(effective_config.anchors or []),
            "warnings":          stage1_warnings,
            "per_expert":        per_expert,
        })

        # ── Master Rule ─────────────────────────────────────────────────────
        _notify(2, "마스터 룰 — 전역 정규화",
                f"정제된 {len(l_common)}줄에 정규화 규칙 적용 중...")
        l_normalized = self._run_master_rule(l_common, logger)

        # ── 초기 ChromaDB 사전지식 enrichment ───────────────────────────────
        # Stage 2B(Reranker)와 Stage 5 모두에 주입할 통합 knowledge_context 조립.
        knowledge_context = self._enrich_initial_knowledge(
            problem_text, merged_profile, stage1_warnings, chip=chip,
        )

        # ── Stage 2 + 3/4 (single 경로 또는 MoE 라우터 앙상블 경로) ──────────
        # single/pinned: 현행 _run_stage2 → _run_stage3_4.
        # ensemble/first_hit: 라우터 → 앙상블 후보 풀 → 풀 전역에서 winner 선정.
        # 어느 경로든 winner 의 증강 컨텍스트(merged_profile/knowledge_context)를
        # downstream(fallback→Stage 5)에 연결한다 (§2-3).
        (matched_case, refined_entries, match_result, minority_reports,
         merged_profile, knowledge_context, winner_profile_names,
         l_common, l_normalized) = self._search_and_match(
            problem_text       = problem_text,
            knowledge_context  = knowledge_context,
            merged_profile     = merged_profile,
            pinned_case_name   = pinned_case_name,
            l_common           = l_common,
            l_normalized       = l_normalized,
            notify             = _notify,
            logger             = logger,
            chip               = chip,
            raw_logs           = raw_logs,
            base_config        = base_config,
            per_expert         = per_expert,
        )

        # ── Stage 3/4 Fallback ──────────────────────────────────────────────
        refined_entries, match_result, fallback_original_score = self._run_fallback(
            matched_case        = matched_case,
            match_result        = match_result,
            refined_entries     = refined_entries,
            l_normalized        = l_normalized,
            warnings            = stage1_warnings,
            notify              = _notify,
            logger              = logger,
            chip                = chip,
        )

        # ── Stage 5 ─────────────────────────────────────────────────────────
        _notify(6, "Stage 5 — 리포트 생성",
                "LLM이 진단 리포트를 작성 중입니다...")
        report = self._run_stage5(
            problem_text            = problem_text,
            l_common                = l_common,
            match_result            = match_result,
            matched_case            = matched_case,
            merged_profile          = merged_profile,
            knowledge_context       = knowledge_context,
            fallback_original_score = fallback_original_score,
            cancel_event            = cancel_event,
            logger                  = logger,
        )

        # ── Stage 6 ─────────────────────────────────────────────────────────
        final_report_md, reflection_notes = self._run_stage6(
            report       = report,
            match_result = match_result,
            l_common     = l_common,
            cancel_event = cancel_event,
            notify       = _notify,
            logger       = logger,
        )

        final_report_md = self._append_source_summary(final_report_md, match_result)

        result = PipelineResult(
            verdict               = report.verdict,
            report_md             = final_report_md,
            l_common              = l_common,
            l_normalized          = l_normalized,
            selected_logs         = selected_logs,
            matched_case          = matched_case,
            refined_entries       = refined_entries,
            match_result          = match_result,
            minority_reports      = minority_reports,
            winner_profile_names  = winner_profile_names,
            kb_suggestion         = report.kb_suggestion,
            reflection_notes      = reflection_notes,
            warnings              = stage1_warnings,
        )

        # ── 이력 저장 + observability flush ────────────────────────────────
        self._finalize_run(result, problem_text, raw_logs, logger, defect_id=defect_id)

        return result

    # ── run() 단계별 헬퍼 ─────────────────────────────────────────────────────

    def _apply_profile_keywords(
        self,
        config: RefineConfig,
        merged_profile: MergedProfile | None,
    ) -> RefineConfig:
        """프로파일 사전정제 키워드를 RefineConfig 에 병합한다 (Stage 1 적용 전)."""
        if not (merged_profile and merged_profile.prefilter_keywords):
            return config
        existing = list(config.input_keywords or [])
        merged_kws = list(dict.fromkeys(existing + merged_profile.prefilter_keywords))
        return replace(config, input_keywords=merged_kws)

    def _should_per_expert(self, mode: str, pinned_case_name: str | None) -> bool:
        """전문가별 Stage 1 정제 경로를 탈지 판정한다.

        ensemble/first_hit 모드 + moe_per_expert_stage1 플래그 ON + pinned 아님일 때만.
        single/pinned 은 현행 전역 1회 정제 경로를 유지한다 (동작 불변).
        """
        if pinned_case_name or mode == "single":
            return False
        return cfg_module.get_bool("pipeline.moe_per_expert_stage1", False)

    def _refine_for_expert(
        self,
        raw_logs: dict[str, str],
        base_config: RefineConfig,
        prof: MergedProfile | None,
        logger: AnalysisLogger,
        expert_name: str | None,
    ) -> tuple[list[LogLine], list[LogLine]]:
        """전문가 1명의 prefilter 로 Stage 1 + Master Rule 을 재실행한다 (설계 §3-1).

        prof=None(전역 sentinel)이면 prefilter 미적용 → 호출부에서 라우팅 로그 재사용.
        Stage 1/Master Rule 은 순수 규칙 기반이라 LLM 비용 0, CPU 만 추가된다.
        """
        cfg = self._apply_profile_keywords(base_config, prof)
        l_common, _selected, _warnings = self._run_stage1(raw_logs, cfg)
        l_normalized = self._run_master_rule(l_common, logger)
        logger.log("stage1_expert", {
            "expert":           expert_name,
            "applied_keywords": list(cfg.input_keywords or []),
            "refined_lines":    len(l_common),
            "normalized_lines": len(l_normalized),
        })
        return l_common, l_normalized

    def _run_master_rule(
        self,
        l_common: list[LogLine],
        logger: AnalysisLogger,
    ) -> list[LogLine]:
        """Master Rule 을 적용하고 결과를 observability 에 기록한다."""
        master_rules = load_master_rules(self.db_path)
        l_normalized = apply_master_rules(l_common, master_rules)
        logger.log("master_rule", {
            "rules":             [
                {"name": r.get("name"), "type": r.get("rule_type"), "pattern": r.get("pattern")}
                for r in master_rules
            ],
            "lines_before":      len(l_common),
            "lines_after":       len(l_normalized),
        })
        return l_normalized

    def _append_knowledge(
        self,
        base: str,
        problem_text: str,
        knowledge_ids: list[int],
    ) -> str:
        """`knowledge_ids` 로 ChromaDB 검색 결과를 `base` 에 이어 붙인다.

        빈 ID 목록이거나 검색 결과가 비면 base 를 그대로 반환.
        base 가 비었으면 검색 결과만 반환, 아니면 두 줄 띄움으로 결합한다.
        """
        if not knowledge_ids:
            return base
        ctx = search_knowledge_context(
            problem_text  = problem_text,
            knowledge_ids = knowledge_ids,
        )
        if not ctx:
            return base
        sep = "\n\n" if base else ""
        return base + sep + ctx

    def _enrich_initial_knowledge(
        self,
        problem_text: str,
        merged_profile: MergedProfile | None,
        warnings: list[str],
        chip=None,
    ) -> str:
        """Stage 2 직전에 사용할 초기 knowledge_context 를 조립한다.

        - 프로파일 선택 시: merge_profiles 단계에서 이미 category/chip 필터 완료 →
          프로파일의 기본 knowledge_context + 그 ChromaDB ID 검색 결과.
        - 프로파일 미선택 시: 전체 ChromaDB 사전지식을 chip으로만 필터 후 검색 결과 + warnings 안내.
        """
        if merged_profile:
            base = merged_profile.knowledge_context
            return self._append_knowledge(
                base, problem_text, list(merged_profile.chromadb_knowledge_ids),
            )

        all_chromadb_ids = get_all_chromadb_knowledge_ids(self.db_path)
        if not all_chromadb_ids:
            return ""
        # 프로파일 미선택 경로: chip만 필터 (category 없음 → 전체 통과)
        filtered_ids = filter_knowledge_ids(all_chromadb_ids, [], chip, self.db_path)
        enriched = self._append_knowledge("", problem_text, filtered_ids)
        if enriched:
            warnings.append(
                "프로파일이 선택되지 않아 전체 사전지식을 대상으로 검색하였습니다. "
                "프로파일을 선택하면 해당 도메인에 최적화된 사전지식만 사용됩니다."
            )
        return enriched

    # ── MoE 라우터 앙상블 (Phase 4/5) ─────────────────────────────────────────

    def _search_and_match(
        self,
        problem_text: str,
        knowledge_context: str,
        merged_profile: MergedProfile | None,
        pinned_case_name: str | None,
        l_common: list[LogLine],
        l_normalized: list[LogLine],
        notify: Callable[[int, str, str], None],
        logger: AnalysisLogger,
        chip: list[str] | str | None = None,
        raw_logs: dict[str, str] | None = None,
        base_config: RefineConfig | None = None,
        per_expert: bool = False,
    ) -> tuple[MatchedCase | None, list[RefinedEntry], MatchResult,
               list[MinorityReport], MergedProfile | None, str, list[str],
               list[LogLine], list[LogLine]]:
        """Stage 2(KB 검색) + Stage 3/4(패턴 매칭)를 모드에 따라 실행한다.

        - pinned_case_name 지정: 라우팅·앙상블 전면 우회 → 현행 단일 경로 (§2-2)
        - mode == "single"      : 현행 단일 경로 (구 off 동작)
        - mode in {ensemble, first_hit}: 라우터 → 앙상블 후보 풀 → 풀 전역 winner 선정

        per_expert=True(ensemble + moe_per_expert_stage1) 이면 입력 l_common/l_normalized
        는 "프로파일 prefilter 미적용 전역 정제(=라우팅·전역 sentinel 용)" 이며,
        _run_ensemble 안에서 전문가별 Stage 1 을 재실행한다.

        Returns
        -------
        (matched_case, refined_entries, match_result, minority_reports,
         merged_profile_after, knowledge_context_after, winner_profile_names,
         l_common_after, l_normalized_after)
        winner 의 증강 컨텍스트·로그를 함께 반환해 downstream(fallback→Stage 5/6)에 연결한다.
        """
        mode = config.get_str("pipeline.moe_traversal_mode", "single")

        # ── single / pinned: 현행 단일 경로 ───────────────────────────────────
        if pinned_case_name or mode == "single":
            matched_cases, merged_profile, knowledge_context = self._run_stage2(
                problem_text      = problem_text,
                knowledge_context = knowledge_context,
                merged_profile    = merged_profile,
                pinned_case_name  = pinned_case_name,
                notify            = notify,
                logger            = logger,
                chip              = chip,
            )
            notify(4, "Stage 3 — 로그 재정제",
                   f"{'케이스' if matched_cases else '전체 패턴'} 기반 로그 재필터링 중...")
            matched_case, refined_entries, match_result, minority_reports = \
                self._run_stage3_4(matched_cases, l_normalized, logger, chip)
            winner_profile_names = list(merged_profile.source_profile_names) if merged_profile else []
            return (matched_case, refined_entries, match_result,
                    minority_reports, merged_profile, knowledge_context,
                    winner_profile_names, l_common, l_normalized)

        # ── MoE 라우터 앙상블 경로 ─────────────────────────────────────────────
        N     = config.get_int("pipeline.moe_window_size", 3)
        floor = config.get_float("pipeline.moe_relevance_floor", 0.4)

        # problem 임베딩 1회 계산 (모든 전문가 Stage 2 벡터검색에 재사용)
        problem_vec = embed([problem_text])[0] if problem_text.strip() else None

        # 라우팅은 prefilter 미적용 전역 정제(l_normalized) 1회로 고정한다 (설계 §3-1).
        seed_names = list(merged_profile.source_profile_names) if merged_profile else []
        active = self._route_experts(
            problem_vec, l_normalized, seed_names, floor, N, chip,
        )

        logger.log("routing", {
            "mode":           mode,
            "seed_profiles":  seed_names,
            "active_experts": [{"name": n, "score": s} for n, s in active],
            "floor":          floor,
            "window_size":    N,
            "per_expert":     per_expert,
        })

        pool = self._run_ensemble(
            active            = active,
            problem_text      = problem_text,
            problem_vec       = problem_vec,
            base_knowledge    = knowledge_context,
            mode              = mode,
            notify            = notify,
            logger            = logger,
            chip              = chip,
            per_expert        = per_expert,
            raw_logs          = raw_logs,
            base_config       = base_config,
            l_routing_common  = l_common,
            l_routing_normalized = l_normalized,
        )

        notify(4, "Stage 3 — 로그 재정제",
               f"{'앙상블 후보' if pool else '전체 패턴'} 기반 로그 재필터링 중...")
        return self._select_from_pool(
            pool, l_common, l_normalized, merged_profile, knowledge_context, logger, chip,
        )

    def _select_from_pool(
        self,
        pool: list[_Candidate],
        l_common: list[LogLine],
        l_normalized: list[LogLine],
        base_merged_profile: MergedProfile | None,
        base_knowledge_context: str,
        logger: AnalysisLogger,
        chip: list[str] | str | None = None,
    ) -> tuple[MatchedCase | None, list[RefinedEntry], MatchResult,
               list[MinorityReport], MergedProfile | None, str, list[str],
               list[LogLine], list[LogLine]]:
        """앙상블 후보 풀에서 Stage 3/4 패턴 매칭 → winner 선정 + minority 분리 (§4).

        - 각 후보를 Stage 3/4 패턴 매칭하여 _Candidate.match_result / refined_entries 채움.
          Stage3 입력은 c.l_normalized_after(per-expert 정제, 빈 경우 인자 l_normalized 폴백).
        - case_id dedup: 동일 case_id 중복 시 패턴매칭 점수 높은 쪽 유지, 발견 전문가 병합.
        - winner = (match_result.score, relevance_score, router_score) 내림차순 1위.
        - winner 의 per-expert 컨텍스트·로그를 downstream 으로 반환 (§2-1, 2-3, 설계 §3-2).
        - 빈 풀(전체 MISS): 현행 MISS 경로 (matched_cases=[] 로 _run_stage3_4 호출, §2-6).

        Returns
        -------
        9-tuple: (matched_case, refined_entries, match_result, minority_reports,
                  merged_profile_after, knowledge_context_after, winner_profile_names,
                  l_common_after, l_normalized_after)
        """
        # 빈 풀 → MISS 경로 (동작 불변): base 컨텍스트·로그 유지
        if not pool:
            matched_case, refined_entries, match_result, minority_reports = \
                self._run_stage3_4([], l_normalized, logger, chip)
            return (matched_case, refined_entries, match_result,
                    minority_reports, base_merged_profile, base_knowledge_context, [],
                    l_common, l_normalized)

        # 각 후보 Stage 3/4 패턴 매칭
        # per-expert 정제 로그가 있으면 그것으로, 없으면 전역 l_normalized 폴백.
        for c in pool:
            l_for_stage3 = c.l_normalized_after if c.l_normalized_after else l_normalized
            entries = self._run_stage3(l_for_stage3, c.matched_case)
            c.refined_entries = entries
            c.match_result = (
                self._matcher.match_entries(entries) if entries
                else MatchResult(matched=[], unmatched=[], score=0.0)
            )

        # case_id dedup: 동일 케이스를 여러 전문가가 찾으면 패턴매칭 점수 높은 쪽 유지,
        # 발견 전문가 이름은 합친다.
        by_case: dict[int, _Candidate] = {}
        for c in pool:
            cid = c.matched_case.case_id
            existing = by_case.get(cid)
            if existing is None:
                by_case[cid] = c
            else:
                # 발견 전문가 병합 (순서 유지, 중복 제거)
                for name in c.expert_profile_names:
                    if name not in existing.expert_profile_names:
                        existing.expert_profile_names.append(name)
                # 더 높은 패턴매칭 점수의 후보로 교체 (컨텍스트도 그쪽으로)
                if (c.match_result.score, c.matched_case.relevance_score) > \
                   (existing.match_result.score, existing.matched_case.relevance_score):
                    c.expert_profile_names = existing.expert_profile_names
                    by_case[cid] = c

        deduped = list(by_case.values())

        # winner 선정: (stage4_score, relevance_score, router_score) 내림차순
        def _sort_key(c: _Candidate):
            return (
                c.match_result.score,
                c.matched_case.relevance_score,
                c.router_score if c.router_score is not None else -1.0,
            )
        deduped.sort(key=_sort_key, reverse=True)

        winner = deduped[0]
        logger.log("stage3", {
            "path":              "HIT" if winner.matched_case else "MISS",
            "source_case":       winner.matched_case.name,
            "keywords_used":     winner.matched_case.keywords,
            "lines_before":      len(l_normalized),
            "entries_after":     len(winner.refined_entries),
            "patterns_targeted": [e.pattern.get("name") for e in winner.refined_entries],
            "ensemble_winner_experts": winner.expert_profile_names,
        })
        logger.log("stage4", {
            "score":     winner.match_result.score,
            "matched":   [{"name": r.name, "type": r.type, "weight": r.weight, "evidence_cnt": len(r.evidence)} for r in winner.match_result.matched],
            "unmatched": [{"name": r.name, "type": r.type, "weight": r.weight} for r in winner.match_result.unmatched],
        })

        # minority: winner 제외 나머지 (score > 0 인 것만), 발견 전문가·라우터 점수 기록
        minority_reports = [
            MinorityReport(
                matched_case         = c.matched_case,
                match_result         = c.match_result,
                source_profile_names = list(c.expert_profile_names),
                knowledge_similarity = c.router_score,
            )
            for c in deduped[1:]
            if c.match_result.score > 0
        ]

        # winner 의 per-expert 정제 로그 — 없으면 전역 로그 폴백
        l_c_after = winner.l_common_after if winner.l_common_after else l_common
        l_n_after = winner.l_normalized_after if winner.l_normalized_after else l_normalized

        return (winner.matched_case, winner.refined_entries, winner.match_result,
                minority_reports, winner.merged_profile_after, winner.knowledge_context_after,
                list(winner.expert_profile_names), l_c_after, l_n_after)

    def _route_experts(
        self,
        problem_vec: list[float] | None,
        l_normalized: list[LogLine],
        seed_names: list[str],
        floor: float,
        n: int,
        chip: list[str] | str | None = None,
    ) -> list[tuple[str | None, float | None]]:
        """라우터(gating) v2: 케이스/패턴에서 활성 전문가 집합을 역산한다 (설계 §5-A).

        v1(프로파일 사전지식 유사도 기반)은 정답 프로파일을 놓쳐 single 보다 나빠지는
        구조적 결함이 있었다(hist=97). v2 는 "케이스를 먼저 보고 프로파일을 역산"한다.

        두 축에서 활성 프로파일을 모은다:
          - 상황 축: 전역 케이스 벡터 검색 → similarity ≥ floor 케이스의 profile_refs.
                     케이스 유사도(1-distance)를 라우터 점수로 보존.
          - 원인 축: 전역 패턴 keyword 사전필터(L_normalized 매치) → 그 패턴이 연결된
                     케이스의 profile_refs. (유사도 신호 없음 → score=None)

        활성 집합 = {Seed 각각} ∪ (상황∪원인 후보 중 상위 N) ∪ {전역 전문가(필요 시)}
          - Seed 는 무조건 활성 (score None), N 카운트와 별개.
          - profile_refs 가 빈 케이스가 지목되면 "전역 전문가" sentinel (None, None) 추가
            — 프로파일 미지정 전역 검색(현행 single 미지정 경로)을 전문가로 편입.

        Returns
        -------
        list[(profile_name | None, router_score | None)]
          name=None 은 전역 전문가(프로파일 미지정 전체 케이스 검색) sentinel.
        """
        seed_set = set(seed_names)
        active: list[tuple[str | None, float | None]] = [(nm, None) for nm in seed_names]

        # ── 상황 축: 전역 케이스 벡터 검색 ───────────────────────────────────
        situ_score: dict[str, float] = {}      # profile → 최고 케이스 유사도
        include_global = False
        if problem_vec is not None:
            cases = self._kb_search._vector_search(
                "", allowed_case_ids=None, problem_vec=problem_vec,
            )
            for c in cases:
                sim = 1.0 - c["distance"]
                if sim < floor:
                    continue
                refs = self._kb_search._load_case_profile_refs(c["case_id"])
                if not refs:
                    include_global = True       # 프로파일 무소속 케이스 → 전역 전문가
                    continue
                for r in refs:
                    if r in seed_set:
                        continue
                    if r not in situ_score or sim > situ_score[r]:
                        situ_score[r] = sim

        # ── 원인 축: 전역 패턴 사전필터 → 케이스 → 프로파일 ──────────────────
        cause_set, cause_global = self._cause_axis_profiles(l_normalized, chip)
        cause_set -= seed_set
        include_global = include_global or cause_global

        # ── 활성 후보 정렬: 양 축 동시 지목 우선, 그다음 상황 유사도 ─────────
        candidate_names = set(situ_score) | cause_set

        def _key(nm: str) -> tuple[int, float]:
            in_both = (nm in situ_score) and (nm in cause_set)
            return (1 if in_both else 0, situ_score.get(nm, -1.0))

        ranked = sorted(candidate_names, key=_key, reverse=True)
        for nm in ranked[:n]:
            active.append((nm, situ_score.get(nm)))   # 원인 축 only → None

        if include_global:
            active.append((None, None))

        return active

    def _cause_axis_profiles(
        self,
        l_normalized: list[LogLine],
        chip: list[str] | str | None = None,
    ) -> tuple[set[str], bool]:
        """원인 축: keyword 가 L_normalized 에 매치되는 패턴이 연결된 케이스의 프로파일.

        Returns
        -------
        (profiles, has_global)
          profiles   : 매치 패턴이 연결된 케이스들의 profile_refs 합집합.
          has_global : profile_refs 가 빈 케이스가 포함되면 True (전역 전문가 필요).
        """
        patterns = self._load_all_patterns(chip)
        if not patterns:
            return set(), False

        # refine_for_patterns 는 keyword 가 1줄 이상 매치된 패턴만 entry 로 반환한다.
        entries = refine_for_patterns(l_normalized, patterns)
        matched_ids = [
            e.pattern.get("id") for e in entries if e.pattern.get("id") is not None
        ]
        if not matched_ids:
            return set(), False

        with get_conn(self.db_path) as conn:
            placeholders = ",".join("?" * len(matched_ids))
            rows = conn.execute(
                f"SELECT DISTINCT case_id FROM case_patterns "
                f"WHERE pattern_id IN ({placeholders})",
                matched_ids,
            ).fetchall()

        profiles: set[str] = set()
        has_global = False
        for row in rows:
            refs = self._kb_search._load_case_profile_refs(row["case_id"])
            if not refs:
                has_global = True
            else:
                profiles.update(refs)
        return profiles, has_global

    def _run_ensemble(
        self,
        active: list[tuple[str, float | None]],
        problem_text: str,
        problem_vec: list[float] | None,
        base_knowledge: str,
        mode: str,
        notify: Callable[[int, str, str], None],
        logger: AnalysisLogger,
        chip: list[str] | str | None = None,
        per_expert: bool = False,
        raw_logs: dict[str, str] | None = None,
        base_config: "RefineConfig | None" = None,
        l_routing_common: "list[LogLine] | None" = None,
        l_routing_normalized: "list[LogLine] | None" = None,
    ) -> list[_Candidate]:
        """활성 전문가 각각 Stage 2 실행 → 후보 풀(_Candidate 리스트) 누적 (설계 §3).

        - 활성 전문가 0개: 프로파일 미지정 전역 1회 검색 (현행 미지정 동작 보존).
        - 각 전문가는 merge_profiles([name]) 로 단일 전문가로 다룬다 (Seed 도 라우터도 동일).
        - first_hit 모드: 유사도 순(active 순서) 실행 중 첫 HIT 에서 중단.
        - per_expert=True(moe_per_expert_stage1 ON): 전문가별 Stage 1 재실행 후
          _Candidate.l_*_after 에 적재. global sentinel 은 l_routing_* 재사용.
        """
        pool: list[_Candidate] = []
        total = len(active) if active else 1

        # 활성 전문가가 없으면 프로파일 미지정 전역 1회 검색 (problem_vec 재사용)
        if not active:
            notify(3, "Stage 2 — KB 검색", "전역 검색 (활성 전문가 없음)")
            matched, mp_after, kc_after = self._run_stage2(
                problem_text      = problem_text,
                knowledge_context = base_knowledge,
                merged_profile    = None,
                pinned_case_name  = None,
                notify            = lambda *a, **k: None,   # 서브 notify 억제
                logger            = logger,
                chip              = chip,
                problem_vec       = problem_vec,
            )
            logger.log("ensemble_step", {
                "k":             0,
                "total":         1,
                "expert":        None,
                "router_score":  None,
                "hit":           len(matched) > 0,
                "matched_count": len(matched),
                "global_search": True,
            })
            # 활성 전문가 없음 = 전역 sentinel 과 동일 → 라우팅 로그 직접 재사용
            l_c_after = l_routing_common if l_routing_common is not None else []
            l_n_after = l_routing_normalized if l_routing_normalized is not None else []
            for mc in matched:
                pool.append(_Candidate(
                    matched_case            = mc,
                    expert_profile_names    = [],
                    merged_profile_after    = mp_after,
                    knowledge_context_after = kc_after,
                    router_score            = None,
                    l_common_after          = l_c_after,
                    l_normalized_after      = l_n_after,
                ))
            return pool

        for k, (name, score) in enumerate(active):
            # name=None 은 전역 전문가 sentinel (프로파일 미지정 전체 케이스 검색).
            is_global = name is None
            notify(3, "Stage 2 — KB 검색",
                   f"전문가 {k + 1}/{total} — {name if name else '전역'}")
            if is_global:
                prof = None
                expert_knowledge = base_knowledge
            else:
                prof = merge_profiles([name], self.db_path, chip=chip)
                # 전문가 사전지식을 base_knowledge 위에 enrich
                expert_knowledge = self._enrich_initial_knowledge(
                    problem_text, prof, [], chip=chip,
                )

            # per-expert Stage 1 정제 (설계 §3-1 / §3-3)
            if per_expert and raw_logs is not None and base_config is not None:
                if is_global:
                    # 전역 sentinel: prefilter 없음 → 라우팅 로그 직접 재사용
                    l_c_after = l_routing_common if l_routing_common is not None else []
                    l_n_after = l_routing_normalized if l_routing_normalized is not None else []
                else:
                    l_c_after, l_n_after = self._refine_for_expert(
                        raw_logs, base_config, prof, logger, name,
                    )
            else:
                l_c_after = []
                l_n_after = []

            matched, mp_after, kc_after = self._run_stage2(
                problem_text      = problem_text,
                knowledge_context = expert_knowledge,
                merged_profile    = prof,
                pinned_case_name  = None,
                notify            = lambda *a, **k: None,   # 서브 notify 억제
                logger            = logger,
                chip              = chip,
                problem_vec       = problem_vec,
            )
            logger.log("ensemble_step", {
                "k":             k,
                "total":         total,
                "expert":        name,
                "router_score":  score,
                "hit":           len(matched) > 0,
                "matched_count": len(matched),
                "global_search": is_global,
                "first_hit_break": (mode == "first_hit" and len(matched) > 0),
            })
            for mc in matched:
                pool.append(_Candidate(
                    matched_case            = mc,
                    expert_profile_names    = [] if is_global else [name],
                    merged_profile_after    = mp_after,
                    knowledge_context_after = kc_after,
                    router_score            = score,
                    l_common_after          = l_c_after,
                    l_normalized_after      = l_n_after,
                ))
            if mode == "first_hit" and matched:
                break

        return pool
