from pathlib import Path
from fastapi import APIRouter, HTTPException
from config import config

router = APIRouter()

EXCLUDE_NAMES = {"meta.json"}


def _require_defect(defect_id: str) -> Path:
    workspace_dir = config.workspace() / defect_id
    if not (workspace_dir / "meta.json").exists():
        raise HTTPException(status_code=404, detail=f"defect_id '{defect_id}' 를 찾을 수 없습니다.")
    return workspace_dir


def _file_entry(path: Path, relative_to: Path) -> dict:
    return {
        "filename": path.name,
        "path": str(path),
        "relative_path": str(path.relative_to(relative_to)),
        "size": path.stat().st_size,
    }


@router.get("/api/defect/{defect_id}/files")
def list_defect_files(defect_id: str):
    workspace_dir = _require_defect(defect_id)

    default_files = []
    comment_attachment_files = []
    user_added_files = []

    for f in sorted(workspace_dir.iterdir()):
        if not f.is_file():
            continue
        if f.name in EXCLUDE_NAMES:
            continue
        default_files.append(_file_entry(f, workspace_dir))

    comment_dir = workspace_dir / "CommentAttachment"
    if comment_dir.exists():
        for f in sorted(comment_dir.rglob("*")):
            if f.is_file():
                comment_attachment_files.append(_file_entry(f, workspace_dir))

    user_log_dir = workspace_dir / "user_added_log"
    if user_log_dir.exists():
        for f in sorted(user_log_dir.iterdir()):
            if f.is_file():
                user_added_files.append(_file_entry(f, workspace_dir))

    return {
        "defect_id": defect_id,
        "default": default_files,
        "comment_attachment": comment_attachment_files,
        "user_added": user_added_files,
    }
