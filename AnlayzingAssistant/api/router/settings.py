"""
api/routers/settings.py

LLM / Embedding 설정 조회 및 저장 API.

config/LLM/config.yaml 구조:
    active_llm: ollama
    active_embed: ollama-embed
    llm_profiles:
      - name: ollama
        provider: openai
        base_url: http://localhost:11434/v1
        api_key: ollama
        model: qwen3.5:35b
        max_tokens: 65535
        timeout: null
        report_temperature: 0.2
    embed_profiles:
      - name: ollama-embed
        base_url: http://localhost:11434/v1
        api_key: ollama
        model: bge-m3
    pipeline:
      ...
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx
import yaml
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "LLM" / "config.yaml"

router = APIRouter()


# ── yaml 읽기/쓰기 헬퍼 ────────────────────────────────────────────────────────

def _load_config() -> dict:
    if not _CONFIG_PATH.exists():
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"config 파일을 찾을 수 없습니다: {_CONFIG_PATH}",
        )
    with open(_CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _save_config(config: dict) -> None:
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


def _get_llm_profile(config: dict, profile_name: str) -> dict:
    for p in config.get("llm_profiles", []):
        if p["name"] == profile_name:
            return p
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"LLM 프로필 '{profile_name}' 을 찾을 수 없습니다.",
    )


def _get_embed_profile(config: dict, profile_name: str) -> dict:
    for p in config.get("embed_profiles", []):
        if p["name"] == profile_name:
            return p
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Embedding 프로필 '{profile_name}' 을 찾을 수 없습니다.",
    )


# ── Pydantic 모델 ─────────────────────────────────────────────────────────────

class LLMConfigSaveRequest(BaseModel):
    profile: str
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    max_tokens: int | None = None
    timeout: float | None = None
    report_temperature: float | None = None
    provider: str | None = None


class EmbeddingConfigSaveRequest(BaseModel):
    profile: str
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None


class ConnectionCheckRequest(BaseModel):
    profile: str
    model: str | None = None


# ── LLM 엔드포인트 ────────────────────────────────────────────────────────────

@router.get("/llm/profiles", summary="LLM 프로필 목록 조회")
def get_llm_profiles() -> list[str]:
    config = _load_config()
    return [p["name"] for p in config.get("llm_profiles", [])]


@router.get("/llm/models", summary="LLM 모델 목록 조회")
async def get_llm_models(profile: str) -> list[str]:
    """프로필의 base_url 에서 사용 가능한 모델 목록을 조회한다."""
    config = _load_config()
    p = _get_llm_profile(config, profile)
    return await _fetch_models(p["base_url"], p.get("api_key", ""))


@router.post("/llm/check", summary="LLM 연결 확인")
async def check_llm_connection(req: ConnectionCheckRequest) -> dict[str, Any]:
    config = _load_config()
    p = _get_llm_profile(config, req.profile)
    model = req.model or p.get("model", "")
    ok, detail = await _check_connection(p["base_url"], p.get("api_key", ""), model)
    return {"ok": ok, "detail": detail}


@router.get("/llm/config", summary="LLM 프로필 설정 조회")
def get_llm_config(profile: str) -> dict[str, Any]:
    config = _load_config()
    p = _get_llm_profile(config, profile)
    return {k: v for k, v in p.items() if k != "name"}


@router.post("/llm/config", summary="LLM 프로필 설정 저장")
def save_llm_config(req: LLMConfigSaveRequest) -> dict[str, str]:
    config = _load_config()
    profiles = config.get("llm_profiles", [])
    updated = False
    for p in profiles:
        if p["name"] == req.profile:
            _apply_updates(p, req.model_dump(exclude={"profile"}, exclude_none=True))
            updated = True
            break
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"LLM 프로필 '{req.profile}' 을 찾을 수 없습니다.",
        )
    config["llm_profiles"] = profiles
    _save_config(config)
    return {"result": "ok"}


# ── Embedding 엔드포인트 ──────────────────────────────────────────────────────

@router.get("/embedding/profiles", summary="Embedding 프로필 목록 조회")
def get_embedding_profiles() -> list[str]:
    config = _load_config()
    return [p["name"] for p in config.get("embed_profiles", [])]


@router.get("/embedding/models", summary="Embedding 모델 목록 조회")
async def get_embedding_models(profile: str) -> list[str]:
    config = _load_config()
    p = _get_embed_profile(config, profile)
    return await _fetch_models(p["base_url"], p.get("api_key", ""))


@router.post("/embedding/check", summary="Embedding 연결 확인")
async def check_embedding_connection(req: ConnectionCheckRequest) -> dict[str, Any]:
    config = _load_config()
    p = _get_embed_profile(config, req.profile)
    model = req.model or p.get("model", "")
    ok, detail = await _check_connection(p["base_url"], p.get("api_key", ""), model)
    return {"ok": ok, "detail": detail}


@router.get("/embedding/config", summary="Embedding 프로필 설정 조회")
def get_embedding_config(profile: str) -> dict[str, Any]:
    config = _load_config()
    p = _get_embed_profile(config, profile)
    return {k: v for k, v in p.items() if k != "name"}


@router.post("/embedding/config", summary="Embedding 프로필 설정 저장")
def save_embedding_config(req: EmbeddingConfigSaveRequest) -> dict[str, str]:
    config = _load_config()
    profiles = config.get("embed_profiles", [])
    updated = False
    for p in profiles:
        if p["name"] == req.profile:
            _apply_updates(p, req.model_dump(exclude={"profile"}, exclude_none=True))
            updated = True
            break
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Embedding 프로필 '{req.profile}' 을 찾을 수 없습니다.",
        )
    config["embed_profiles"] = profiles
    _save_config(config)
    return {"result": "ok"}


# ── 공통 헬퍼 ─────────────────────────────────────────────────────────────────

def _apply_updates(profile: dict, updates: dict) -> None:
    """None 이 아닌 값만 profile 에 반영한다."""
    for k, v in updates.items():
        profile[k] = v


async def _fetch_models(base_url: str, api_key: str) -> list[str]:
    """OpenAI 호환 /v1/models 엔드포인트에서 모델 목록을 가져온다."""
    url = base_url.rstrip("/").removesuffix("/v1") + "/v1/models"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key and api_key != "ollama" else {}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.get(url, headers=headers)
            res.raise_for_status()
            data = res.json()
            return [m["id"] for m in data.get("data", [])]
    except Exception as e:
        logger.warning(f"모델 목록 조회 실패 ({url}): {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"모델 목록 조회 실패: {e}",
        )


async def _check_connection(base_url: str, api_key: str, model: str) -> tuple[bool, str]:
    """간단한 completion 요청으로 연결 상태를 확인한다."""
    url = base_url.rstrip("/").removesuffix("/v1") + "/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}" if api_key and api_key != "ollama" else "Bearer ollama",
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.post(url, json=payload, headers=headers)
            res.raise_for_status()
            return True, "연결 성공"
    except httpx.HTTPStatusError as e:
        return False, f"HTTP {e.response.status_code}: {e.response.text[:200]}"
    except Exception as e:
        return False, str(e)
