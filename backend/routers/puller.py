import zipfile
import json
import logging
from datetime import datetime
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from config import config
from puller_client import fetch_defect, list_defect_files, download_defect_file, list_comment_attachments, download_comment_attachment_file
from chip_resolver import resolve as resolve_chip, resolve_meta

router = APIRouter()
logger = logging.getLogger(__name__)

# zip 해제 상한 — zip bomb(소형 zip이 대량으로 팽창)으로 인한 디스크 고갈 방지
MAX_EXTRACT_BYTES = 10 * 1024 ** 3   # 10GB
MAX_EXTRACT_FILES = 10_000


def _extract_archives_recursive(save_dir: Path) -> dict[str, str]:
    """save_dir 안의 zip을 **재귀적으로**(중첩 zip 포함) 모두 해제한다.

    각 zip은 자신이 있는 폴더에 풀리며, 처리 후 원본 zip은 삭제하지 않는다(비파괴).

    안전장치:
    - 총량(MAX_EXTRACT_BYTES)·개수(MAX_EXTRACT_FILES) 상한을 **재귀 전체에 누적**
      적용해 zip bomb(중첩 팽창 포함)을 막는다.
    - 엔트리 경로를 resolve한 결과가 save_dir 루트 밖이면 skip(zip slip 심층 방어).
    - 처리한 zip을 resolve 경로로 기록해 무한 루프(자기참조 등)를 방지한다.

    Returns
    -------
    {압축해제 파일의 save_dir 기준 상대경로: 최상위 원본 zip 파일명} 맵.
    중첩(outer.zip→inner.zip→deep.log)이면 deep.log 의 값은 **최상위** outer.zip.
    """
    root = save_dir.resolve()
    origins: dict[str, str] = {}     # 상대경로 → 최상위 zip 파일명
    processed: set[Path] = set()     # 이미 해제한 zip (resolve 경로)
    total_bytes = 0
    total_files = 0
    stopped = False

    while not stopped:
        pending = [
            p for p in sorted(save_dir.rglob("*"))
            if p.is_file() and p.suffix.lower() == ".zip"
            and p.resolve() not in processed and zipfile.is_zipfile(p)
        ]
        if not pending:
            break

        for zp in pending:
            processed.add(zp.resolve())
            dest = zp.parent.resolve()
            # 이 zip 자신이 다른 zip에서 나왔으면 그 최상위 출처를 물려받고,
            # 아니면(직접 다운로드된 최상위 zip) 자기 이름이 출처가 된다.
            rel_zip = str(zp.resolve().relative_to(root))
            top_source = origins.get(rel_zip, zp.name)

            try:
                with zipfile.ZipFile(zp) as zf:
                    for info in zf.infolist():
                        if info.is_dir():
                            continue
                        total_files += 1
                        if total_files > MAX_EXTRACT_FILES:
                            logger.warning("압축 해제 중단: 파일 개수 상한(%d개) 초과", MAX_EXTRACT_FILES)
                            stopped = True
                            break
                        total_bytes += info.file_size
                        if total_bytes > MAX_EXTRACT_BYTES:
                            logger.warning("압축 해제 중단: 총량 상한(%d bytes) 초과", MAX_EXTRACT_BYTES)
                            stopped = True
                            break
                        target = (dest / info.filename).resolve()
                        if not target.is_relative_to(root):
                            logger.warning("zip slip 의심 엔트리 skip: %r", info.filename)
                            continue
                        zf.extract(info, dest)
                        origins[str(target.relative_to(root))] = top_source
            except zipfile.BadZipFile:
                logger.warning("손상된 zip skip: %s", zp.name)
                continue
            if stopped:
                break

    return origins


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
        # chip 미보정 레거시 defect는 응답에서만 메모리 보정 (GET은 파일을 쓰지 않음, §9-6)
        defects.append(resolve_meta(meta))
    defects.sort(key=lambda x: x.get("fetchedAt", ""), reverse=True)
    return {"defects": defects[:20]}


@router.get("/api/defects/{defect_id}")
def get_defect(defect_id: str):
    meta_path = config.workspace() / defect_id / "meta.json"
    if not meta_path.exists():
        return {"exists": False, "defect": None}
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)
    return {"exists": True, "defect": resolve_meta(meta)}


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
        await download_defect_file(
            req.puller_name, req.defect_id, filename, save_dir
        )
        downloaded.append(filename)

    # 다운로드 완료 후 zip을 재귀적으로 해제한다 (중첩 zip 포함).
    # archive_origins: 압축해제 파일 → 최상위 원본 zip명 (파일 선택 UI 표기용).
    archive_origins = _extract_archives_recursive(save_dir)

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
        "archive_origins": archive_origins,
        "fetchedAt": datetime.now().isoformat(),
        "workspace": str(save_dir),
    }
    with open(save_dir / "meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    return {"success": True, **meta}
