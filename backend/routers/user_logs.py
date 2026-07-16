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


def _within_roots(path: Path, roots: list[Path]) -> bool:
    """resolve()된 경로가 허용 루트 중 하나의 하위(또는 루트 자신)인지 확인한다."""
    return any(path == root or path.is_relative_to(root) for root in roots)


def _require_allowed_src(raw_path: str) -> Path:
    """src_path를 허용 루트(config user_log_roots) 경계 안에서만 받아들인다.

    - resolve()로 심볼릭 링크를 해소한 실제 경로 기준으로 검사한다.
    - 경계 검사를 존재 확인보다 먼저 수행해, 경계 밖 경로는 존재 여부조차
      노출하지 않는다.
    """
    roots = config.user_log_roots()
    if not roots:
        raise HTTPException(
            status_code=403,
            detail="user_log_roots 가 설정되지 않았습니다. backend config.yaml 에 허용 루트를 등록하세요.",
        )
    src = Path(raw_path).expanduser().resolve()
    if not _within_roots(src, roots):
        raise HTTPException(status_code=403, detail=f"허용된 경로가 아닙니다: {raw_path}")
    return src


class AddUserLogRequest(BaseModel):
    src_path: str  # 서버 내 원본 파일 경로


@router.post("/api/defect/{defect_id}/user-logs")
def add_user_log(defect_id: str, req: AddUserLogRequest):
    _require_defect(defect_id)

    src = _require_allowed_src(req.src_path)
    if not src.exists():
        raise HTTPException(status_code=400, detail=f"경로를 찾을 수 없습니다: {req.src_path}")

    dest_dir = _user_log_dir(defect_id)
    dest_dir.mkdir(parents=True, exist_ok=True)

    if src.is_file():
        dest = dest_dir / src.name
        shutil.copy2(src, dest)
        return {"copied": [src.name], "skipped": []}

    if src.is_dir():
        roots = config.user_log_roots()
        copied = []
        skipped = []
        for file in sorted(src.rglob("*")):
            if not file.is_file():
                continue
            # 폴더 안 심볼릭 링크가 허용 루트 밖을 가리키면 제외
            if not _within_roots(file.resolve(), roots):
                skipped.append(file.name)
                continue
            dest = dest_dir / file.name
            # 동명 파일 충돌 시 상대경로를 언더스코어로 연결하여 구분
            if dest.exists():
                rel = file.relative_to(src)
                dest = dest_dir / str(rel).replace("/", "_").replace("\\", "_")
            shutil.copy2(file, dest)
            copied.append(dest.name)
        return {"copied": copied, "skipped": skipped}

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

    # filename의 경로 조작('..', 인코딩된 구분자 등)이 user_added_log/ 밖을
    # 가리키지 않도록 resolve()된 실제 경로로 경계를 검사한다.
    base = _user_log_dir(defect_id).resolve()
    target = (base / filename).resolve()
    if target == base or not target.is_relative_to(base):
        raise HTTPException(status_code=400, detail=f"잘못된 파일명입니다: {filename}")

    if not target.exists():
        raise HTTPException(status_code=404, detail=f"파일을 찾을 수 없습니다: {filename}")

    target.unlink()
    return {"deleted": filename}
