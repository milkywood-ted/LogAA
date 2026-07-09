"""
api/main.py

FastAPI 앱 및 엔드포인트.

실행:
    uvicorn api.main:app --host 0.0.0.0 --port 8000

"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Security, status

from api.auth import verify_api_key
from api.job_store import count_jobs_by_status, get_job
from api.models import AnalyzeRequest, HealthResponse, JobStatusResponse, SubmitResponse
from api.router import settings as settings_router
from api.worker import get_active_job_count, shutdown, startup, submit_job

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    startup()
    yield
    shutdown()


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="LogAA Analysis API",
    description="커널 로그 분석 파이프라인 API",
    version="1.0.0",
    lifespan=lifespan,
)

router = APIRouter(dependencies=[Security(verify_api_key)])


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post(
    "/analyze",
    response_model=SubmitResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="로그 분석 요청",
)
def analyze(req: AnalyzeRequest) -> SubmitResponse:
    """
    분석 작업을 큐에 등록하고 job_id를 즉시 반환한다.

    - `log_paths`: 서버 로컬 경로 (파일 또는 폴더)
    - `profile_names`: 적용할 분석 프로파일 이름 목록 (선택)
    - `pinned_case_name`: 직접 지정할 KB 케이스 이름 (선택)
    """
    job_id = submit_job(req)
    return SubmitResponse(job_id=job_id, status="pending")


@router.get(
    "/analyze/{job_id}",
    response_model=JobStatusResponse,
    summary="분석 결과 조회",
)
def get_analyze_result(job_id: str) -> JobStatusResponse:
    """
    job_id로 분석 상태 및 결과를 조회한다.

    - `status`: pending | running | done | error
    - `stage`: 현재 진행 중인 stage 설명 (running 상태)
    - `progress`: 0~100
    - `result`: 분석 완료 시 결과 데이터
    - `error`: 오류 발생 시 메시지
    """
    job = get_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"job_id '{job_id}' 를 찾을 수 없습니다.",
        )

    return JobStatusResponse(
        job_id=job["job_id"],
        status=job["status"],
        stage=job.get("stage", ""),
        progress=job.get("progress", 0),
        result=job.get("result"),
        error=job.get("error"),
    )


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="서버 상태 확인",
)
def health() -> HealthResponse:
    """서버 및 worker 상태를 반환한다."""
    from api.worker import MAX_WORKERS

    counts = count_jobs_by_status()
    return HealthResponse(
        status="ok",
        worker_threads=MAX_WORKERS,
        active_jobs=counts.get("running", 0),
        queued_jobs=counts.get("pending", 0),
    )


# ── Router 등록 ───────────────────────────────────────────────────────────────

app.include_router(router)
app.include_router(
    settings_router.router,
    prefix="/settings",
    dependencies=[Security(verify_api_key)],
)
