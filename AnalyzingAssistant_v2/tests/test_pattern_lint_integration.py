"""정규식 린트가 패턴 유입 3경로에 연결되어 있는지 검증.

경로별로 사람의 개입 시점이 달라 처리도 다르다:
  API      — 경고를 돌려주고, 사용자가 확인하면(confirm_warnings) 저장한다
  YAML seed — 확인해 줄 사람이 없으므로 경고를 오류로 승격한다
  LLM 생성  — 생성 시점엔 사람이 없으므로 경고를 결과에 담아 검토 화면에 넘긴다
"""

import json

import pytest
from fastapi import HTTPException

import api.router.patterns as pm
import core.pattern_generator as pg
import core.pattern_seeder as ps


def _req(**kw) -> pm.PatternSaveRequest:
    kw.setdefault("name", "P")
    kw.setdefault("type", "PRESENCE")
    return pm.PatternSaveRequest(**kw)


# ── API 경로 ─────────────────────────────────────────────────────────────────

def test_clean_pattern_passes_with_no_warnings():
    assert pm._check_pattern_syntax(_req(pattern="ata.*: EH complete")) == []


def test_compile_failure_is_blocked_even_when_confirmed():
    """컴파일 실패는 확정적 오류 — 확인으로 넘길 수 없다."""
    with pytest.raises(HTTPException) as e:
        pm._check_pattern_syntax(_req(pattern="*ERROR* commit failed", confirm_warnings=True))
    assert e.value.status_code == 400
    assert e.value.detail["code"] == "PATTERN_LINT_ERROR"


def test_warning_blocks_until_confirmed():
    with pytest.raises(HTTPException) as e:
        pm._check_pattern_syntax(_req(pattern="[drm] init failed"))
    assert e.value.status_code == 400
    assert e.value.detail["code"] == "PATTERN_LINT_WARNING"
    assert e.value.detail["issues"][0]["rule"] == "taglike_class"
    assert e.value.detail["issues"][0]["field"] == "pattern"


def test_confirmed_warning_is_saved_and_returned():
    warnings = pm._check_pattern_syntax(
        _req(pattern="[drm] init failed", confirm_warnings=True)
    )
    assert [w["rule"] for w in warnings] == ["taglike_class"]


def test_all_regex_fields_are_checked():
    with pytest.raises(HTTPException) as e:
        pm._check_pattern_syntax(_req(
            type="SEQUENCE",
            steps=["exception Emask", "mmc0: error (retry)"],
        ))
    assert e.value.detail["issues"][0]["field"] == "steps[1]"

    with pytest.raises(HTTPException) as e:
        pm._check_pattern_syntax(_req(
            type="ABSENCE",
            trigger_pattern="ata.*: EH in port_reset",
            absent_pattern="[drm] done",
        ))
    assert e.value.detail["issues"][0]["field"] == "absent_pattern"


def test_composite_without_regex_fields_is_clean():
    assert pm._check_pattern_syntax(
        _req(type="COMPOSITE", operator="AND", components=["A", "B"])
    ) == []


# ── /lint 미리보기 엔드포인트 ────────────────────────────────────────────────

def test_lint_endpoint_reports_issue_and_span():
    out = pm.lint_pattern_text(pm.PatternLintRequest(pattern="[drm] init failed"))
    assert out["escaped"] == "[drm] init failed"        # 아무것도 지정 안 하면 원본
    assert out["issues"][0]["rule"] == "taglike_class"


def test_lint_endpoint_partial_escape_resolves_only_selected():
    out = pm.lint_pattern_text(pm.PatternLintRequest(
        pattern="[drm] ata.*: timeout",
        escape_positions=[0, 4],
    ))
    assert out["escaped"] == r"\[drm\] ata.*: timeout"
    assert out["issues"] == []


def test_lint_endpoint_sample_matching_shows_the_real_behavior():
    samples = ["[drm] init failed", "d init failed"]

    before = pm.lint_pattern_text(pm.PatternLintRequest(
        pattern="[drm] init failed", sample_lines=samples,
    ))
    assert before["matched_samples"] == ["d init failed"]      # 의도와 정반대

    after = pm.lint_pattern_text(pm.PatternLintRequest(
        pattern="[drm] init failed", escape_positions=[0, 4], sample_lines=samples,
    ))
    assert after["matched_samples"] == ["[drm] init failed"]


def test_lint_endpoint_skips_matching_when_uncompilable():
    out = pm.lint_pattern_text(pm.PatternLintRequest(
        pattern="*bad", sample_lines=["anything"],
    ))
    assert out["issues"][0]["severity"] == "error"
    assert out["matched_samples"] == []


# ── YAML seed 경로 ───────────────────────────────────────────────────────────

def _yaml(tmp_path, pattern: str):
    path = tmp_path / "p.yaml"
    path.write_text(
        "patterns:\n"
        '  - name: "T"\n'
        "    type: PRESENCE\n"
        '    keywords: ["k"]\n'
        f'    pattern: "{pattern}"\n',
        encoding="utf-8",
    )
    return path


def test_seed_rejects_uncompilable_pattern(tmp_path):
    with pytest.raises(ValueError, match="해석할 수 없습니다"):
        ps.load_yaml(_yaml(tmp_path, "*ERROR* commit failed"))


def test_seed_promotes_warning_to_error(tmp_path):
    """seed 는 확인해 줄 사람이 없으므로 경고에서 멈춘다."""
    with pytest.raises(ValueError, match="이스케이프"):
        ps.load_yaml(_yaml(tmp_path, "mmc0: error (retry)"))


def test_seed_accepts_escaped_literal(tmp_path):
    patterns = ps.load_yaml(_yaml(tmp_path, r"mmc0: error \\(retry\\)"))
    assert len(patterns) == 1


def test_default_patterns_yaml_is_clean():
    """기본 패턴 코퍼스가 승격된 규칙을 그대로 통과하는지 — 오경보 회귀 방지."""
    assert ps.load_yaml()


# ── LLM 생성 경로 ────────────────────────────────────────────────────────────

def _llm_response(pattern: str) -> str:
    return json.dumps({
        "pattern": {
            "name": "G", "type": "PRESENCE",
            "keywords": ["k"], "pattern": pattern,
        },
        "relations": [],
    })


def test_generation_surfaces_warnings_without_blocking():
    result = pg._parse_response(_llm_response("[drm] init failed"), [])
    assert [w["rule"] for w in result.lint_warnings] == ["taglike_class"]


def test_generation_rejects_uncompilable_pattern():
    """컴파일 실패는 pydantic 검증에서 막혀 재시도 피드백으로 이어진다."""
    with pytest.raises(ValueError):
        pg._parse_response(_llm_response("*ERROR* commit"), [])


def test_generation_clean_pattern_has_no_warnings():
    result = pg._parse_response(_llm_response("ata.*: EH complete"), [])
    assert result.lint_warnings == []


def test_generator_prompt_documents_escaping():
    assert "이스케이프" in pg._SYSTEM_PROMPT
