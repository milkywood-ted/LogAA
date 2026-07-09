"""
core/config.py

config/LLM/config.yaml 로드 및 전역 설정 접근.

사용 예시:
    from core.config import cfg, LLMProfile

    model    = cfg.llm_model
    base_url = cfg.llm_base_url
    profiles = cfg.llm_profiles          # list[LLMProfile]
    active   = cfg.active_llm            # 활성 LLM 프로파일 이름
    eprofiles = cfg.embed_profiles       # list[LLMProfile]
    eactive   = cfg.active_embed         # 활성 Embed 프로파일 이름

config.yaml 형식:
    active_llm: ollama-qwen
    llm_profiles:
      - name: ollama-qwen
        base_url: http://localhost:11434/v1
        api_key: ollama
        model: qwen3.5:35b
        max_tokens: null
        timeout: null
        report_temperature: 0.2

    active_embed: ollama-embed
    embed_profiles:
      - name: ollama-embed
        base_url: http://localhost:11434/v1
        api_key: ollama
        model: bge-m3

    pipeline: ...

기존 단일 llm: / embedding: 섹션 형식도 자동 마이그레이션 지원.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import yaml

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "LLM" / "config.yaml"

# ── 기본값 상수 ──────────────────────────────────────────────────────────────
_DEFAULT_LLM_NAME          = "default"
_DEFAULT_EMBED_NAME        = "default-embed"
_DEFAULT_OLLAMA_URL        = "http://localhost:11434/v1"
_DEFAULT_OLLAMA_API_KEY    = "ollama"
_DEFAULT_LLM_MODEL         = "qwen3:14b"
_DEFAULT_EMBED_MODEL       = "bge-m3"
_DEFAULT_REPORT_TEMP       = 0.2
_DEFAULT_KB_THRESHOLD      = 0.70
_DEFAULT_DEFINITE_THRESHOLD = 0.50
_DEFAULT_MAX_LOG_LINES     = 200
_DEFAULT_HYBRID_RATIO      = 0.3
_DEFAULT_CONTEXT_STRATEGY  = "truncation"

DEFAULT_SYSTEM_ANALYSIS_GUIDELINES = (
    "- 로그에 직접적 근거가 없는 내용은 분석에 포함하지 않는다\n"
    "- 추측성 판단은 별도 구분하여 표시한다\n"
    "- 각 분석 항목에 반드시 evidence 로그 라인을 명시한다\n"
    "- 판정은 제공된 score와 매칭 결과에 기반한다"
)


# ── 프로파일 ──────────────────────────────────────────────────────────────────

@dataclass
class LLMProfile:
    """LLM 또는 Embedding 엔드포인트 프로파일."""
    name: str
    base_url: str
    api_key: str
    model: str
    max_tokens: int | None = None      # LLM 전용 (embed 에서는 무시)
    timeout: int | None = None         # LLM 전용
    report_temperature: float = _DEFAULT_REPORT_TEMP   # LLM 전용
    provider: str = "openai"           # "openai" | "anthropic"


def _parse_profile(d: dict[str, Any], default_name: str = _DEFAULT_LLM_NAME) -> LLMProfile:
    raw_max = d.get("max_tokens")
    raw_to  = d.get("timeout")
    return LLMProfile(
        name               = d.get("name",     default_name),
        base_url           = d.get("base_url", _DEFAULT_OLLAMA_URL),
        api_key            = d.get("api_key",  _DEFAULT_OLLAMA_API_KEY),
        model              = d.get("model",    ""),
        max_tokens         = int(raw_max) if raw_max is not None else None,
        timeout            = int(raw_to)  if raw_to  is not None else None,
        report_temperature = float(d.get("report_temperature", _DEFAULT_REPORT_TEMP)),
        provider           = d.get("provider", "openai"),
    )


def _find_active(profiles: list[LLMProfile], name: str) -> LLMProfile | None:
    """이름이 일치하는 프로파일을 반환. 없으면 첫 번째, 목록이 비면 None."""
    return next(
        (p for p in profiles if p.name == name),
        profiles[0] if profiles else None,
    )


def _load_profiles(
    raw: dict[str, Any],
    *,
    new_key: str,
    legacy_key: str,
    active_key: str,
    default_name: str,
    fallback_model: str,
) -> tuple[list[LLMProfile], str]:
    """신규(list) / 레거시(single-section) 두 형식을 모두 지원하여 프로파일 목록과 활성 이름을 반환."""
    if new_key in raw:
        profiles = [_parse_profile(p, default_name) for p in raw[new_key]]
        if not profiles:
            profiles = [LLMProfile(
                name     = default_name,
                base_url = _DEFAULT_OLLAMA_URL,
                api_key  = _DEFAULT_OLLAMA_API_KEY,
                model    = fallback_model,
            )]
        active = raw.get(active_key, profiles[0].name)
    else:
        legacy = raw.get(legacy_key, {})
        p = _parse_profile(
            {**legacy, "name": legacy.get("name", default_name)},
            default_name,
        )
        profiles = [p]
        active = p.name
    return profiles, active


# ── 전체 설정 ─────────────────────────────────────────────────────────────────

@dataclass
class AppConfig:
    # ── 활성 LLM 편의 필드 (기존 코드 호환 — active LLM profile 값) ──────────
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    llm_max_tokens: int | None
    llm_timeout: int | None
    llm_report_temperature: float
    llm_provider: str                # "openai" | "anthropic"

    # ── LLM 프로파일 목록 ────────────────────────────────────────────────────
    llm_profiles: list[LLMProfile]
    active_llm: str

    # ── 활성 Embedding 편의 필드 (기존 코드 호환 — active embed profile 값) ──
    embed_base_url: str
    embed_api_key: str
    embed_model: str

    # ── Embedding 프로파일 목록 ───────────────────────────────────────────────
    embed_profiles: list[LLMProfile]
    active_embed: str

    # ── Pipeline ──────────────────────────────────────────────────────────────
    kb_threshold: float
    definite_threshold: float
    max_log_lines: int

    # ── 시스템 분석 지침 ───────────────────────────────────────────────────────
    system_analysis_guidelines: str

    # ── Stage 6 Reflection ────────────────────────────────────────────────────
    stage6_reflection_enabled: bool

    # ── Context Strategy ──────────────────────────────────────────────────────
    context_strategy: str           # "truncation" | "split" | "hybrid"
    hybrid_overflow_ratio: float    # hybrid: split 전환 임계 비율 (0.0~1.0)
    num_ctx: int | None             # 저장된 모델 컨텍스트 윈도우 크기 (토큰)

    # ── Observability ─────────────────────────────────────────────────────────
    observability_enabled: bool     # Stage별 상세 로그(analysis_logs) 기록 여부


# ── 로드 ─────────────────────────────────────────────────────────────────────

def _default_config() -> AppConfig:
    """config.yaml 이 없을 때 사용하는 기본 AppConfig."""
    llm_p = LLMProfile(
        name=_DEFAULT_LLM_NAME, base_url=_DEFAULT_OLLAMA_URL,
        api_key=_DEFAULT_OLLAMA_API_KEY, model=_DEFAULT_LLM_MODEL,
    )
    emb_p = LLMProfile(
        name=_DEFAULT_EMBED_NAME, base_url=_DEFAULT_OLLAMA_URL,
        api_key=_DEFAULT_OLLAMA_API_KEY, model=_DEFAULT_EMBED_MODEL,
    )
    return AppConfig(
        llm_base_url           = llm_p.base_url,
        llm_api_key            = llm_p.api_key,
        llm_model              = llm_p.model,
        llm_max_tokens         = None,
        llm_timeout            = None,
        llm_report_temperature = _DEFAULT_REPORT_TEMP,
        llm_provider           = "openai",
        llm_profiles           = [llm_p],
        active_llm             = llm_p.name,
        embed_base_url         = emb_p.base_url,
        embed_api_key          = emb_p.api_key,
        embed_model            = emb_p.model,
        embed_profiles         = [emb_p],
        active_embed           = emb_p.name,
        kb_threshold           = _DEFAULT_KB_THRESHOLD,
        definite_threshold     = _DEFAULT_DEFINITE_THRESHOLD,
        max_log_lines          = _DEFAULT_MAX_LOG_LINES,
        system_analysis_guidelines = DEFAULT_SYSTEM_ANALYSIS_GUIDELINES,
        stage6_reflection_enabled  = True,
        context_strategy       = _DEFAULT_CONTEXT_STRATEGY,
        hybrid_overflow_ratio  = _DEFAULT_HYBRID_RATIO,
        num_ctx                = None,
        observability_enabled  = False,
    )


def _load() -> AppConfig:
    if not _CONFIG_PATH.exists():
        return _default_config()

    with _CONFIG_PATH.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    pipeline = raw.get("pipeline", {})

    llm_profiles, active_llm = _load_profiles(
        raw,
        new_key        = "llm_profiles",
        legacy_key     = "llm",
        active_key     = "active_llm",
        default_name   = _DEFAULT_LLM_NAME,
        fallback_model = _DEFAULT_LLM_MODEL,
    )
    embed_profiles, active_embed = _load_profiles(
        raw,
        new_key        = "embed_profiles",
        legacy_key     = "embedding",
        active_key     = "active_embed",
        default_name   = _DEFAULT_EMBED_NAME,
        fallback_model = _DEFAULT_EMBED_MODEL,
    )

    active_llm_p   = _find_active(llm_profiles,   active_llm)   or llm_profiles[0]
    active_embed_p = _find_active(embed_profiles, active_embed) or embed_profiles[0]

    return AppConfig(
        llm_base_url           = active_llm_p.base_url,
        llm_api_key            = active_llm_p.api_key,
        llm_model              = active_llm_p.model,
        llm_max_tokens         = active_llm_p.max_tokens,
        llm_timeout            = active_llm_p.timeout,
        llm_report_temperature = active_llm_p.report_temperature,
        llm_provider           = active_llm_p.provider,
        llm_profiles           = llm_profiles,
        active_llm             = active_llm_p.name,
        embed_base_url         = active_embed_p.base_url,
        embed_api_key          = active_embed_p.api_key,
        embed_model            = active_embed_p.model,
        embed_profiles         = embed_profiles,
        active_embed           = active_embed_p.name,
        kb_threshold           = float(pipeline.get("kb_threshold",       _DEFAULT_KB_THRESHOLD)),
        definite_threshold     = float(pipeline.get("definite_threshold", _DEFAULT_DEFINITE_THRESHOLD)),
        max_log_lines          = int(pipeline.get("max_log_lines",        _DEFAULT_MAX_LOG_LINES)),
        system_analysis_guidelines = raw.get(
            "system_analysis_guidelines", DEFAULT_SYSTEM_ANALYSIS_GUIDELINES
        ),
        stage6_reflection_enabled = bool(pipeline.get("stage6_reflection_enabled", True)),
        context_strategy      = pipeline.get("context_strategy", _DEFAULT_CONTEXT_STRATEGY),
        hybrid_overflow_ratio = float(pipeline.get("hybrid_overflow_ratio", _DEFAULT_HYBRID_RATIO)),
        num_ctx               = int(pipeline["num_ctx"]) if pipeline.get("num_ctx") else None,
        observability_enabled = bool(pipeline.get("observability_enabled", False)),
    )


# ── 저장 ─────────────────────────────────────────────────────────────────────

def save(config: AppConfig) -> None:
    """AppConfig 를 config/LLM/config.yaml 에 저장한다."""
    data = {
        "system_analysis_guidelines": config.system_analysis_guidelines,
        "active_llm": config.active_llm,
        "llm_profiles": [
            {
                "name":               p.name,
                "provider":           p.provider,
                "base_url":           p.base_url,
                "api_key":            p.api_key,
                "model":              p.model,
                "max_tokens":         p.max_tokens,
                "timeout":            p.timeout,
                "report_temperature": p.report_temperature,
            }
            for p in config.llm_profiles
        ],
        "active_embed": config.active_embed,
        "embed_profiles": [
            {
                "name":     p.name,
                "base_url": p.base_url,
                "api_key":  p.api_key,
                "model":    p.model,
            }
            for p in config.embed_profiles
        ],
        "pipeline": {
            "kb_threshold":             config.kb_threshold,
            "definite_threshold":       config.definite_threshold,
            "max_log_lines":            config.max_log_lines,
            "stage6_reflection_enabled": config.stage6_reflection_enabled,
            "context_strategy":         config.context_strategy,
            "hybrid_overflow_ratio":    config.hybrid_overflow_ratio,
            "num_ctx":                  config.num_ctx,
            "observability_enabled":    config.observability_enabled,
        },
    }
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _CONFIG_PATH.open("w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)


# ── 헬퍼 ─────────────────────────────────────────────────────────────────────

def rebuild_from_active(
    config: AppConfig,
    profiles: list[LLMProfile],
    active_name: str,
) -> AppConfig:
    """LLM 프로파일 목록·활성 이름으로 AppConfig 의 llm_* 편의 필드를 재구성한다."""
    active = _find_active(profiles, active_name)
    if active is None:
        return config
    return replace(
        config,
        llm_base_url           = active.base_url,
        llm_api_key            = active.api_key,
        llm_model              = active.model,
        llm_max_tokens         = active.max_tokens,
        llm_timeout            = active.timeout,
        llm_report_temperature = active.report_temperature,
        llm_provider           = active.provider,
        llm_profiles           = list(profiles),
        active_llm             = active.name,
    )


def rebuild_embed_from_active(
    config: AppConfig,
    profiles: list[LLMProfile],
    active_name: str,
) -> AppConfig:
    """Embedding 프로파일 목록·활성 이름으로 AppConfig 의 embed_* 편의 필드를 재구성한다."""
    active = _find_active(profiles, active_name)
    if active is None:
        return config
    return replace(
        config,
        embed_base_url  = active.base_url,
        embed_api_key   = active.api_key,
        embed_model     = active.model,
        embed_profiles  = list(profiles),
        active_embed    = active.name,
    )


# 모듈 임포트 시 한 번 로드
cfg: AppConfig = _load()
