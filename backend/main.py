import zipfile
import json
from datetime import datetime
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator
from pathlib import Path
from puller_client import fetch_defect, list_defect_files, download_defect_file, load_pullers
from AnalyzingAssistant_client import (  # 🆕
    analyze,
    get_llm_profiles,
    get_llm_models,
    check_llm_connection,
    get_llm_config,
    save_llm_config,
    get_embedding_profiles,
    get_embedding_models,
    check_embedding_connection,
    get_embedding_config,
    save_embedding_config,
)

app = FastAPI(title="LogAA API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

WORKSPACE = Path(__file__).parent / "workspace"


# ── Request 모델 ──────────────────────────────────────────────────────────────

class FetchDefectRequest(BaseModel):
    puller_name: str
    defect_id: str


class AnalyzeRequest(BaseModel):
    defect_id: str
    profile_names: list[str] = []
    pinned_case_name: str | None = None
    parser_names: list[str] = []
    input_keywords: list[str] = []
    anchors: list[str] = []


class LLMConfigSaveRequest(BaseModel):
    profile: str
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
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


class EmbeddingConfigSaveRequest(BaseModel):
    profile: str
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None


class ConnectionCheckRequest(BaseModel):
    profile: str
    model: str | None = None


# ── 기본 ──────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


# ── Puller ────────────────────────────────────────────────────────────────────

@app.get("/api/pullers")
def get_pullers():
    pullers = load_pullers()
    return {"pullers": [{"name": p["name"]} for p in pullers]}


@app.get("/api/cases")
def get_cases():
    if not WORKSPACE.exists():
        return {"cases": []}
    cases = []
    for folder in WORKSPACE.iterdir():
        if not folder.is_dir():
            continue
        meta_path = folder / "meta.json"
        if not meta_path.exists():
            continue
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
        cases.append(meta)
    cases.sort(key=lambda x: x.get("fetchedAt", ""), reverse=True)
    return {"cases": cases[:10]}


@app.post("/api/defect/fetch")
async def defect_fetch(req: FetchDefectRequest):
    result = await fetch_defect(req.puller_name, req.defect_id)
    if not result.get("success"):
        raise HTTPException(status_code=502, detail=result.get("error", "Puller 오류"))

    save_dir = WORKSPACE / req.defect_id
    save_dir.mkdir(parents=True, exist_ok=True)

    files = await list_defect_files(req.puller_name, req.defect_id)
    downloaded = []
    for file_info in files:
        filename = file_info["filename"]
        file_path = await download_defect_file(
            req.puller_name, req.defect_id, filename, save_dir
        )
        if zipfile.is_zipfile(file_path):
            with zipfile.ZipFile(file_path) as zf:
                zf.extractall(save_dir)
        downloaded.append(filename)

    description = result.get("data", {}).get("texts", {})
    meta = {
        "id": req.defect_id,
        "puller": req.puller_name,
        "title": result.get("data", {}).get("title", req.defect_id),
        "description": description,
        "files": downloaded,
        "fetchedAt": datetime.now().isoformat(),
        "workspace": str(save_dir),
    }
    with open(save_dir / "meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    return {"success": True, **meta}


# ── AnalyzingAssistant - 분석 ─────────────────────────────────────────────────

@app.post("/api/defect/analyze")
async def defect_analyze(req: AnalyzeRequest):
    meta_path = WORKSPACE / req.defect_id / "meta.json"
    if not meta_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"defect_id '{req.defect_id}' 를 찾을 수 없습니다. 먼저 /api/defect/fetch 를 호출하세요.",
        )

    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)

    description = meta.get("description", {})
    if isinstance(description, dict):
        problem_text = "\n".join(f"{k}: {v}" for k, v in description.items() if v)
    else:
        problem_text = str(description)

    if not problem_text.strip():
        problem_text = meta.get("title", req.defect_id)

    log_paths = [meta["workspace"]]

    try:
        result = await analyze(
            problem_text=problem_text,
            log_paths=log_paths,
            profile_names=req.profile_names,
            pinned_case_name=req.pinned_case_name,
            parser_names=req.parser_names,
            input_keywords=req.input_keywords,
            anchors=req.anchors,
        )
    except TimeoutError as e:
        raise HTTPException(status_code=504, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    return {"success": True, "defect_id": req.defect_id, "analysis": result}


# ── AnalyzingAssistant - Settings / LLM ──────────────────────────────────────

@app.get("/api/settings/llm/profiles")
async def settings_llm_profiles():
    data = await get_llm_profiles()
    return {"profiles": [{"name": p} for p in data]}


@app.get("/api/settings/llm/models")
async def settings_llm_models(profile: str):
    data = await get_llm_models(profile)
    return {"models": data}


@app.post("/api/settings/llm/check")
async def settings_llm_check(req: ConnectionCheckRequest):
    data = await check_llm_connection(req.profile, req.model)
    return {"connected": data.get("ok", False), "detail": data.get("detail", "")}


@app.get("/api/settings/llm/config")
async def settings_llm_config(profile: str):
    return await get_llm_config(profile)


@app.post("/api/settings/llm/config")
async def settings_llm_config_save(req: LLMConfigSaveRequest):
    return await save_llm_config(req.profile, req.model_dump(exclude={"profile"}, exclude_none=True))


# ── AnalyzingAssistant - Settings / Embedding ─────────────────────────────────

@app.get("/api/settings/embedding/profiles")
async def settings_embedding_profiles():
    data = await get_embedding_profiles()
    return {"profiles": [{"name": p} for p in data]}


@app.get("/api/settings/embedding/models")
async def settings_embedding_models(profile: str):
    data = await get_embedding_models(profile)
    return {"models": data}


@app.post("/api/settings/embedding/check")
async def settings_embedding_check(req: ConnectionCheckRequest):
    data = await check_embedding_connection(req.profile, req.model)
    return {"connected": data.get("ok", False), "detail": data.get("detail", "")}


@app.get("/api/settings/embedding/config")
async def settings_embedding_config(profile: str):
    return await get_embedding_config(profile)


@app.post("/api/settings/embedding/config")
async def settings_embedding_config_save(req: EmbeddingConfigSaveRequest):
    return await save_embedding_config(req.profile, req.model_dump(exclude={"profile"}, exclude_none=True))
