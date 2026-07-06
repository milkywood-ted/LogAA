"""
api/worker.py

ThreadPoolExecutor 기반 Pipeline 실행기.

- 서버 시작 시 싱글턴 Pipeline 인스턴스를 초기화한다.
- submit_job()으로 분석 작업을 thread pool에 제출한다.
- on_progress 콜백을 통해 진행 상황을 job_store에 기록한다.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import core.config as config
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
    update_job_cancelled,
    purge_old_jobs,
    mark_zombies_cancelled,
)
from api.models import AnalyzeRequest

logger = logging.getLogger(__name__)

# ── 싱글턴 ────────────────────────────────────────────────────────────────────

_pipeline: Pipeline | None = None
_executor: ThreadPoolExecutor | None = None
_cleanup_thread: threading.Thread | None = None
_cleanup_stop: threading.Event = threading.Event()

# job_id → threading.Event  (set 시 해당 job 취소)
_cancel_events: dict[str, threading.Event] = {}


def startup(db_path: Path = DB_PATH) -> None:
    """
    FastAPI lifespan startup 시 호출.
    DB 초기화, 패턴 시드, Pipeline/Executor 생성.
    """
    global _pipeline, _executor, _cleanup_thread, _cleanup_stop

    # DB 초기화
    init_db(db_path)
    init_jobs_table(db_path)

    # 1번: startup 시 정리
    #   - 좀비(pending/running) → cancelled 로 마킹 (클라이언트가 상태를 알 수 있게)
    #   - 완료(done/error/cancelled) → ttl_minutes=0 즉시 삭제
    zombies = mark_zombies_cancelled(db_path)
    if zombies:
        logger.info("startup: 좀비 job %d건을 cancelled 로 마킹", zombies)
    purged = purge_old_jobs(ttl_minutes=0, db_path=db_path)
    if purged:
        logger.info("startup: 완료 job %d건 정리 완료", purged)

    # 기본 패턴 시드
    seed(db_path=db_path)

    # Pipeline 싱글턴 (무거운 모델 로드는 최초 1회)
    _pipeline = Pipeline(db_path=db_path)

    # ThreadPoolExecutor
    _executor = ThreadPoolExecutor(max_workers=config.get_int("server.max_workers", 10), thread_name_prefix="logaa-worker")

    # 2번: 주기적 job 정리 background thread
    _cleanup_stop.clear()
    _cleanup_thread = threading.Thread(
        target=_cleanup_loop,
        args=(db_path,),
        daemon=True,
        name="logaa-job-cleanup",
    )
    _cleanup_thread.start()

    logger.info(
        "Worker 시작: max_workers=%d, job_ttl=%d분, cleanup_interval=%d초",
        config.get_int("server.max_workers", 10),
        config.get_int("server.job_ttl_minutes", 60),
        config.get_int("server.job_cleanup_interval", 300),
    )


def shutdown() -> None:
    """FastAPI lifespan shutdown 시 호출."""
    global _executor
    _cleanup_stop.set()
    if _executor:
        _executor.shutdown(wait=True)
        logger.info("Worker 종료 완료")


def _cleanup_loop(db_path: Path) -> None:
    """주기적으로 TTL 초과 완료 job을 삭제하는 background loop."""
    while not _cleanup_stop.wait(timeout=config.get_int("server.job_cleanup_interval", 300)):
        try:
            ttl_minutes = config.get_int("server.job_ttl_minutes", 60)
            purged = purge_old_jobs(ttl_minutes=ttl_minutes, db_path=db_path)
            if purged:
                logger.debug("job 정리: %d건 삭제 (TTL=%d분)", purged, ttl_minutes)
        except Exception:
            logger.exception("job 정리 중 오류")


def get_executor() -> ThreadPoolExecutor:
    if _executor is None:
        raise RuntimeError("Worker가 초기화되지 않았습니다. startup()을 먼저 호출하세요.")
    return _executor


# ── Job 제출 ──────────────────────────────────────────────────────────────────

def cancel_job(job_id: str, db_path: Path = DB_PATH) -> bool:
    """
    실행 중인 job에 취소 신호를 보낸다.

    Returns
    -------
    True  : 취소 신호 전달 성공 (pending/running 상태였음)
    False : 해당 job이 없거나 이미 완료/오류 상태
    """
    event = _cancel_events.get(job_id)
    if event is None:
        return False
    event.set()
    return True


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
    cancel_event = threading.Event()
    _cancel_events[job_id] = cancel_event
    get_executor().submit(_run_job, job_id, req, db_path, cancel_event)
    return job_id


# ── 실행 ──────────────────────────────────────────────────────────────────────

class _JobCancelledError(Exception):
    """취소 신호 수신 시 on_progress 콜백에서 raise 하는 내부 예외."""


def _run_job(job_id: str, req: AnalyzeRequest, db_path: Path, cancel_event: threading.Event) -> None:
    """Thread에서 실행되는 실제 파이프라인 실행 함수."""
    try:
        # 1. 로그 파일 로드
        update_job_running(job_id, "로그 파일 로드 중...", 5, db_path)
        raw_logs = load_inputs(req.log_paths, recursive=req.recursive)
        raw_logs = _relativize_paths(raw_logs, req.log_path_base)

        if not raw_logs:
            update_job_error(job_id, "로그 파일을 찾을 수 없습니다: " + str(req.log_paths), db_path)
            return

        # 2. 프로파일 병합
        merged_profile = None
        if req.profile_names:
            merged_profile = merge_profiles(req.profile_names, db_path, chip=req.chip)

        # 3. RefineConfig 조립 (파서 + 키워드 + anchor)
        refine_config = _build_refine_config(req)

        # 4. progress 콜백 정의 (취소 체크 포함)
        def on_progress(step: int, total: int, name: str, detail: str) -> None:
            if cancel_event.is_set():
                raise _JobCancelledError()
            progress = int((step / total) * 90) + 5  # 5~95% 구간 사용
            update_job_running(job_id, f"{name} — {detail}", progress, db_path)

        # 5. Pipeline 실행
        if _pipeline is None:
            raise RuntimeError(
                "Pipeline 이 초기화되지 않았습니다. startup() 호출 여부를 확인하세요."
            )
        result = _pipeline.run(
            problem_text=req.problem_text,
            raw_logs=raw_logs,
            config=refine_config,
            merged_profile=merged_profile,
            on_progress=on_progress,
            pinned_case_name=req.pinned_case_name,
            cancel_event=cancel_event,
            chip=req.chip,
            defect_id=req.defect_id,
        )

        # 6. 결과 직렬화
        update_job_done(job_id, _serialize_result(result), db_path)

    except (_JobCancelledError, InterruptedError):
        logger.info("Job %s 취소됨", job_id)
        update_job_cancelled(job_id, db_path)
    except Exception as e:
        logger.exception("Job %s 실행 중 오류", job_id)
        update_job_error(job_id, str(e), db_path)
    finally:
        _cancel_events.pop(job_id, None)


def _build_refine_config(req: AnalyzeRequest) -> RefineConfig:
    """
    AnalyzeRequest 에서 RefineConfig 를 조립한다.

    파서 선택:
    - req.parser_names 가 비어 있으면 log_parsers.yaml 의 default=true 파서만 사용
    - 값이 있으면 해당 이름의 파서만 사용
    """
    selected_parsers: list = []
    try:
        registry = ParserRegistry.from_yaml(None)
        if req.parser_names:
            selected_parsers = [
                p for p in registry.parsers
                if p.name in req.parser_names
            ]
        else:
            selected_parsers = [p for p in registry.parsers if getattr(p, "default", False)]
    except Exception:
        logger.warning("log_parsers.yaml 로드 실패 — 기본 파서 사용")

    return RefineConfig(
        active_parsers=[p.name for p in selected_parsers] if selected_parsers else list(DEFAULT_ACTIVE_PARSERS),
        input_keywords=list(req.input_keywords),
        anchors=list(req.anchors),
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
            "chip_tags": result.matched_case.chip_tags,
            "references": result.matched_case.references,
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

    minority_reports = [
        {
            "matched_case": {
                "case_id": mr.matched_case.case_id,
                "name": mr.matched_case.name,
                "relevance_score": mr.matched_case.relevance_score,
                "chip_tags": mr.matched_case.chip_tags,
            },
            "match_result": {
                "score": mr.match_result.score,
                "matched": [
                    {
                        "name": r.name,
                        "type": r.type,
                        "weight": r.weight,
                        "evidence_count": len(r.evidence),
                    }
                    for r in mr.match_result.matched
                ],
                "unmatched": [
                    {"name": r.name, "type": r.type, "weight": r.weight}
                    for r in mr.match_result.unmatched
                ],
            },
            "source_profile_names": mr.source_profile_names,
            "knowledge_similarity": mr.knowledge_similarity,
        }
        for mr in result.minority_reports
    ]

    return {
        "verdict": result.verdict,
        "report_md": result.report_md,
        "matched_case": matched_case,
        "match_result": match_result,
        "reflection_notes": result.reflection_notes,
        "history_id": result.history_id,
        "selected_logs": list(result.selected_logs.keys()),
        "warnings": result.warnings,
        "minority_reports": minority_reports,
        "winner_profile_names": result.winner_profile_names,
        "traversal_mode": config.get_str("pipeline.moe_traversal_mode", "single"),
    }


def _relativize_paths(raw_logs: dict[str, str], log_path_base: str | None) -> dict[str, str]:
    """
    절대경로 키를 log_path_base 기준 상대경로로 변환한다.

    log_path_base 가 주어지면 해당 경로를 잘라내어 그 하위 상대경로만 남긴다.
    (예: base=/ws, path=/ws/DF260512-00254/dmesg.log → DF260512-00254/dmesg.log)
    log_path_base 가 없거나 매칭되지 않으면 원본 경로를 유지한다.
    """
    from pathlib import Path as _Path

    if not log_path_base:
        return raw_logs

    base = str(_Path(log_path_base).resolve()).rstrip("/") + "/"

    result: dict[str, str] = {}
    for abs_path, content in raw_logs.items():
        rel = abs_path[len(base):] if abs_path.startswith(base) else abs_path
        result[rel] = content
    return result
