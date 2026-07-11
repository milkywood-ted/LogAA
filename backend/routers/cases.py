"""
routers/cases.py

케이스 / 패턴 관리 프록시 라우터.

AnalyzingAssistant(AA) 서버의 /cases, /patterns CRUD 엔드포인트를
프론트엔드용 /api/* 경로로 패스스루한다. AA 가 반환하는 4xx/5xx
상태코드는 그대로 프론트에 전파한다.
"""

from contextlib import contextmanager
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from AnalyzingAssistant_client import aa_client

router = APIRouter()


@contextmanager
def _propagate_aa_errors():
    """AA 의 HTTP 4xx/5xx 상태코드와 detail 을 그대로 전파한다."""
    try:
        yield
    except httpx.HTTPStatusError as e:
        detail = e.response.text
        try:
            detail = e.response.json().get("detail", detail)
        except Exception:
            pass
        raise HTTPException(status_code=e.response.status_code, detail=detail)


# ── Pydantic 모델 ─────────────────────────────────────────────────────────────
# AA V2 api/router/cases.py 의 모델을 미러링한다 (여기 없는 필드는 model_dump()
# 에서 조용히 탈락하므로 필드 목록을 반드시 동기화할 것).
# 조건부 필수 검증(결함→좌표 필수 등)은 AA 쪽에만 있고, 프록시는 형태만 검증한다.
# 스키마 설계: Document/로그분석 리포트 및 케이스 스키마 개선/케이스 스키마 개선 구현 설계.md

class FixEntry(BaseModel):
    type: Literal["fix", "defensive"]
    module: str
    change: str = ""


class FixAction(BaseModel):
    selected: bool = False
    entries: list[FixEntry] = Field(default_factory=list)


class AdditionalAction(BaseModel):
    selected: bool = False
    items: list[str] = Field(default_factory=list)
    plan: str = ""


class KeepAction(BaseModel):
    selected: bool = False
    detail: Literal["close_no_defect", "accept_defect", "close_undetermined"] | None = None
    reason: str = ""


class HandoverAction(BaseModel):
    selected: bool = False
    items: list[str] = Field(default_factory=list)
    owner: str = ""
    channel: str = ""


class CaseActions(BaseModel):
    fix: FixAction = FixAction()
    additional: AdditionalAction = AdditionalAction()
    keep: KeepAction = KeepAction()
    handover: HandoverAction = HandoverAction()


class CaseSaveRequest(BaseModel):
    name: str
    description: str = ""
    keywords: list[str] = Field(default_factory=list)
    analysis: str = ""
    profile_refs: list[str] = Field(default_factory=list)
    chip_tags: list[str] = Field(default_factory=list)
    # 기본 정보 (섹션 01)
    analyst: str = ""
    owner_module: str = ""
    analysis_date: str | None = None
    log_source: str = ""
    # 판정 (섹션 03) — verdict 는 모든 저장에서 필수
    verdict: Literal["defect", "no_defect", "undetermined"]
    symptom_module: str = ""
    defect_area_type: Literal["module", "external", "verification"] | None = None
    defect_area_module: str = ""
    defect_area_items: list[str] = Field(default_factory=list)
    undetermined_reason: Literal["insufficient_logs", "not_reproducible", "other"] | None = None
    undetermined_reason_note: str = ""
    verdict_rationale: str = ""
    # 조치 (섹션 04) / 비고 (섹션 05)
    actions: CaseActions = CaseActions()
    notes: str = ""


class ReferenceAddRequest(BaseModel):
    system: str
    reference_id: str


class PatternSaveRequest(BaseModel):
    name: str
    type: str
    description: str = ""
    keywords: list[str] = Field(default_factory=list)
    weight: float = 1.0
    is_required: bool = False
    analysis_guidelines: str = ""
    chip_tags: list[str] = Field(default_factory=list)
    pattern: str | None = None
    event_dedup_window_sec: float | None = None
    steps: list[str] = Field(default_factory=list)
    step_dedup: bool = False
    non_overlapping: bool = False
    window_sec: float | None = None
    count_threshold: int | None = None
    count_unique_only: bool = False
    trigger_pattern: str | None = None
    absent_pattern: str | None = None
    operator: str | None = None
    components: list[str] = Field(default_factory=list)


# ── 케이스 ────────────────────────────────────────────────────────────────────

@router.get("/api/cases")
async def list_cases():
    with _propagate_aa_errors():
        return await aa_client.get_cases()


@router.get("/api/cases/{cid}")
async def get_case(cid: int):
    with _propagate_aa_errors():
        return await aa_client.get_case(cid)


@router.post("/api/cases")
async def create_case(req: CaseSaveRequest):
    with _propagate_aa_errors():
        return await aa_client.create_case(req.model_dump())


@router.put("/api/cases/{cid}")
async def update_case(cid: int, req: CaseSaveRequest):
    with _propagate_aa_errors():
        return await aa_client.update_case(cid, req.model_dump())


@router.delete("/api/cases/{cid}")
async def delete_case(cid: int):
    with _propagate_aa_errors():
        return await aa_client.delete_case(cid)


@router.post("/api/cases/sync")
async def sync_cases():
    with _propagate_aa_errors():
        return await aa_client.sync_cases()


# ── 케이스 ↔ 패턴 연결 ────────────────────────────────────────────────────────

@router.get("/api/cases/{cid}/patterns")
async def list_case_patterns(cid: int):
    with _propagate_aa_errors():
        return await aa_client.get_case_patterns(cid)


@router.post("/api/cases/{cid}/patterns/{pid}")
async def link_pattern(cid: int, pid: int):
    with _propagate_aa_errors():
        return await aa_client.link_pattern(cid, pid)


@router.delete("/api/cases/{cid}/patterns/{pid}")
async def unlink_pattern(cid: int, pid: int):
    with _propagate_aa_errors():
        return await aa_client.unlink_pattern(cid, pid)


# ── 케이스 외부 참조 ID ───────────────────────────────────────────────────────

@router.get("/api/cases/{cid}/references")
async def list_case_references(cid: int):
    with _propagate_aa_errors():
        return await aa_client.get_case_references(cid)


@router.post("/api/cases/{cid}/references")
async def add_case_reference(cid: int, req: ReferenceAddRequest):
    with _propagate_aa_errors():
        return await aa_client.add_case_reference(cid, req.model_dump())


@router.delete("/api/cases/{cid}/references/{rid}")
async def delete_case_reference(cid: int, rid: int):
    with _propagate_aa_errors():
        return await aa_client.delete_case_reference(cid, rid)


# ── 패턴 ─────────────────────────────────────────────────────────────────────

@router.get("/api/patterns")
async def list_patterns(type: str | None = None):
    with _propagate_aa_errors():
        return await aa_client.get_patterns(type)


@router.get("/api/patterns/{pid}")
async def get_pattern(pid: int):
    with _propagate_aa_errors():
        return await aa_client.get_pattern(pid)


@router.post("/api/patterns")
async def create_pattern(req: PatternSaveRequest):
    with _propagate_aa_errors():
        return await aa_client.create_pattern(req.model_dump())


@router.put("/api/patterns/{pid}")
async def update_pattern(pid: int, req: PatternSaveRequest):
    with _propagate_aa_errors():
        return await aa_client.update_pattern(pid, req.model_dump())


@router.delete("/api/patterns/{pid}")
async def delete_pattern(pid: int):
    with _propagate_aa_errors():
        return await aa_client.delete_pattern(pid)
