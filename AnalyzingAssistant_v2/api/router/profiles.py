"""
api/router/profiles.py

분석 프로파일 CRUD API.

프로파일은 config/profiles/*.json 파일 1개 = 1 프로파일로 저장된다.
비즈니스 로직은 core/profile.py 에 위임하고, 본 라우터는 HTTP 계층만 담당한다.

엔드포인트 (prefix=/profiles):
    GET    /            목록 조회 (요약 필드)
    GET    /{name}      단건 조회 (전체 필드 — 편집용)
    POST   /            생성 (이름 중복 시 409)
    PUT    /{name}      수정 (이름 변경 시 rename, 대상 없으면 404)
    DELETE /{name}      삭제 (대상 없으면 404)
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from core.profile import (
    Profile,
    load_profiles,
    load_profile,
    save_profile,
    delete_profile,
    rename_profile,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Pydantic 모델 ─────────────────────────────────────────────────────────────

class ProfileSaveRequest(BaseModel):
    name: str
    description: str = ""
    analysis_guidelines: str = ""
    prefilter_keywords: list[str] = Field(default_factory=list)
    knowledge_refs: list[int] = Field(default_factory=list)
    category: str = ""


def _to_profile(req: ProfileSaveRequest) -> Profile:
    return Profile(
        name                = req.name,
        description         = req.description,
        analysis_guidelines = req.analysis_guidelines,
        prefilter_keywords  = list(req.prefilter_keywords),
        knowledge_refs      = list(req.knowledge_refs),
        category            = req.category,
    )


def _to_dict(p: Profile) -> dict:
    return {
        "name":                p.name,
        "description":         p.description,
        "analysis_guidelines": p.analysis_guidelines,
        "prefilter_keywords":  p.prefilter_keywords,
        "knowledge_refs":      p.knowledge_refs,
        "category":            p.category,
    }


# ── 엔드포인트 ────────────────────────────────────────────────────────────────

@router.get("", summary="분석 프로파일 목록 조회")
def list_profiles() -> list[dict]:
    """목록 조회 — 요약 필드(name/description/prefilter_keywords/knowledge_refs)를 반환한다."""
    return [
        {
            "name":               p.name,
            "description":        p.description,
            "prefilter_keywords": p.prefilter_keywords,
            "knowledge_refs":     p.knowledge_refs,
            "category":           p.category,
        }
        for p in load_profiles()
    ]


@router.get("/{name}", summary="분석 프로파일 단건 조회")
def get_profile(name: str) -> dict:
    """단건 조회 — 편집에 필요한 전체 필드를 반환한다."""
    p = load_profile(name)
    if p is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"프로파일 '{name}' 을 찾을 수 없습니다.",
        )
    return _to_dict(p)


@router.post("", status_code=status.HTTP_201_CREATED, summary="분석 프로파일 생성")
def create_profile(req: ProfileSaveRequest) -> dict:
    """프로파일을 생성한다. 같은 이름이 이미 있으면 409."""
    if not req.name.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="프로파일 이름을 입력하세요.",
        )
    if load_profile(req.name) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"프로파일 '{req.name}' 이 이미 존재합니다.",
        )
    profile = _to_profile(req)
    save_profile(profile)
    return _to_dict(profile)


@router.put("/{name}", summary="분석 프로파일 수정")
def update_profile(name: str, req: ProfileSaveRequest) -> dict:
    """
    프로파일을 수정한다.

    - 대상(name)이 없으면 404.
    - 이름 변경(name != req.name) 시 rename. 새 이름이 다른 프로파일과 충돌하면 409.
    """
    if load_profile(name) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"프로파일 '{name}' 을 찾을 수 없습니다.",
        )
    if not req.name.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="프로파일 이름을 입력하세요.",
        )

    renamed = req.name != name
    if renamed and load_profile(req.name) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"프로파일 '{req.name}' 이 이미 존재합니다.",
        )

    profile = _to_profile(req)
    if renamed:
        rename_profile(name, profile)
    else:
        save_profile(profile)
    return _to_dict(profile)


@router.delete("/{name}", summary="분석 프로파일 삭제")
def remove_profile(name: str) -> dict:
    """프로파일을 삭제한다. 대상이 없으면 404."""
    if not delete_profile(name):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"프로파일 '{name}' 을 찾을 수 없습니다.",
        )
    return {"result": "ok"}
