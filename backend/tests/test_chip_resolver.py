"""chip_resolver.py — SW Version → 칩 해석 + resolve_meta 메모리 보정 (§9-6 검증 정식화).

임시 YAML 매핑으로 격리한다 — 실제 config/sw_version_chip_map.yaml 은 건드리지 않는다.
"""

from pathlib import Path

import pytest

import chip_resolver


@pytest.fixture
def mapped(tmp_path, monkeypatch):
    """임시 매핑 테이블로 교체하고 캐시를 비운다."""
    yaml_path = tmp_path / "map.yaml"
    yaml_path.write_text(
        "mappings:\n"
        "  - pattern: rhea\n"
        "    chip: [RheaM]\n"
        "  - pattern: rose\n"
        "    chip: [RoseM, RoseP]\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(chip_resolver, "_MAP_PATH", yaml_path)
    chip_resolver.reload()
    yield
    chip_resolver.reload()   # 다른 테스트에 캐시 누수 방지


def test_resolve_hit_and_miss(mapped):
    assert chip_resolver.resolve("T-RHEAM-1.0") == ["RheaM"]
    assert chip_resolver.resolve("T-ROSE-2.0") == ["RoseM", "RoseP"]
    assert chip_resolver.resolve("UNKNOWN-9.9") is None
    assert chip_resolver.resolve("") is None


def test_resolve_case_insensitive(mapped):
    assert chip_resolver.resolve("xx-rhea-yy") == ["RheaM"]


def test_resolve_meta_fills_from_sw_version(mapped):
    meta = {"id": "D-1", "description": {"SW_Version": "T-RHEAM-1.0"}}
    out = chip_resolver.resolve_meta(meta)
    assert out["chip"] == ["RheaM"]
    assert out["sw_version"] == "T-RHEAM-1.0"


def test_resolve_meta_keeps_existing_chip(mapped):
    meta = {"chip": ["Old"], "sw_version": "T-RHEAM-1.0"}
    assert chip_resolver.resolve_meta(meta)["chip"] == ["Old"]   # 재계산 안 함


def test_resolve_meta_safe_on_string_description(mapped):
    # description 이 dict 가 아닌 문자열이어도 크래시하지 않는다
    meta = {"id": "D-2", "description": "자유 서술"}
    assert chip_resolver.resolve_meta(meta).get("chip") is None


def test_resolve_meta_is_pure_no_file_write(mapped, tmp_path):
    # resolve_meta 는 meta dict 만 갱신하고 파일 IO 를 하지 않는다 (§9-6 핵심)
    meta = {"description": {"SW_Version": "T-RHEAM-1.0"}}
    chip_resolver.resolve_meta(meta)
    # 매핑 YAML 외에 tmp 에 새 파일이 생기지 않음
    assert sorted(p.name for p in tmp_path.iterdir()) == ["map.yaml"]
