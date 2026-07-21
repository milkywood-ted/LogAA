"""
api/main.py

FastAPI 앱 및 엔드포인트.

실행:
    uvicorn api.main:app --host 0.0.0.0 --port 8000

"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, HTTPException, Security, status
from fastapi.responses import StreamingResponse

from api.auth import verify_api_key
from api.job_store import count_jobs_by_status, get_job
from api.models import AnalyzeRequest, HealthResponse, JobStatusResponse, SubmitResponse
from api.router import settings as settings_router
from api.router import profiles as profiles_router
from api.router import knowledge as knowledge_router
from api.router import cases as cases_router
from api.router import patterns as patterns_router
from api.router import history as history_router
from api.worker import shutdown, startup, submit_job, cancel_job

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
    "/analyze/{job_id}/stream",
    summary="분석 진행 상황 SSE 스트림",
)
async def stream_analyze_result(job_id: str):
    """
    분석 진행 상황을 Server-Sent Events 로 스트리밍한다.

    이벤트 타입:
      - progress : 진행 중 (status, stage, progress)
      - done     : 완료 (status, result)
      - error    : 오류 (status, error)

    done / error 이벤트 전송 후 스트림을 닫는다.
    """
    async def event_generator():
        last_updated_at = None
        while True:
            job = get_job(job_id)
            if job is None:
                yield _sse_event("error", {"status": "error", "error": f"job_id '{job_id}' 를 찾을 수 없습니다."})
                return

            updated_at = job.get("updated_at")
            if updated_at != last_updated_at:
                last_updated_at = updated_at
                s = job["status"]
                if s == "done":
                    yield _sse_event("done", {"status": "done", "result": job.get("result")})
                    return
                if s == "error":
                    yield _sse_event("error", {"status": "error", "error": job.get("error", "")})
                    return
                if s == "cancelled":
                    yield _sse_event("cancelled", {"status": "cancelled"})
                    return
                yield _sse_event("progress", {
                    "status":   s,
                    "stage":    job.get("stage", ""),
                    "progress": job.get("progress", 0),
                })

            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def _sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.delete(
    "/analyze/{job_id}",
    status_code=status.HTTP_200_OK,
    summary="분석 취소",
)
def cancel_analyze(job_id: str) -> dict:
    """
    실행 중인 분석 작업에 취소 신호를 보낸다.

    - 취소는 다음 stage 진입 시점에 적용된다 (LLM 호출 도중에는 즉시 중단 불가).
    - 이미 완료/오류/취소된 작업에는 적용되지 않는다.
    """
    job = get_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"job_id '{job_id}' 를 찾을 수 없습니다.",
        )
    if job["status"] not in ("pending", "running"):
        return {"result": "already_finished", "status": job["status"]}

    cancel_job(job_id)
    return {"result": "cancel_requested", "job_id": job_id}


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
    import core.config as config

    counts = count_jobs_by_status()
    return HealthResponse(
        status="ok",
        worker_threads=config.get_int("server.max_workers", 10),
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
app.include_router(
    profiles_router.router,
    prefix="/profiles",
    dependencies=[Security(verify_api_key)],
)
app.include_router(
    knowledge_router.router,
    prefix="/knowledge",
    dependencies=[Security(verify_api_key)],
)
app.include_router(
    cases_router.router,
    prefix="/cases",
    dependencies=[Security(verify_api_key)],
)
app.include_router(
    patterns_router.router,
    prefix="/patterns",
    dependencies=[Security(verify_api_key)],
)
app.include_router(
    history_router.router,
    prefix="/history",
    dependencies=[Security(verify_api_key)],
)
