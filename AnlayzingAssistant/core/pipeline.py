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
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from core.db import DB_PATH, get_conn
from core.config import cfg
from core.master_rule import apply as apply_master_rules, load_rules as load_master_rules
from core.observability import AnalysisLogger
from core.profile import MergedProfile, merge_profiles, search_knowledge_context
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
from core.pattern_matcher import PatternMatcher, MatchResult
from core.report_generator import ReportGenerator, ReportResult

if TYPE_CHECKING:
    pass

_BASE_STAGES   = 7   # Stage 6 미사용 시 단계 수 (Fallback 포함)
_REFLECT_STAGE = 8   # Stage 6 사용 시 단계 수


# ── 결과 타입 ─────────────────────────────────────────────────────────────────

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

    # 알 수 없음 경로에서 생성된 KB 추가 후보
    kb_suggestion: object | None = field(default=None)    # GenerationResult | None

    # Stage 6 Reflection 결과
    reflection_notes: str = field(default="")

    # 이력 저장 후 할당되는 row id
    history_id: int | None = field(default=None)


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
        reflect: bool | None          = None,   # None → cfg.stage6_reflection_enabled
        save_history: bool            = True,
        observability: bool | None    = None,   # None → cfg.observability_enabled
    ) -> None:
        # None 이면 호출 시점의 cfg 값 사용 — import 시 고정되는 기본값 문제 방지
        llm_model          = llm_model          if llm_model          is not None else cfg.llm_model
        kb_threshold       = kb_threshold       if kb_threshold       is not None else cfg.kb_threshold
        definite_threshold = definite_threshold if definite_threshold is not None else cfg.definite_threshold
        max_log_lines      = max_log_lines      if max_log_lines      is not None else cfg.max_log_lines
        reflect            = reflect            if reflect            is not None else cfg.stage6_reflection_enabled
        observability      = observability      if observability      is not None else cfg.observability_enabled

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

        # ── 프로파일 사전정제 키워드 병합 (Stage 1 적용 전) ───────────────────
        if merged_profile and merged_profile.prefilter_keywords:
            existing = list(config.input_keywords or [])
            merged_kws = list(dict.fromkeys(existing + merged_profile.prefilter_keywords))
            import dataclasses as _dc
            config = _dc.replace(config, input_keywords=merged_kws)

        # ── Stage 1 (1-4 파일 선별 → 1-1~1-3 정제) ──────────────────────────
        _notify(1, "Stage 1 — 로그 정제",
                f"{len(raw_logs)}개 파일 선별 및 노이즈 제거 중...")
        raw_total_lines = sum(len(c.splitlines()) for c in raw_logs.values())
        l_common, selected_logs = self._run_stage1(raw_logs, config)
        logger.log("stage1", {
            "raw_files":         list(raw_logs.keys()),
            "raw_lines_total":   raw_total_lines,
            "selected_files":    list(selected_logs.keys()),
            "refined_lines":     len(l_common),
            "applied_keywords":  list(config.input_keywords or []),
            "file_conditions":   list(config.file_conditions or []),
            "anchors":           list(config.anchors or []),
        })

        # ── Master Rule (Stage 1 → Stage 3 사이 전역 정규화) ─────────────────
        _notify(2, "마스터 룰 — 전역 정규화",
                f"정제된 {len(l_common)}줄에 정규화 규칙 적용 중...")
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

        # ── 프로파일 ChromaDB 사전지식 enrichment (problem_text 확보 후 실행) ──
        # Stage 2B(Reranker)와 Stage 5 모두에 주입할 통합 knowledge_context 조립
        knowledge_context = merged_profile.knowledge_context if merged_profile else ""
        if merged_profile and merged_profile.chromadb_knowledge_ids:
            chroma_ctx = search_knowledge_context(
                problem_text  = problem_text,
                knowledge_ids = merged_profile.chromadb_knowledge_ids,
            )
            if chroma_ctx:
                sep = "\n\n" if knowledge_context else ""
                knowledge_context = knowledge_context + sep + chroma_ctx

        # ── Stage 2 ──────────────────────────────────────────────────────────
        # pinned_case_name 이 주어지면 벡터 검색·Reranker 를 건너뛰고 해당
        # 케이스를 그대로 matched_case 로 채택한다. 그렇지 않으면 사용자 프로파일
        # 범위로 자동 검색한다.
        matched_case: MatchedCase | None
        if pinned_case_name:
            _notify(3, "Stage 2 — KB 검색",
                    f"사용자 지정 케이스 '{pinned_case_name}' 로드 중...")
            matched_case = self._kb_search.load_case_by_name(pinned_case_name)
            if matched_case is None:
                # DB 에 해당 이름이 없을 때는 로깅만 남기고 자동 검색으로 폴백
                logger.log("stage2", {
                    "source":               "pinned_not_found",
                    "pinned_case_name":     pinned_case_name,
                    "hit":                  False,
                    "matched_case":         None,
                })
                matched_case = self._kb_search.search(
                    problem_text               = problem_text,
                    knowledge_context          = knowledge_context,
                    system_analysis_guidelines = cfg.system_analysis_guidelines,
                    selected_profile_names     = merged_profile.source_profile_names if merged_profile else None,
                )
                logger.log("stage2", {
                    "source":            "auto_fallback",
                    "query":             problem_text,
                    "selected_profiles": (merged_profile.source_profile_names if merged_profile else []),
                    "threshold":         self._kb_search.threshold,
                    "top_k":             self._kb_search.top_k,
                    "hit":               matched_case is not None,
                    "matched_case":      {
                        "case_id":         matched_case.case_id,
                        "name":            matched_case.name,
                        "relevance_score": matched_case.relevance_score,
                        "keywords":        matched_case.keywords,
                        "profile_refs":    matched_case.profile_refs,
                        "patterns":        [p.get("name") for p in matched_case.patterns],
                    } if matched_case else None,
                    "knowledge_context_chars": len(knowledge_context),
                })
            else:
                logger.log("stage2", {
                    "source":            "pinned",
                    "pinned_case_name":  pinned_case_name,
                    "hit":               True,
                    "matched_case":      {
                        "case_id":         matched_case.case_id,
                        "name":            matched_case.name,
                        "relevance_score": matched_case.relevance_score,
                        "keywords":        matched_case.keywords,
                        "profile_refs":    matched_case.profile_refs,
                        "patterns":        [p.get("name") for p in matched_case.patterns],
                        "pinned":          True,
                    },
                    "knowledge_context_chars": len(knowledge_context),
                })
        else:
            _notify(3, "Stage 2 — KB 검색",
                    "유사 케이스 벡터 검색 및 LLM Reranker 실행 중...")
            matched_case = self._kb_search.search(
                problem_text               = problem_text,
                knowledge_context          = knowledge_context,
                system_analysis_guidelines = cfg.system_analysis_guidelines,
                selected_profile_names     = merged_profile.source_profile_names if merged_profile else None,
            )
            logger.log("stage2", {
                "source":            "auto",
                "query":             problem_text,
                "selected_profiles": (merged_profile.source_profile_names if merged_profile else []),
                "threshold":         self._kb_search.threshold,
                "top_k":             self._kb_search.top_k,
                "hit":               matched_case is not None,
                "matched_case":      {
                    "case_id":         matched_case.case_id,
                    "name":            matched_case.name,
                    "relevance_score": matched_case.relevance_score,
                    "keywords":        matched_case.keywords,
                    "profile_refs":    matched_case.profile_refs,
                    "patterns":        [p.get("name") for p in matched_case.patterns],
                } if matched_case else None,
                "knowledge_context_chars": len(knowledge_context),
            })

        # ── Stage 2 HIT: 케이스 추천 프로파일 자동 병합 ──────────────────────
        if matched_case and matched_case.profile_refs:
            case_merged = merge_profiles(matched_case.profile_refs, self.db_path)
            merged_profile = _combine_merged_profiles(merged_profile, case_merged)

            # 케이스 프로파일의 ChromaDB 사전지식 enrichment (Stage 5 에 반영)
            if case_merged.chromadb_knowledge_ids:
                existing_ids = set(
                    merged_profile.chromadb_knowledge_ids
                    if merged_profile else []
                )
                new_ids = [i for i in case_merged.chromadb_knowledge_ids
                           if i not in existing_ids]
                if new_ids:
                    chroma_ctx = search_knowledge_context(
                        problem_text  = problem_text,
                        knowledge_ids = new_ids,
                    )
                    if chroma_ctx:
                        sep = "\n\n" if knowledge_context else ""
                        knowledge_context = knowledge_context + sep + chroma_ctx

        # ── Stage 3 ──────────────────────────────────────────────────────────
        _notify(4, "Stage 3 — 로그 재정제",
                f"{'케이스' if matched_case else '전체 패턴'} 기반 로그 재필터링 중...")
        refined_entries = self._run_stage3(l_normalized, matched_case)
        logger.log("stage3", {
            "path":              "HIT" if matched_case else "MISS",
            "source_case":       matched_case.name if matched_case else None,
            "keywords_used":     (matched_case.keywords if matched_case else None),
            "lines_before":      len(l_normalized),
            "entries_after":     len(refined_entries),
            "patterns_targeted": [e.pattern.get("name") for e in refined_entries] if refined_entries else [],
        })

        # ── Stage 4 ──────────────────────────────────────────────────────────
        _notify(5, "Stage 4 — 패턴 매칭",
                "문제 패턴 매칭 및 점수 산출 중...")
        if refined_entries:
            match_result = self._matcher.match_entries(refined_entries)
        else:
            # 매칭할 패턴이 없음 → MatchResult 빈 상태 생성
            from core.pattern_matcher import MatchResult as _MR
            match_result = _MR(matched=[], unmatched=[], score=0.0)
        logger.log("stage4", {
            "score":             match_result.score,
            "matched":           [
                {
                    "name":          r.name,
                    "type":          r.type,
                    "weight":        r.weight,
                    "evidence_cnt":  len(r.evidence),
                }
                for r in match_result.matched
            ],
            "unmatched":         [
                {"name": r.name, "type": r.type, "weight": r.weight}
                for r in match_result.unmatched
            ],
        })

        # ── Stage 3/4 Fallback (HIT이지만 score < definite_threshold) ────────
        # 케이스 패턴만으로 점수가 부족한 경우 전체 패턴으로 재시도한다.
        # matched_case는 유지하여 Stage 5 리포트에 HIT 사실을 포함시킨다.
        fallback_original_score: float | None = None
        if matched_case and match_result.score < self._reporter.definite_threshold:
            fallback_original_score = match_result.score
            _notify(6, "Stage 3/4 — Fallback",
                    f"케이스 패턴 점수 낮음({match_result.score:.0%}) — 전체 패턴으로 재시도 중...")
            fallback_entries = self._run_stage3(l_normalized, None)  # MISS 경로
            if fallback_entries:
                fallback_result = self._matcher.match_entries(fallback_entries)
                if fallback_result.score >= match_result.score:
                    refined_entries = fallback_entries
                    match_result    = fallback_result
                    logger.log("fallback", {
                        "triggered":           True,
                        "original_score":      fallback_original_score,
                        "threshold":           self._reporter.definite_threshold,
                        "fallback_score":      fallback_result.score,
                        "adopted":             True,
                        "fallback_entries":    len(fallback_entries),
                    })
                else:
                    logger.log("fallback", {
                        "triggered":           True,
                        "original_score":      fallback_original_score,
                        "threshold":           self._reporter.definite_threshold,
                        "fallback_score":      fallback_result.score,
                        "adopted":             False,
                        "fallback_entries":    len(fallback_entries),
                    })
                    fallback_original_score = None  # fallback 결과가 더 나빠 원래 유지, 표시 안 함
            else:
                logger.log("fallback", {
                    "triggered":        True,
                    "original_score":   fallback_original_score,
                    "threshold":        self._reporter.definite_threshold,
                    "fallback_entries": 0,
                    "adopted":          False,
                })
                fallback_original_score = None

        # ── Stage 5 ──────────────────────────────────────────────────────────
        _notify(7, "Stage 5 — 리포트 생성",
                "LLM이 진단 리포트를 작성 중입니다...")
        report = self._reporter.generate(
            problem_text               = problem_text,
            l_common                   = l_common,
            match_result               = match_result,
            matched_case               = matched_case,
            analysis_guidelines        = merged_profile.analysis_guidelines if merged_profile else "",
            knowledge_context          = knowledge_context,
            system_analysis_guidelines = cfg.system_analysis_guidelines,
            fallback_original_score    = fallback_original_score,
        )
        logger.log("stage5", {
            "verdict":                 report.verdict,
            "report_chars":            len(report.report_md),
            "report_md":               report.report_md,
            "analysis_guidelines":     merged_profile.analysis_guidelines if merged_profile else "",
            "knowledge_context_chars": len(knowledge_context),
            "kb_suggestion":           (report.kb_suggestion is not None),
        })

        # ── Stage 6 ──────────────────────────────────────────────────────────
        if self._reflector is not None:
            _notify(8, "Stage 6 — Reflection",
                    "LLM이 리포트를 자기 검증하는 중...")
            try:
                reflection = self._reflector.reflect(
                    report_md                  = report.report_md,
                    verdict                    = report.verdict,
                    score                      = match_result.score if match_result else 0.0,
                    match_result               = match_result,
                    l_common                   = l_common,
                    system_analysis_guidelines = cfg.system_analysis_guidelines,
                )
                final_report_md    = reflection.report_final
                reflection_notes   = reflection.notes
                logger.log("stage6", {
                    "enabled":       True,
                    "error":         False,
                    "notes":         reflection_notes,
                    "final_chars":   len(final_report_md),
                    "changed":       (final_report_md != report.report_md),
                })
            except Exception as _e:
                final_report_md  = report.report_md
                reflection_notes = "(Stage 6 오류 — Stage 5 리포트 원본 사용)"
                logger.log("stage6", {
                    "enabled":       True,
                    "error":         True,
                    "error_message": str(_e),
                })
        else:
            final_report_md  = report.report_md
            reflection_notes = ""
            logger.log("stage6", {"enabled": False})

        result = PipelineResult(
            verdict          = report.verdict,
            report_md        = final_report_md,
            l_common         = l_common,
            l_normalized     = l_normalized,
            selected_logs    = selected_logs,
            matched_case     = matched_case,
            refined_entries  = refined_entries,
            match_result     = match_result,
            kb_suggestion    = report.kb_suggestion,
            reflection_notes = reflection_notes,
        )

        # ── 이력 저장 ─────────────────────────────────────────────────────────
        if self.save_history:
            result.history_id = self._save_history(
                problem_text = problem_text,
                raw_logs     = raw_logs,
                result       = result,
            )

        # ── Stage별 상세 로그 flush (Observability) ──────────────────────────
        logger.flush(history_id=result.history_id)

        return result

    # ── Stage 1 헬퍼 ──────────────────────────────────────────────────────────

    def _run_stage1(
        self,
        raw_logs: dict[str, str],
        config: RefineConfig,
    ) -> tuple[list[LogLine], dict[str, str]]:
        """
        1-4 파일 선별 → 1-1~1-3 정제 순으로 실행한 뒤
        하나의 L_common 으로 합친다.

        Returns
        -------
        (l_common, selected_logs)
          l_common      : 정제된 로그 라인 목록 (타임스탬프 순 정렬)
          selected_logs : 1-4 선별 후 실제 처리된 파일 딕셔너리 (UI 표시용)
        """
        # 0단계: 키워드 전처리 필터 (파일 선별 + 라인 추출)
        if config.input_keywords:
            raw_logs = prefilter_by_keywords(raw_logs, config.input_keywords)

        # 1-4: 파일 선별 조건 (AND)
        selected_logs = self._refiner.select_files(raw_logs, config.file_conditions)

        # 1-1 → 1-2 → 1-3: 선별된 파일만 정제
        all_lines: list[LogLine] = []
        for content in selected_logs.values():
            lines = self._refiner.refine(content, config)
            all_lines.extend(lines)

        # 타임스탬프 기준 안정 정렬 (없는 경우 float('inf') 로 끝에 배치)
        all_lines.sort(key=lambda ll: ll.timestamp if ll.timestamp is not None else float("inf"))
        return all_lines, selected_logs

    # ── Stage 3 헬퍼 ──────────────────────────────────────────────────────────

    def _run_stage3(
        self,
        l_common: list[LogLine],
        matched_case: MatchedCase | None,
    ) -> list[RefinedEntry]:
        """HIT/MISS 에 따라 Stage 3 를 분기한다."""
        if matched_case is not None:
            # HIT: 케이스 keywords 로 L_common 필터
            return refine_for_case(l_common, matched_case)

        # MISS: DB 에 있는 모든 패턴으로 시도
        patterns = self._load_all_patterns()
        if not patterns:
            return []
        return refine_for_patterns(l_common, patterns)

    # ── DB 헬퍼 ───────────────────────────────────────────────────────────────

    def _load_all_patterns(self) -> list[dict]:
        """
        SQLite 에서 모든 패턴을 로드한다 (MISS 경로에서 사용).
        SEQUENCE 의 steps, COMPOSITE 의 components 이름도 포함한다.
        """
        with get_conn(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM patterns ORDER BY id"
            ).fetchall()

            result: list[dict] = []
            for row in rows:
                d = dict(row)
                d["keywords"] = json.loads(d["keywords"])
                pid = d["id"]

                if d["type"] == "SEQUENCE":
                    d["steps"] = [
                        s["pattern"]
                        for s in conn.execute(
                            "SELECT pattern FROM pattern_steps "
                            "WHERE pattern_id = ? ORDER BY step_order",
                            (pid,),
                        ).fetchall()
                    ]

                if d["type"] == "COMPOSITE":
                    d["components"] = [
                        c["name"]
                        for c in conn.execute(
                            """
                            SELECT p2.name
                            FROM pattern_components pc
                            JOIN patterns p2 ON pc.ref_pattern_id = p2.id
                            WHERE pc.pattern_id = ?
                            ORDER BY pc.component_order
                            """,
                            (pid,),
                        ).fetchall()
                    ]

                result.append(d)
        return result

    def _save_history(
        self,
        problem_text: str,
        raw_logs: dict[str, str],
        result: PipelineResult,
    ) -> int | None:
        """분석 결과를 history 테이블에 저장한다."""
        # 입력 해시: 문제 설명 + 로그 파일 내용 전체를 합쳐서 SHA256
        combined = problem_text + "".join(raw_logs.values())
        input_hash = hashlib.sha256(combined.encode(errors="replace")).hexdigest()

        payload: dict = {
            "verdict":       result.verdict,
            "problem_text":  problem_text,
            "score":         result.match_result.score if result.match_result else 0.0,
            "matched_case":  result.matched_case.name if result.matched_case else None,
            "matched_patterns": [
                {"name": r.name, "type": r.type, "weight": r.weight}
                for r in (result.match_result.matched if result.match_result else [])
            ],
            "report_md":     result.report_md,
        }

        try:
            with get_conn(self.db_path) as conn:
                cur = conn.execute(
                    "INSERT INTO history (input_hash, result) VALUES (?, ?)",
                    (input_hash, json.dumps(payload, ensure_ascii=False)),
                )
                return cur.lastrowid
        except Exception:
            return None


