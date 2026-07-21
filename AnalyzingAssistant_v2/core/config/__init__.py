"""
core/config

config/LLM/config.yaml 에 대한 전역 접근 facade.

raw 파싱/저장은 llm_config (→ _yaml_io) 가 담당하고, 이 모듈은 앱 전역에서
쓰이는 편의 접근자를 제공한다.

사용 예:
    import core.config as config

    raw     = config.llm()                       # 전체 raw dict
    active  = config.active_llm()                # 활성 LLM 프로필 dict
    embed   = config.active_embed()              # 활성 Embed 프로필 dict
    model   = config.get_str("pipeline.context_strategy", "truncation")
    ctx     = config.get("pipeline.num_ctx")
    config.save_llm(raw)                          # 전체 raw dict 저장
"""

from __future__ import annotations

from typing import Any

from core.config import llm_config

DEFAULT_SYSTEM_ANALYSIS_GUIDELINES = (
    "- 로그에 직접적 근거가 없는 내용은 분석에 포함하지 않는다\n"
    "- 추측성 판단은 별도 구분하여 표시한다\n"
    "- 각 분석 항목에 반드시 evidence 로그 라인을 명시한다\n"
    "- 판정은 제공된 score와 매칭 결과에 기반한다"
)


# ── raw 로드/저장 ─────────────────────────────────────────────────────────────

def llm() -> dict:
    """config/LLM/config.yaml 전체를 raw dict 로 반환한다."""
    return llm_config.load()


def save_llm(data: dict) -> None:
    """raw dict 를 config/LLM/config.yaml 에 저장한다."""
    llm_config.save(data)


# ── dotted-path 접근자 ────────────────────────────────────────────────────────

def get(path: str, default: Any = None) -> Any:
    """"pipeline.num_ctx" 처럼 점(.)으로 구분된 경로로 값을 조회한다."""
    node: Any = llm_config.load()
    for key in path.split("."):
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node if node is not None else default


def get_str(path: str, default: str = "") -> str:
    v = get(path, None)
    return default if v is None else str(v)


def get_int(path: str, default: int = 0) -> int:
    v = get(path, None)
    if v is None:
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def get_float(path: str, default: float = 0.0) -> float:
    v = get(path, None)
    if v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def get_bool(path: str, default: bool = False) -> bool:
    v = get(path, None)
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


# ── 프로필 접근자 ─────────────────────────────────────────────────────────────

def _profiles(list_key: str) -> list[dict]:
    return llm_config.load().get(list_key, []) or []


def _resolve_profile(list_key: str, name: str | None) -> dict | None:
    """이름이 일치하는 프로필을 반환. 없으면 목록의 첫 번째, 목록이 비면 None."""
    profiles = _profiles(list_key)
    if not profiles:
        return None
    if name:
        for p in profiles:
            if p.get("name") == name:
                return p
    return profiles[0]


def active_llm() -> dict:
    """활성 LLM 프로필 dict 를 반환한다. active_llm 이 비어 있으면 첫 프로필로 폴백한다."""
    p = _resolve_profile("llm_profiles", llm_config.load().get("active_llm"))
    return p if p is not None else {}


def active_embed() -> dict:
    """활성 Embedding 프로필 dict 를 반환한다. active_embed 가 비어 있으면 첫 프로필로 폴백한다."""
    p = _resolve_profile("embed_profiles", llm_config.load().get("active_embed"))
    return p if p is not None else {}


def reranker_llm() -> dict | None:
    """KB Reranker 1차 LLM 프로필 dict. reranker_llm 이름이 없거나 못 찾으면 None."""
    name = llm_config.load().get("reranker_llm")
    if not name:
        return None
    return llm_config.find_llm_profile(name)


def reranker_fallback_llm() -> dict | None:
    """KB Reranker fallback LLM 프로필 dict. reranker_fallback_llm 이름이 없거나 못 찾으면 None."""
    name = llm_config.load().get("reranker_fallback_llm")
    if not name:
        return None
    return llm_config.find_llm_profile(name)
