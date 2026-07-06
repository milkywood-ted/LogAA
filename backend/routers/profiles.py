"""
routers/profiles.py

분석 프로파일 / 도메인 지식 관리 프록시 라우터.

AnalyzingAssistant(AA) 서버의 /profiles, /knowledge CRUD 엔드포인트를
프론트엔드용 /api/* 경로로 패스스루한다. AA 가 반환하는 4xx(404/409/400 등)
상태코드는 그대로 프론트에 전파한다.
"""

from contextlib import contextmanager

import httpx
from fastapi import APIRouter, HTTPException
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

class ProfileSaveRequest(BaseModel):
    name: str
    description: str = ""
    analysis_guidelines: str = ""
    prefilter_keywords: list[str] = Field(default_factory=list)
    knowledge_refs: list[int] = Field(default_factory=list)
    category: str = ""


class KnowledgeSaveRequest(BaseModel):
    name: str
    store_type: str = "sqlite"
    content: str = ""
    description: str = ""
    category: str = ""
    sub_category: str = ""
    chip_tags: list[str] = Field(default_factory=list)


# ── 분석 프로파일 ─────────────────────────────────────────────────────────────

@router.get("/api/profiles")
async def list_profiles():
    return await aa_client.get_analysis_profiles()


@router.get("/api/profiles/{name}")
async def get_profile(name: str):
    with _propagate_aa_errors():
        return await aa_client.get_analysis_profile(name)


@router.post("/api/profiles")
async def create_profile(req: ProfileSaveRequest):
    with _propagate_aa_errors():
        return await aa_client.create_analysis_profile(req.model_dump())


@router.put("/api/profiles/{name}")
async def update_profile(name: str, req: ProfileSaveRequest):
    with _propagate_aa_errors():
        return await aa_client.update_analysis_profile(name, req.model_dump())


@router.delete("/api/profiles/{name}")
async def delete_profile(name: str):
    with _propagate_aa_errors():
        return await aa_client.delete_analysis_profile(name)


# ── 도메인 지식 ───────────────────────────────────────────────────────────────

@router.get("/api/knowledge")
async def list_knowledge():
    return await aa_client.get_knowledge_list()


@router.get("/api/knowledge/{kid}")
async def get_knowledge(kid: int):
    with _propagate_aa_errors():
        return await aa_client.get_knowledge(kid)


@router.post("/api/knowledge")
async def create_knowledge(req: KnowledgeSaveRequest):
    with _propagate_aa_errors():
        return await aa_client.create_knowledge(req.model_dump())


@router.put("/api/knowledge/{kid}")
async def update_knowledge(kid: int, req: KnowledgeSaveRequest):
    with _propagate_aa_errors():
        return await aa_client.update_knowledge(kid, req.model_dump())


@router.delete("/api/knowledge/{kid}")
async def delete_knowledge(kid: int):
    with _propagate_aa_errors():
        return await aa_client.delete_knowledge(kid)