# ── 헬퍼 ──────────────────────────────────────────────────────────────────────

def _combine_merged_profiles(
    base: MergedProfile | None,
    additional: MergedProfile,
) -> MergedProfile:
    """
    두 MergedProfile 을 병합한다. base 가 우선, additional 이 그 뒤에 추가된다.

    사용처:
      Stage 2 HIT 후 케이스 추천 프로파일(additional)을
      사용자 선택 프로파일(base)에 합산할 때 호출.
    """
    if base is None:
        return additional

    # 분석 지침: 사용자 프로파일(base) 우선, 케이스 추천(additional) 후순위
    ag_parts = [p for p in (base.analysis_guidelines, additional.analysis_guidelines) if p.strip()]
    merged_ag = "\n\n".join(ag_parts)

    # 사전정제 키워드: 합집합 (순서 유지, base 우선)
    seen: set[str] = set(base.prefilter_keywords)
    merged_kws = list(base.prefilter_keywords)
    for kw in additional.prefilter_keywords:
        if kw not in seen:
            seen.add(kw)
            merged_kws.append(kw)

    # 사전지식 컨텍스트: 연결 (base 우선)
    kc_parts = [p for p in (base.knowledge_context, additional.knowledge_context) if p.strip()]
    merged_kc = "\n\n".join(kc_parts)

    # ChromaDB ID: 합집합 (순서 유지)
    seen_ids: set[int] = set(base.chromadb_knowledge_ids)
    merged_ids = list(base.chromadb_knowledge_ids)
    for kid in additional.chromadb_knowledge_ids:
        if kid not in seen_ids:
            seen_ids.add(kid)
            merged_ids.append(kid)

    # 원본 프로파일 이름: 합집합 (순서 유지)
    seen_names: set[str] = set(base.source_profile_names)
    merged_names = list(base.source_profile_names)
    for n in additional.source_profile_names:
        if n not in seen_names:
            seen_names.add(n)
            merged_names.append(n)

    return MergedProfile(
        analysis_guidelines    = merged_ag,
        prefilter_keywords     = merged_kws,
        knowledge_context      = merged_kc,
        chromadb_knowledge_ids = merged_ids,
        source_profile_names   = merged_names,
    )
