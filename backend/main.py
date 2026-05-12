import zipfile
import json
from datetime import datetime
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pathlib import Path
from puller_client import fetch_defect, list_defect_files, download_defect_file, load_pullers

app = FastAPI(title="LogAA API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

WORKSPACE = Path(__file__).parent / "workspace"

class FetchDefectRequest(BaseModel):
    puller_name: str
    defect_id: str

@app.get("/health")
def health():
    return {"status": "ok"}

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
