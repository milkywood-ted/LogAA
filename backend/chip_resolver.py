"""
chip_resolver.py

SW Version 문자열에서 칩 정보를 추출한다.

매핑 테이블: backend/config/sw_version_chip_map.yaml
  - pattern이 SW Version 문자열에 포함되면 해당 chip으로 인식
  - 목록 순서대로 매칭, 첫 번째 히트 반환
  - 매칭 없으면 None 반환
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

_MAP_PATH = Path(__file__).parent / "config" / "sw_version_chip_map.yaml"


@lru_cache(maxsize=1)
def _load_mappings() -> list[dict]:
    """YAML 매핑 테이블을 로드한다. 프로세스 내 최초 1회만 읽는다."""
    if not _MAP_PATH.exists():
        return []
    with open(_MAP_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("mappings") or []


def resolve(sw_version: str) -> list[str] | None:
    """
    SW Version 문자열에서 칩 정보를 추출한다.

    Parameters
    ----------
    sw_version : Puller로부터 받은 SW Version 문자열

    Returns
    -------
    칩 식별자 목록, 매핑 없으면 None
    """
    if not sw_version:
        return None
    sw_lower = sw_version.lower()
    for entry in _load_mappings():
        pattern = entry.get("pattern", "")
        if not pattern or pattern.lower() not in sw_lower:
            continue
        chip = entry.get("chip")
        if chip is None:
            return None
        # 단일 문자열도 리스트로 통일
        return chip if isinstance(chip, list) else [chip]
    return None


def reload() -> None:
    """매핑 테이블 캐시를 초기화한다. YAML 수정 후 재로드 시 사용."""
    _load_mappings.cache_clear()
