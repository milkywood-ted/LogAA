"""
api/worker.py

ThreadPoolExecutor 기반 Pipeline 실행기.

- 서버 시작 시 싱글턴 Pipeline 인스턴스를 초기화한다.
- submit_job()으로 분석 작업을 thread pool에 제출한다.
- on_progress 콜백을 통해 진행 상황을 job_store에 기록한다.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from core.db import DB_PATH, init_db
from core.log_loader import load_inputs
from core.log_refiner import RefineConfig
from core.parser_registry import ParserRegistry, DEFAULT_ACTIVE_PARSERS
from core.pattern_seeder import seed
from core.pipeline import Pipeline
from core.profile import merge_profiles

from api.job_store import (
    init_jobs_table,
    create_job,
    update_job_running,
    update_job_done,
    update_job_error,
)
from api.models import AnalyzeRequest

logger = logging.getLogger(__name__)

# ── 설정 ──────────────────────────────────────────────────────────────────────

# 동시 실행 worker 수. LLM 병렬 처리 가능 수에 맞게 조정.
MAX_WORKERS = 10

# ── 싱글턴 ────────────────────────────────────────────────────────────────────

_pipeline: Pipeline | None = None
_executor: ThreadPoolExecutor | None = None


def startup(db_path: Path = DB_PATH) -> None:
    """
    FastAPI lifespan startup 시 호출.
    DB 초기화, 패턴 시드, Pipeline/Executor 생성.
    """
    global _pipeline, _executor

    # DB 초기화
    init_db(db_path)
    init_jobs_table(db_path)

    # 기본 패턴 시드
    yaml_path = Path(__file__).parent.parent / "config" / "patterns" / "default_patterns.yaml"
    if yaml_path.exists():
        seed(yaml_path, db_path)

    # Pipeline 싱글턴 (무거운 모델 로드는 최초 1회)
    _pipeline = Pipeline(db_path=db_path)

    # ThreadPoolExecutor
    _executor = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="logaa-worker")

    logger.info(f"Worker 시작: max_workers={MAX_WORKERS}")


def shutdown() -> None:
    """FastAPI lifespan shutdown 시 호출."""
    global _executor
    if _executor:
        _executor.shutdown(wait=True)
        logger.info("Worker 종료 완료")


def get_executor() -> ThreadPoolExecutor:
    if _executor is None:
        raise RuntimeError("Worker가 초기화되지 않았습니다. startup()을 먼저 호출하세요.")
    return _executor


def get_active_job_count() -> int:
    """현재 실행 중인 future 수를 반환한다."""
    if _executor is None:
        return 0
    return len([f for f in _executor._threads if f.is_alive()])


# ── Job 제출 ──────────────────────────────────────────────────────────────────

def submit_job(req: AnalyzeRequest, db_path: Path = DB_PATH) -> str:
    """
    분석 작업을 thread pool에 제출하고 job_id를 반환한다.

    Parameters
    ----------
    req : AnalyzeRequest
    db_path : SQLite DB 경로

    Returns
    -------
    job_id : str
    """
    job_id = create_job(db_path)
    get_executor().submit(_run_job, job_id, req, db_path)
    return job_id


# ── 실행 ──────────────────────────────────────────────────────────────────────

def _run_job(job_id: str, req: AnalyzeRequest, db_path: Path) -> None:
    """Thread에서 실행되는 실제 파이프라인 실행 함수."""
    try:
        # 1. 로그 파일 로드
        update_job_running(job_id, "로그 파일 로드 중...", 5, db_path)
        raw_logs = load_inputs(req.log_paths, recursive=req.recursive)

        if not raw_logs:
            update_job_error(job_id, "로그 파일을 찾을 수 없습니다: " + str(req.log_paths), db_path)
            return

        # 2. 프로파일 병합
        merged_profile = None
        if req.profile_names:
            merged_profile = merge_profiles(req.profile_names, db_path)

        # 3. RefineConfig 조립 (파서 + 키워드 + anchor)
        refine_config = _build_refine_config(req)

        # 4. progress 콜백 정의
        def on_progress(step: int, total: int, name: str, detail: str) -> None:
            progress = int((step / total) * 90) + 5  # 5~95% 구간 사용
            update_job_running(job_id, f"{name} — {detail}", progress, db_path)

        # 5. Pipeline 실행
        assert _pipeline is not None
        result = _pipeline.run(
            problem_text=req.problem_text,
            raw_logs=raw_logs,
            config=refine_config,
            merged_profile=merged_profile,
            on_progress=on_progress,
            pinned_case_name=req.pinned_case_name,
        )

        # 6. 결과 직렬화
        update_job_done(job_id, _serialize_result(result), db_path)

    except Exception as e:
        logger.exception(f"Job {job_id} 실행 중 오류")
        update_job_error(job_id, str(e), db_path)


def _build_refine_config(req: AnalyzeRequest) -> RefineConfig:
    """
    AnalyzeRequest 에서 RefineConfig 를 조립한다.

    파서 선택:
    - req.parser_names 가 비어 있으면 log_parsers.yaml 의 default=true 파서만 사용
    - 값이 있으면 해당 이름의 파서만 사용
    """
    _PARSERS_YAML = Path(__file__).parent.parent / "config" / "log_parsers.yaml"

    selected_parsers: list = []
    if _PARSERS_YAML.exists():
        try:
            registry = ParserRegistry.from_yaml(_PARSERS_YAML)
            if req.parser_names:
                selected_parsers = [
                    p for p in registry.parsers
                    if p.name in req.parser_names
                ]
            else:
                # default=true 파서만
                selected_parsers = [p for p in registry.parsers if getattr(p, "default", False)]
        except Exception:
            logger.warning("log_parsers.yaml 로드 실패 — 기본 파서 사용")

    return RefineConfig(
        active_parsers=[p.name for p in selected_parsers] if selected_parsers else list(DEFAULT_ACTIVE_PARSERS),
        input_keywords=req.input_keywords or None,
        anchors=req.anchors or None,
    )


def _serialize_result(result) -> dict:
    """PipelineResult를 JSON 직렬화 가능한 dict로 변환한다."""
    matched_case = None
    if result.matched_case:
        matched_case = {
            "case_id": result.matched_case.case_id,
            "name": result.matched_case.name,
            "relevance_score": result.matched_case.relevance_score,
            "keywords": result.matched_case.keywords,
        }

    match_result = None
    if result.match_result:
        match_result = {
            "score": result.match_result.score,
            "matched": [
                {
                    "name": r.name,
                    "type": r.type,
                    "weight": r.weight,
                    "evidence_count": len(r.evidence),
                }
                for r in result.match_result.matched
            ],
            "unmatched": [
                {"name": r.name, "type": r.type, "weight": r.weight}
                for r in result.match_result.unmatched
            ],
        }

    return {
        "verdict": result.verdict,
        "report_md": result.report_md,
        "matched_case": matched_case,
        "match_result": match_result,
        "reflection_notes": result.reflection_notes,
        "history_id": result.history_id,
        "selected_logs": list(result.selected_logs.keys()),
    }