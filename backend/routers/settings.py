from fastapi import APIRouter
from pydantic import BaseModel, field_validator
from AnalyzingAssistant_client import aa_client
from routers._errors import propagate_aa_errors
from chip_resolver import _load_mappings

router = APIRouter()


class ProviderConfigBase(BaseModel):
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None


class LLMConfigSaveRequest(ProviderConfigBase):
    profile: str
    max_tokens: int | None = None
    timeout: float | None = None
    report_temperature: float | None = None
    provider: str | None = None

    @field_validator("max_tokens", "timeout", "report_temperature", "base_url", "api_key", "model", "provider", mode="before")
    @classmethod
    def empty_str_to_none(cls, v):
        if v == "":
            return None
        return v


class EmbeddingConfigSaveRequest(ProviderConfigBase):
    profile: str


class ConnectionCheckRequest(BaseModel):
    profile: str
    model: str | None = None


class RerankerConfigSaveRequest(BaseModel):
    reranker_llm: str | None = None
    reranker_fallback_llm: str | None = None


class PipelineConfigSaveRequest(BaseModel):
    kb_threshold: float | None = None
    definite_threshold: float | None = None
    max_log_lines: int | None = None
    stage6_reflection_enabled: bool | None = None
    context_strategy: str | None = None
    hybrid_overflow_ratio: float | None = None
    num_ctx: int | None = None
    observability_enabled: bool | None = None
    unknown_refine_mode: str | None = None
    chip_match_mode: str | None = None


class ServerConfigSaveRequest(BaseModel):
    max_workers: int | None = None


class GuidelinesSaveRequest(BaseModel):
    value: str


# ── Guidelines ────────────────────────────────────────────────────────────────

@router.get("/api/settings/guidelines")
async def settings_guidelines():
    with propagate_aa_errors():
        return await aa_client.get_guidelines()


@router.post("/api/settings/guidelines")
async def settings_guidelines_save(req: GuidelinesSaveRequest):
    with propagate_aa_errors():
        return await aa_client.save_guidelines(req.value)


# ── Pipeline ──────────────────────────────────────────────────────────────────

@router.get("/api/settings/pipeline/num_ctx")
async def settings_pipeline_num_ctx():
    with propagate_aa_errors():
        return await aa_client.query_num_ctx()


@router.get("/api/settings/pipeline/config")
async def settings_pipeline_config():
    with propagate_aa_errors():
        return await aa_client.get_pipeline_config()


@router.post("/api/settings/pipeline/config")
async def settings_pipeline_config_save(req: PipelineConfigSaveRequest):
    with propagate_aa_errors():
        return await aa_client.save_pipeline_config(req.model_dump(exclude_none=True))


# ── Server ────────────────────────────────────────────────────────────────────

@router.get("/api/settings/server/config")
async def settings_server_config():
    with propagate_aa_errors():
        return await aa_client.get_server_config()


@router.post("/api/settings/server/config")
async def settings_server_config_save(req: ServerConfigSaveRequest):
    with propagate_aa_errors():
        return await aa_client.save_server_config(req.model_dump(exclude_none=True))


# ── Active ────────────────────────────────────────────────────────────────────

@router.get("/api/settings/active")
async def settings_active():
    with propagate_aa_errors():
        return await aa_client.get_active_profiles()


# ── LLM ───────────────────────────────────────────────────────────────────────

@router.get("/api/settings/llm/profiles")
async def settings_llm_profiles():
    with propagate_aa_errors():
        data = await aa_client.get_llm_profiles()
    return {"profiles": [{"name": p} for p in data]}


@router.get("/api/settings/llm/models")
async def settings_llm_models(profile: str):
    with propagate_aa_errors():
        data = await aa_client.get_llm_models(profile)
    return {"models": data}


@router.post("/api/settings/llm/check")
async def settings_llm_check(req: ConnectionCheckRequest):
    with propagate_aa_errors():
        data = await aa_client.check_llm_connection(req.profile, req.model)
    return {"connected": data.get("ok", False), "detail": data.get("detail", "")}


@router.get("/api/settings/llm/config")
async def settings_llm_config(profile: str):
    with propagate_aa_errors():
        return await aa_client.get_llm_config(profile)


@router.post("/api/settings/llm/config")
async def settings_llm_config_save(req: LLMConfigSaveRequest):
    with propagate_aa_errors():
        return await aa_client.save_llm_config(req.profile, req.model_dump(exclude={"profile"}, exclude_none=True))


# ── Reranker ──────────────────────────────────────────────────────────────────

@router.get("/api/settings/reranker/config")
async def settings_reranker_config():
    with propagate_aa_errors():
        return await aa_client.get_reranker_config()


@router.post("/api/settings/reranker/config")
async def settings_reranker_config_save(req: RerankerConfigSaveRequest):
    with propagate_aa_errors():
        return await aa_client.save_reranker_config(req.model_dump())


# ── Embedding ─────────────────────────────────────────────────────────────────

@router.get("/api/settings/embedding/profiles")
async def settings_embedding_profiles():
    with propagate_aa_errors():
        data = await aa_client.get_embedding_profiles()
    return {"profiles": [{"name": p} for p in data]}


@router.get("/api/settings/embedding/models")
async def settings_embedding_models(profile: str):
    with propagate_aa_errors():
        data = await aa_client.get_embedding_models(profile)
    return {"models": data}


@router.post("/api/settings/embedding/check")
async def settings_embedding_check(req: ConnectionCheckRequest):
    with propagate_aa_errors():
        data = await aa_client.check_embedding_connection(req.profile, req.model)
    return {"connected": data.get("ok", False), "detail": data.get("detail", "")}


@router.get("/api/settings/embedding/config")
async def settings_embedding_config(profile: str):
    with propagate_aa_errors():
        return await aa_client.get_embedding_config(profile)


@router.post("/api/settings/embedding/config")
async def settings_embedding_config_save(req: EmbeddingConfigSaveRequest):
    with propagate_aa_errors():
        return await aa_client.save_embedding_config(req.profile, req.model_dump(exclude={"profile"}, exclude_none=True))


@router.get("/api/settings/chips")
def settings_chips():
    """sw_version_chip_map.yaml 에서 전체 칩 이름 목록을 반환한다."""
    chips = []
    seen = set()
    for entry in _load_mappings():
        for chip in (entry.get("chip") or []):
            if chip not in seen:
                seen.add(chip)
                chips.append(chip)
    return {"chips": chips}
