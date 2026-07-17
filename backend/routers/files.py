import json
from pathlib import Path
from fastapi import APIRouter, HTTPException
from config import config

router = APIRouter()

EXCLUDE_NAMES = {"meta.json"}
# 서브트리 처리 카테고리 — default 재귀 walk에서 제외한다(각자 별도 수집).
SUBTREE_DIRS = {"CommentAttachment", "user_added_log"}
# 분석 대상이 아닌 아카이브 — 압축해제된 실제 파일이 default에 들어가므로 목록에서 뺀다.
ARCHIVE_SUFFIXES = {".zip"}


def _require_defect(defect_id: str) -> Path:
    workspace_dir = config.workspace() / defect_id
    if not (workspace_dir / "meta.json").exists():
        raise HTTPException(status_code=404, detail=f"defect_id '{defect_id}' 를 찾을 수 없습니다.")
    return workspace_dir


def _load_archive_origins(workspace_dir: Path) -> dict:
    """meta.json 의 archive_origins(압축해제 파일 → 최상위 zip명) 맵을 로드한다."""
    try:
        with open(workspace_dir / "meta.json", encoding="utf-8") as f:
            return json.load(f).get("archive_origins") or {}
    except Exception:
        return {}


def _file_entry(path: Path, relative_to: Path, origins: dict) -> dict:
    rel = str(path.relative_to(relative_to))
    return {
        "filename": path.name,
        "path": str(path),
        "relative_path": rel,
        "size": path.stat().st_size,
        # 압축해제된 파일이면 최상위 원본 zip명, 아니면 None (파일 선택 UI 표기용).
        "archive": origins.get(rel),
    }


@router.get("/api/defect/{defect_id}/files")
def list_defect_files(defect_id: str):
    workspace_dir = _require_defect(defect_id)
    origins = _load_archive_origins(workspace_dir)

    default_files = []
    comment_attachment_files = []
    user_added_files = []

    # default: 서브트리(CommentAttachment/user_added_log)를 제외하고 재귀 walk.
    # zip 은 압축해제 결과가 이미 포함되므로 목록에서 뺀다.
    for f in sorted(workspace_dir.rglob("*")):
        if not f.is_file() or f.name in EXCLUDE_NAMES:
            continue
        if f.suffix.lower() in ARCHIVE_SUFFIXES:
            continue
        rel_parts = f.relative_to(workspace_dir).parts
        if rel_parts[0] in SUBTREE_DIRS:
            continue
        default_files.append(_file_entry(f, workspace_dir, origins))

    comment_dir = workspace_dir / "CommentAttachment"
    if comment_dir.exists():
        for f in sorted(comment_dir.rglob("*")):
            if f.is_file():
                comment_attachment_files.append(_file_entry(f, workspace_dir, origins))

    user_log_dir = workspace_dir / "user_added_log"
    if user_log_dir.exists():
        for f in sorted(user_log_dir.iterdir()):
            if f.is_file():
                user_added_files.append(_file_entry(f, workspace_dir, origins))

    return {
        "defect_id": defect_id,
        "default": default_files,
        "comment_attachment": comment_attachment_files,
        "user_added": user_added_files,
    }
