"""core/pattern_lint.py — 정규식 오작성 검출.

핵심 원리 검증: "메타문자가 있는지" 가 아니라 "정규식으로서 무의미한 구조인지"
를 본다. 따라서 의도된 정규식에는 경고가 없어야 하고(오경보 억제),
로그 원문을 그대로 옮긴 패턴에는 경고가 나와야 한다.
"""

import re

import pytest

from core.pattern_lint import (
    ERROR,
    WARNING,
    escape_at,
    has_error,
    iter_pattern_fields,
    lint,
    lint_pattern_fields,
)


def _rules(pattern: str) -> list[str]:
    return [i.rule for i in lint(pattern)]


# ── 오경보 억제 — 의도된 정규식은 통과해야 한다 ──────────────────────────────

@pytest.mark.parametrize("pattern", [
    "ata.*exception|ata.*error",             # 기본 패턴 코퍼스 실사용
    "EXT4-fs.*: unmounting filesystem",
    "blk_update_request.*I/O error",
    "Out of memory: Kill process|oom_kill_process",
    "kernel panic - not syncing",            # 메타문자 없음
    r"errors?: \d+",                         # ? 는 정당한 용법
    "[0-9]+ blocks",                         # 범위가 있는 문자 클래스
    "(timeout|abort)+",                      # 대안 + 수량자
    "(?:reset|resume) complete",             # 특수 그룹
    r"\[drm\] init failed",                  # 이미 이스케이프됨
    r"ata\d+\.\d+: failed",
    "[^ ]+ failed",                          # 부정 클래스
])
def test_intentional_regex_produces_no_warning(pattern):
    assert lint(pattern) == []


# ── 오작성 검출 — 로그 원문을 그대로 넣은 경우 ───────────────────────────────

@pytest.mark.parametrize("pattern, rule", [
    ("[drm] init failed",           "taglike_class"),
    ("i2c: read failed [-110]",     "taglike_class"),
    ("mmc0: error (retry)",         "noop_group"),
    ("C++ exception",               "possessive_quantifier"),
    ("func+0x0/0x10",               "literal_quantifier"),
    ("at drivers/mmc/core.c:1234",  "bare_dot"),
    ("ata1.00: failed",             "bare_dot"),
])
def test_literal_mistakes_are_detected(pattern, rule):
    assert rule in _rules(pattern)


def test_each_problem_reported_once():
    """`C++` 은 possessive 규칙만 보고한다 — 같은 문제의 중복 경고를 만들지 않는다."""
    assert _rules("C++ exception") == ["possessive_quantifier"]


def test_taglike_class_actually_misbehaves():
    """경고가 가리키는 오동작이 실재하는지 확인 — 규칙의 근거."""
    assert re.search("[drm] init failed", "d init failed")      # 의도치 않은 매칭
    assert not re.search("[drm] init failed", "[drm] init failed")


def test_possessive_is_silent_on_this_runtime():
    """Python 3.11+ 에서 `C++` 는 예외 없이 컴파일된다 — 그래서 린트가 필요하다."""
    compiled = re.compile("C++ exception")           # 예외 없음
    assert not compiled.search("C++ exception")
    assert compiled.search("CCC exception")


# ── 컴파일 실패는 ERROR ──────────────────────────────────────────────────────

def test_compile_failure_is_error():
    issues = lint("*ERROR* atomic commit failed")
    assert len(issues) == 1
    assert issues[0].severity == ERROR
    assert issues[0].rule == "compile_failed"


def test_compile_failure_suppresses_other_rules():
    """구조를 신뢰할 수 없으므로 다른 규칙 결과를 섞지 않는다."""
    assert len(lint("[drm (unclosed")) == 1


def test_warning_severity():
    assert all(i.severity == WARNING for i in lint("[drm] init failed"))


def test_empty_pattern_is_clean():
    assert lint("") == []


# ── 위치 정보 ────────────────────────────────────────────────────────────────

def test_issue_span_points_at_the_problem():
    issue = lint("[drm] init failed")[0]
    assert "[drm] init failed"[issue.start:issue.end] == "[drm]"


# ── escape_at — 혼합 케이스 ──────────────────────────────────────────────────

def test_escape_at_only_selected_positions():
    src = "[drm] ata.*: timeout"
    out = escape_at(src, [0, 4])
    assert out == r"\[drm\] ata.*: timeout"
    assert lint(out) == []                  # 리터럴 부분만 해소, .* 는 그대로 정규식


def test_escape_at_ignores_out_of_range():
    assert escape_at("abc", [-1, 99]) == "abc"


def test_escape_at_empty_positions_is_identity():
    assert escape_at("ata.*", []) == "ata.*"


# ── 패턴 dict 순회 ───────────────────────────────────────────────────────────

def test_iter_pattern_fields_covers_all_regex_fields():
    p = {
        "pattern":         "a",
        "trigger_pattern": "b",
        "absent_pattern":  "c",
        "steps":           ["d", "e"],
        "keywords":        ["ignored"],     # 리터럴 필드 — 대상 아님
        "description":     "ignored",
    }
    assert list(iter_pattern_fields(p)) == [
        ("pattern", "a"),
        ("trigger_pattern", "b"),
        ("absent_pattern", "c"),
        ("steps[0]", "d"),
        ("steps[1]", "e"),
    ]


def test_iter_pattern_fields_skips_empty_and_missing():
    assert list(iter_pattern_fields({"pattern": "", "steps": []})) == []


def test_lint_pattern_fields_tags_the_field():
    issues = lint_pattern_fields({"steps": ["ok .*", "[drm] bad"]})
    assert [i.field for i in issues] == ["steps[1]"]
    assert issues[0].issue.rule == "taglike_class"


def test_has_error_distinguishes_severity():
    assert has_error(lint_pattern_fields({"pattern": "*bad"}))
    assert not has_error(lint_pattern_fields({"pattern": "[drm] warn only"}))
