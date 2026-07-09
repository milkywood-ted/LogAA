"""
core/parser_registry.py

config/log_parsers.yaml 을 읽어 로그 파서를 등록·제공한다.

외부에서 사용하는 심볼:
  PARSER_DEFS : list[dict]  — UI 체크박스 생성용 메타데이터 (name, label, description, default)
  PARSER_MAP  : dict[str, tuple] — LogRefiner 가 사용하는 (pattern, cpu_id_group, ts_group, msg_group, use_search)
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

_PARSERS_YAML = Path(__file__).parent.parent / "config" / "log_parsers.yaml"

# re 플래그 이름 → 값 매핑
_FLAG_MAP: dict[str, int] = {
    "DOTALL":     re.DOTALL,
    "IGNORECASE": re.IGNORECASE,
    "MULTILINE":  re.MULTILINE,
    "VERBOSE":    re.VERBOSE,
}


def _compile_flags(flag_names: list[str]) -> int:
    flags = 0
    for name in flag_names:
        flags |= _FLAG_MAP.get(name.upper(), 0)
    return flags


def _load() -> tuple[list[dict], dict[str, tuple]]:
    if not _PARSERS_YAML.exists():
        raise FileNotFoundError(f"파서 정의 파일을 찾을 수 없습니다: {_PARSERS_YAML}")

    with _PARSERS_YAML.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    defs: list[dict] = []
    pmap: dict[str, tuple] = {}

    for entry in raw.get("parsers", []):
        name = entry["name"]
        compiled = re.compile(entry["pattern"], _compile_flags(entry.get("flags", [])))

        defs.append({
            "name":        name,
            "label":       entry["label"],
            "description": entry.get("description", "").strip(),
            "default":     bool(entry.get("default", False)),
        })
        pmap[name] = (
            compiled,
            entry.get("cpu_id_group"),
            entry.get("ts_group"),
            entry["msg_group"],
            bool(entry.get("use_search", False)),
        )

    return defs, pmap


# 모듈 임포트 시 한 번만 로드
PARSER_DEFS, PARSER_MAP = _load()

# 기본 활성 파서 이름 목록
DEFAULT_ACTIVE_PARSERS: list[str] = [d["name"] for d in PARSER_DEFS if d["default"]]


class _ParserEntry:
    __slots__ = ("name", "label", "description", "default")

    def __init__(self, d: dict) -> None:
        self.name: str = d["name"]
        self.label: str = d["label"]
        self.description: str = d.get("description", "")
        self.default: bool = bool(d.get("default", False))


class ParserRegistry:
    def __init__(self, parsers: list[_ParserEntry]) -> None:
        self.parsers = parsers

    @classmethod
    def from_yaml(cls, path: Path) -> "ParserRegistry":
        if not Path(path).exists():
            raise FileNotFoundError(f"파서 정의 파일을 찾을 수 없습니다: {path}")
        defs, _ = _load()
        return cls([_ParserEntry(d) for d in defs])
