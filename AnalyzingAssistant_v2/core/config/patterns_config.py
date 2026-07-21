"""
core/config/patterns_config.py

config/patterns/default_patterns.yaml 의 raw 파싱만 담당한다.
"""

from __future__ import annotations

from pathlib import Path

from core.config._yaml_io import read

_DEFAULT_PATH: Path = Path(__file__).parent.parent.parent / "config" / "patterns" / "default_patterns.yaml"


def load(path: Path | None = None) -> dict:
    """patterns yaml 을 raw dict 로 반환. 파일 없으면 빈 dict."""
    yaml_path = Path(path) if path is not None else _DEFAULT_PATH
    return read(yaml_path) or {}
