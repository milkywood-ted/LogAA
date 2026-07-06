import zipfile
import json
from datetime import datetime
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from config import config
from puller_client import fetch_defect, list_defect_files, download_defect_file, list_comment_attachments, download_comment_attachment_file
from chip_resolver import resolve as resolve_chip

router = APIRouter()


class Credentials(BaseModel):
    id: str
    pw: str


class FetchDefectRequest(BaseModel):
    puller_name: str
    defect_id: str
    credentials: Credentials | None = None


@router.get("/api/pullers")
def get_pullers():
    pullers = config.pullers()
    return {"pullers": [{"name": p["name"]} for p in pullers]}


@router.get("/api/defects")
def get_defects():
    if not config.workspace().exists():
        return {"defects": []}
    defects = []
    for folder in config.workspace().iterdir():
        if not folder.is_dir():
            continue
        meta_path = folder / "meta.json"
        if not meta_path.exists():
            continue
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
        meta = _ensure_chip(meta, meta_path)
        defects.append(meta)
    defects.sort(key=lambda x: x.get("fetchedAt", ""), reverse=True)
    return {"defects": defects[:10]}


def _ensure_chip(meta: dict, meta_path) -> dict:
    """chip 필드가 없고 sw_version이 있으면 resolve해서 meta.json을 갱신한다."""
    if meta.get("chip") is not None:
        return meta
    sw_version = meta.get("sw_version") or meta.get("description", {}).get("SW_Version")
    if not sw_version:
        return meta
    chip = resolve_chip(sw_version)
    meta["sw_version"] = sw_version
    meta["chip"] = chip
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return meta


@router.post("/api/defect/fetch")
async def defect_fetch(req: FetchDefectRequest):
    credentials = req.credentials.model_dump() if req.credentials else None
    result = await fetch_defect(req.puller_name, req.defect_id, credentials)
    if not result.get("success"):
        raise HTTPException(status_code=502, detail=result.get("error", "Puller 오류"))

    save_dir = config.workspace() / req.defect_id
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

    data = result.get("data", {})

    try:
        all_items = await list_comment_attachments(req.puller_name, req.defect_id)
        comment_attachment_items = [item for item in all_items if item.get("files")]
        for item in comment_attachment_items:
            index = item["index"]
            comment_save_dir = save_dir / "CommentAttachment" / str(index)
            for file_info in item.get("files", []):
                if file_info.get("downloaded") and not file_info.get("error"):
                    await download_comment_attachment_file(
                        req.puller_name, req.defect_id, index, file_info["filename"], comment_save_dir
                    )
    except Exception:
        comment_attachment_items = data.get("comment_attachment_items", [])

    texts = data.get("texts", {})
    sw_version = texts.get("SW_Version")
    chip = resolve_chip(sw_version) if sw_version else None

    meta = {
        "id": req.defect_id,
        "puller": req.puller_name,
        "title": data.get("title", req.defect_id),
        "description": texts,
        "sw_version": sw_version,
        "chip": chip,
        "comment_attachment_items": comment_attachment_items,
        "files": downloaded,
        "fetchedAt": datetime.now().isoformat(),
        "workspace": str(save_dir),
    }
    with open(save_dir / "meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    return {"success": True, **meta}
