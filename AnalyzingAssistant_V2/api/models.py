"""
api/models.py

FastAPI request / response Pydantic 모델 정의.
"""

from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field


# ── Request ───────────────────────────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    problem_text: str = Field(..., description="문제 설명")
    log_paths: list[str] = Field(..., description="로그 파일 또는 폴더 경로 목록 (서버 로컬 경로)")
    log_path_base: str | None = Field(
        default=None,
        description="로그 출처 표시용 기준 경로. 지정 시 이 경로를 잘라낸 상대경로로 출처를 표기한다.",
    )
    profile_names: list[str] = Field(default_factory=list, description="적용할 분석 프로파일 이름 목록")
    pinned_case_name: str | None = Field(default=None, description="직접 지정할 KB 케이스 이름")
    recursive: bool = Field(default=True, description="폴더 탐색 시 하위 폴더 재귀 여부")

    # log_parsers.yaml 에 정의된 파서 이름 목록.
    # 비어 있으면 default=true 파서(dmesg)만 자동 적용.
    parser_names: list[str] = Field(
        default_factory=list,
        description="적용할 로그 파서 이름 목록 (config/log_parsers.yaml 기준). 비어 있으면 default 파서만 사용.",
    )

    # Stage 1 RefineConfig 관련 옵션 (선택)
    input_keywords: list[str] = Field(
        default_factory=list,
        description="로그 사전 필터링 키워드 목록 (빈 리스트면 전체 로드)",
    )
    anchors: list[str] = Field(
        default_factory=list,
        description="Stage 1 anchor 키워드 — 이 패턴이 있는 라인 주변을 우선 추출",
    )

    chip: list[str] = Field(
        default_factory=list,
        description="defect 의 칩 정보. Stage 3/4 패턴을 chip_tags 기준으로 필터링한다. "
                    "비어 있으면 필터링하지 않는다.",
    )

    defect_id: str | None = Field(
        default=None,
        description="분석 대상 defect ID. history 테이블에 기록된다.",
    )


# ── Response ──────────────────────────────────────────────────────────────────

class JobStatusResponse(BaseModel):
    job_id: str
    status: str          # pending | running | done | error
    stage: str = ""      # 현재 진행 중인 stage 설명 (running 상태에서 사용)
    progress: int = 0    # 0~100
    result: dict[str, Any] | None = None
    error: str | None = None


class SubmitResponse(BaseModel):
    job_id: str
    status: str = "pending"


class HealthResponse(BaseModel):
    status: str = "ok"
    worker_threads: int
    active_jobs: int
    queued_jobs: int
