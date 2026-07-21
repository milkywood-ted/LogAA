"""
api/router/knowledge.py

도메인 사전지식 CRUD API.

사전지식은 SQLite 'domain_knowledge' 테이블에 저장되며, store_type='chromadb'
항목은 ChromaDB 에도 임베딩된다. SQLite 변경과 ChromaDB 동기화는 core/knowledge.py
의 create/update/delete_knowledge 가 함께 처리한다. 본 라우터는 HTTP 계층만 담당한다.

엔드포인트 (prefix=/knowledge):
    GET    /          목록 조회
    GET    /{kid}     단건 조회
    POST   /          생성 (이름 중복 시 409)
    PUT    /{kid}     수정 (대상 없으면 404, 이름 중복 시 409)
    DELETE /{kid}     삭제 (ChromaDB 항목도 함께 제거, 대상 없으면 404)
"""

from __future__ import annotations

import logging
import sqlite3

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from core.knowledge import (
    DomainKnowledge,
    load_knowledge,
    load_one_knowledge,
    create_knowledge,
    update_knowledge,
    delete_knowledge,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_STORE_TYPES = ("sqlite", "chromadb")


# ── Pydantic 모델 ─────────────────────────────────────────────────────────────

class KnowledgeSaveRequest(BaseModel):
    name: str
    store_type: str = "sqlite"
    content: str = ""
    description: str = ""
    category: str = ""
    sub_category: str = ""
    chip_tags: list[str] = Field(default_factory=list)

    @field_validator("store_type")
    @classmethod
    def _check_store_type(cls, v: str) -> str:
        if v not in _STORE_TYPES:
            raise ValueError(f"store_type 은 {_STORE_TYPES} 중 하나여야 합니다.")
        return v


def _to_dict(k: DomainKnowledge) -> dict:
    return {
        "id":           k.id,
        "name":         k.name,
        "store_type":   k.store_type,
        "content":      k.content,
        "description":  k.description,
        "category":     k.category,
        "sub_category": k.sub_category,
        "chip_tags":    k.chip_tags,
    }


# ── 엔드포인트 ────────────────────────────────────────────────────────────────

@router.get("", summary="사전지식 목록 조회")
def list_knowledge() -> list[dict]:
    return [_to_dict(k) for k in load_knowledge()]


@router.get("/{kid}", summary="사전지식 단건 조회")
def get_knowledge(kid: int) -> dict:
    k = load_one_knowledge(kid)
    if k is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"사전지식 id={kid} 를 찾을 수 없습니다.",
        )
    return _to_dict(k)


@router.post("", status_code=status.HTTP_201_CREATED, summary="사전지식 생성")
def add_knowledge(req: KnowledgeSaveRequest) -> dict:
    """사전지식을 생성한다. 이름이 비었으면 400, 이미 있으면 409."""
    if not req.name.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="사전지식 이름을 입력하세요.",
        )
    try:
        kid = create_knowledge(
            req.name.strip(), req.store_type, req.content, req.description,
            category=req.category, sub_category=req.sub_category, chip_tags=list(req.chip_tags),
        )
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"사전지식 '{req.name}' 이 이미 존재합니다.",
        )
    return get_knowledge(kid)


@router.put("/{kid}", summary="사전지식 수정")
def edit_knowledge(kid: int, req: KnowledgeSaveRequest) -> dict:
    """사전지식을 수정한다. 대상 없으면 404, 이름 비었으면 400, 이름 중복 시 409."""
    if load_one_knowledge(kid) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"사전지식 id={kid} 를 찾을 수 없습니다.",
        )
    if not req.name.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="사전지식 이름을 입력하세요.",
        )
    try:
        update_knowledge(
            kid, req.name.strip(), req.store_type, req.content, req.description,
            category=req.category, sub_category=req.sub_category, chip_tags=list(req.chip_tags),
        )
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"사전지식 '{req.name}' 이 이미 존재합니다.",
        )
    return get_knowledge(kid)


@router.delete("/{kid}", summary="사전지식 삭제")
def remove_knowledge(kid: int) -> dict:
    """사전지식을 삭제한다 (ChromaDB 항목도 함께 제거). 대상 없으면 404."""
    if load_one_knowledge(kid) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"사전지식 id={kid} 를 찾을 수 없습니다.",
        )
    delete_knowledge(kid)
    return {"result": "ok"}
