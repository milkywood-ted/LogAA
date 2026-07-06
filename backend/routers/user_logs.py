import shutil
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from config import config

router = APIRouter()

USER_LOG_DIR = "user_added_log"


def _user_log_dir(defect_id: str) -> Path:
    return config.workspace() / defect_id / USER_LOG_DIR


def _require_defect(defect_id: str) -> Path:
    meta_path = config.workspace() / defect_id / "meta.json"
    if not meta_path.exists():
        raise HTTPException(status_code=404, detail=f"defect_id '{defect_id}' 를 찾을 수 없습니다.")
    return meta_path


class AddUserLogRequest(BaseModel):
    src_path: str  # 서버 내 원본 파일 경로


@router.post("/api/defect/{defect_id}/user-logs")
def add_user_log(defect_id: str, req: AddUserLogRequest):
    _require_defect(defect_id)

    src = Path(req.src_path)
    if not src.exists():
        raise HTTPException(status_code=400, detail=f"경로를 찾을 수 없습니다: {req.src_path}")

    dest_dir = _user_log_dir(defect_id)
    dest_dir.mkdir(parents=True, exist_ok=True)

    if src.is_file():
        dest = dest_dir / src.name
        shutil.copy2(src, dest)
        return {"copied": [src.name]}

    if src.is_dir():
        copied = []
        for file in sorted(src.rglob("*")):
            if not file.is_file():
                continue
            dest = dest_dir / file.name
            # 동명 파일 충돌 시 상대경로를 언더스코어로 연결하여 구분
            if dest.exists():
                rel = file.relative_to(src)
                dest = dest_dir / str(rel).replace("/", "_").replace("\\", "_")
            shutil.copy2(file, dest)
            copied.append(dest.name)
        return {"copied": copied}

    raise HTTPException(status_code=400, detail=f"파일 또는 폴더가 아닙니다: {req.src_path}")


@router.get("/api/defect/{defect_id}/user-logs")
def list_user_logs(defect_id: str):
    _require_defect(defect_id)

    dest_dir = _user_log_dir(defect_id)
    if not dest_dir.exists():
        return {"files": []}

    files = [
        {"filename": f.name, "path": str(f), "size": f.stat().st_size}
        for f in sorted(dest_dir.iterdir())
        if f.is_file()
    ]
    return {"files": files}


@router.delete("/api/defect/{defect_id}/user-logs/{filename}")
def delete_user_log(defect_id: str, filename: str):
    _require_defect(defect_id)

    target = _user_log_dir(defect_id) / filename
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"파일을 찾을 수 없습니다: {filename}")

    target.unlink()
    return {"deleted": filename}
