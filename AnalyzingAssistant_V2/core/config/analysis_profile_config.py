"""
core/config/analysis_profile_config.py

config/profiles/*.json 의 파일 I/O 만 담당한다.

도메인 로직 — Profile 객체 변환, 프로파일 병합(merge_profiles), MergedProfile 조립,
ChromaDB 사전지식 enrichment 등 — 은 본 모듈에서 다루지 않는다. 그 책임은 호출자
(현재는 `core/profile.py`) 에 있다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_DEFAULT_DIR: Path = Path(__file__).parent.parent.parent / "config" / "profiles"


def _dir(directory: Path | None) -> Path:
    return Path(directory) if directory is not None else _DEFAULT_DIR


def _name_to_stem(name: str) -> str:
    slug = name.strip().replace(" ", "_")
    slug = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", slug)
    return slug or "profile"


def _path_for(name: str, directory: Path | None) -> Path:
    return _dir(directory) / f"{_name_to_stem(name)}.json"


def _read_json(path: Path) -> dict | None:
    """단일 json 파일을 읽어 dict 로 반환. 실패 시 None."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _find_by_name(name: str, directory: Path | None) -> Path | None:
    """이름이 일치하는 파일의 경로를 반환. 없으면 None."""
    # 1차: stem 으로 직접 접근
    path = _path_for(name, directory)
    if path.exists():
        data = _read_json(path)
        if data is not None:
            return path
    # 2차: 전체 스캔
    for p in sorted(_dir(directory).glob("*.json")):
        data = _read_json(p)
        if data is not None and data.get("name") == name:
            return p
    return None


# ── 공개 API ─────────────────────────────────────────────────────────────────

def load_all(directory: Path | None = None) -> list[dict]:
    """디렉토리 내 모든 .json 을 raw dict 목록으로 반환 (이름순). 손상 파일은 스킵."""
    target = _dir(directory)
    if not target.exists():
        return []
    return [d for p in sorted(target.glob("*.json")) if (d := _read_json(p)) is not None]


def load_one(name: str, directory: Path | None = None) -> dict | None:
    """프로파일 이름으로 단일 raw dict 반환. 없으면 None."""
    path = _find_by_name(name, directory)
    if path is None:
        return None
    return _read_json(path)


def save(name: str, data: dict, directory: Path | None = None) -> Path:
    """프로파일 raw dict 를 JSON 파일로 저장한다. 반환값: 저장된 파일 경로."""
    target = _dir(directory)
    target.mkdir(parents=True, exist_ok=True)
    path = _path_for(name, directory)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def delete(name: str, directory: Path | None = None) -> bool:
    """프로파일 JSON 파일을 삭제한다. 반환값: 삭제 성공 여부."""
    path = _find_by_name(name, directory)
    if path is None:
        return False
    path.unlink()
    return True
