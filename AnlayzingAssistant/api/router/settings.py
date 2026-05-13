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
from pydantic import BaseModel, field_validator

from core.config import DEFAULT_SYSTEM_ANALYSIS_GUIDELINES

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

    @field_validator("max_tokens", "timeout", "report_temperature", mode="before")
    @classmethod
    def empty_str_to_none(cls, v):
        if v == "":
            return None
        return v


class EmbeddingConfigSaveRequest(BaseModel):
    profile: str
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None

    @field_validator("base_url", "api_key", "model", mode="before")
    @classmethod
    def empty_str_to_none(cls, v):
        if v == "":
            return None
        return v


class GuidelinesSaveRequest(BaseModel):
    value: str


class PipelineConfigSaveRequest(BaseModel):
    kb_threshold: float | None = None
    definite_threshold: float | None = None
    max_log_lines: int | None = None
    stage6_reflection_enabled: bool | None = None
    context_strategy: str | None = None
    hybrid_overflow_ratio: float | None = None
    num_ctx: int | None = None
    observability_enabled: bool | None = None


class ConnectionCheckRequest(BaseModel):
    profile: str
    model: str | None = None


# ── 공통 엔드포인트 ───────────────────────────────────────────────────────────

@router.get("/active", summary="활성 프로파일 조회")
def get_active_profiles() -> dict[str, str]:
    config = _load_config()
    return {
        "active_llm": config.get("active_llm", ""),
        "active_embed": config.get("active_embed", ""),
    }


# ── 분석 프로파일 엔드포인트 ─────────────────────────────────────────────────

@router.get("/profiles", summary="분석 프로파일 목록 조회")
def get_analysis_profiles() -> list[dict]:
    from core.profile import load_profiles, PROFILES_DIR
    return [
        {
            "name":               p.name,
            "description":        p.description,
            "prefilter_keywords": p.prefilter_keywords,
        }
        for p in load_profiles(PROFILES_DIR)
    ]


# ── 시스템 분석 지침 엔드포인트 ───────────────────────────────────────────────

@router.get("/guidelines", summary="시스템 분석 지침 조회")
def get_guidelines() -> dict[str, str]:
    config = _load_config()
    return {
        "value":   config.get("system_analysis_guidelines", DEFAULT_SYSTEM_ANALYSIS_GUIDELINES),
        "default": DEFAULT_SYSTEM_ANALYSIS_GUIDELINES,
    }


@router.post("/guidelines", summary="시스템 분석 지침 저장")
def save_guidelines(req: GuidelinesSaveRequest) -> dict[str, str]:
    config = _load_config()
    config["system_analysis_guidelines"] = req.value
    _save_config(config)
    return {"result": "ok"}


# ── Pipeline 엔드포인트 ───────────────────────────────────────────────────────

@router.get("/pipeline/config", summary="파이프라인 설정 조회")
def get_pipeline_config() -> dict[str, Any]:
    config = _load_config()
    p = config.get("pipeline", {})
    return {
        "kb_threshold":              float(p.get("kb_threshold",              0.70)),
        "definite_threshold":        float(p.get("definite_threshold",        0.50)),
        "max_log_lines":             int(p.get("max_log_lines",               200)),
        "stage6_reflection_enabled": bool(p.get("stage6_reflection_enabled",  True)),
        "context_strategy":          p.get("context_strategy",                "truncation"),
        "hybrid_overflow_ratio":     float(p.get("hybrid_overflow_ratio",     0.3)),
        "num_ctx":                   int(p["num_ctx"]) if p.get("num_ctx") else None,
        "observability_enabled":     bool(p.get("observability_enabled",      False)),
    }


