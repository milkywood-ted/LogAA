"""
core/config/_yaml_io.py

yaml 파일의 읽기/쓰기 공통 함수 + 인메모리 캐시.

- read: 캐시에서 반환. 최초 호출 시에만 파일에서 로드.
- write: 캐시 갱신 + 파일 쓰기 (flush).
- 파일 I/O 는 앱 시작 후 최초 1회 read + write 시에만 발생.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_cache: dict[Path, dict] = {}


def read(path: Path) -> dict | None:
    """yaml 파일을 읽어 dict 로 반환. 파일이 없으면 None.

    캐시에 있으면 파일을 읽지 않고 캐시에서 반환한다.
    """
    if path in _cache:
        return _cache[path]
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    _cache[path] = data
    return data


def write(path: Path, data: dict) -> None:
    """dict 를 yaml 파일에 저장하고 캐시를 갱신한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    verify(path, data)
    _cache[path] = data


def verify(path: Path, expected: dict) -> None:
    """파일에서 다시 읽어 expected 와 대조한다. 불일치 시 RuntimeError."""
    with path.open(encoding="utf-8") as f:
        written = yaml.safe_load(f) or {}
    if written != expected:
        raise RuntimeError(f"config write 검증 실패: {path} — 기록된 내용이 원본과 불일치")
