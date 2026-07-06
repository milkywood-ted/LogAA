"""
core/config/llm_config.py

config/LLM/config.yaml 의 raw 파싱과 저장만 담당한다.
"""

from __future__ import annotations

from pathlib import Path

from core.config._yaml_io import read, write

_PATH: Path = Path(__file__).parent.parent.parent / "config" / "LLM" / "config.yaml"


def load() -> dict:
    """LLM/config.yaml 을 raw dict 로 반환. 파일 없으면 빈 dict."""
    return read(_PATH) or {}


def save(data: dict) -> None:
    """raw dict 를 LLM/config.yaml 에 저장한다."""
    write(_PATH, data)


def find_llm_profile(name: str) -> dict | None:
    """llm_profiles 에서 이름이 일치하는 프로파일 dict 를 반환. 없으면 None."""
    for p in load().get("llm_profiles", []):
        if p.get("name") == name:
            return p
    return None


def find_embed_profile(name: str) -> dict | None:
    """embed_profiles 에서 이름이 일치하는 프로파일 dict 를 반환. 없으면 None."""
    for p in load().get("embed_profiles", []):
        if p.get("name") == name:
            return p
    return None
