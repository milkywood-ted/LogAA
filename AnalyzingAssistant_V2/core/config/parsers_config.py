"""
core/config/parsers_config.py

config/log_parsers.yaml 의 raw 파싱만 담당한다.
"""

from __future__ import annotations

from pathlib import Path

from core.config._yaml_io import read

_PATH: Path = Path(__file__).parent.parent.parent / "config" / "log_parsers.yaml"


def load(path: Path | None = None) -> dict:
    """log_parsers.yaml 을 raw dict 로 반환. 파일 없으면 FileNotFoundError."""
    yaml_path = Path(path) if path is not None else _PATH
    result = read(yaml_path)
    if result is None:
        raise FileNotFoundError(f"파서 정의 파일을 찾을 수 없습니다: {yaml_path}")
    return result