@router.get("/pipeline/num_ctx", summary="활성 LLM 모델 num_ctx 조회")
async def query_num_ctx() -> dict[str, Any]:
    config = _load_config()
    profiles = config.get("llm_profiles", [])
    active_name = config.get("active_llm", "")
    active = next((p for p in profiles if p["name"] == active_name), profiles[0] if profiles else None)
    if not active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="활성 LLM 프로파일을 찾을 수 없습니다.")

    num_ctx, source = await _query_num_ctx_impl(active["base_url"], active["model"])
    suggested = max(50, min(2000, (num_ctx - 4000) // 30)) if num_ctx else None
    return {"model": active["model"], "num_ctx": num_ctx, "source": source, "suggested_max_log_lines": suggested}


@router.post("/pipeline/config", summary="파이프라인 설정 저장")
def save_pipeline_config(req: PipelineConfigSaveRequest) -> dict[str, str]:
    config = _load_config()
    pipeline = config.get("pipeline", {})
    pipeline.update(req.model_dump(exclude_none=True))
    config["pipeline"] = pipeline
    _save_config(config)
    return {"result": "ok"}


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
    config["active_llm"] = req.profile
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
    ok, detail = await _check_connection(p["base_url"], p.get("api_key", ""), model, is_embedding=True)
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
    config["active_embed"] = req.profile
    _save_config(config)
    return {"result": "ok"}


# ── 공통 헬퍼 ─────────────────────────────────────────────────────────────────

_KNOWN_CONTEXT_WINDOWS: dict[str, int] = {
    "gpt-4o": 128_000, "gpt-4o-mini": 128_000, "gpt-4-turbo": 128_000,
    "gpt-4": 8_192, "gpt-4-32k": 32_768, "gpt-3.5-turbo": 16_385,
    "claude-3-5-sonnet-20241022": 200_000, "claude-3-5-haiku-20241022": 200_000,
    "claude-3-opus-20240229": 200_000, "claude-3-sonnet-20240229": 200_000,
    "claude-3-haiku-20240307": 200_000,
}


async def _query_num_ctx_impl(base_url: str, model: str) -> tuple[int | None, str]:
    """모델 컨텍스트 윈도우를 Ollama → OpenAI 호환 → 하드코딩 순으로 조회한다."""
    ollama_base = base_url.rstrip("/")
    if ollama_base.endswith("/v1"):
        ollama_base = ollama_base[:-3]
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(f"{ollama_base}/api/show", json={"model": model})
            res.raise_for_status()
            data = res.json()
        for key, val in data.get("model_info", {}).items():
            if "context_length" in key:
                return int(val), "Ollama /api/show"
        for line in data.get("parameters", "").splitlines():
            parts = line.strip().split()
            if len(parts) >= 2 and parts[0] == "num_ctx":
                return int(parts[1]), "Ollama /api/show (parameters)"
    except Exception:
        pass

    v1_base = base_url.rstrip("/")
    if not v1_base.endswith("/v1"):
        v1_base += "/v1"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.get(f"{v1_base}/models/{model}")
            res.raise_for_status()
            data = res.json()
        for field in ("max_model_len", "context_length", "max_context_length"):
            if data.get(field):
                return int(data[field]), f"/v1/models ({field})"
    except Exception:
        pass

    model_lower = model.lower()
    for known, ctx in _KNOWN_CONTEXT_WINDOWS.items():
        if model_lower.startswith(known.lower()):
            return ctx, f"알려진 모델 목록 ({known})"

    return None, ""


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


async def _check_connection(base_url: str, api_key: str, model: str, is_embedding: bool = False) -> tuple[bool, str]:
    """openai 패키지로 연결 상태를 확인한다."""
    from openai import AsyncOpenAI, APIConnectionError, APIStatusError

    client = AsyncOpenAI(base_url=base_url, api_key=api_key or "ollama")
    try:
        if is_embedding:
            await client.embeddings.create(input="ping", model=model)
        else:
            await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
            )
        return True, "연결 성공"
    except APIConnectionError as e:
        return False, f"연결 실패: {e}"
    except APIStatusError as e:
        return False, f"HTTP {e.status_code}: {e.message}"
    except Exception as e:
        return False, str(e)
