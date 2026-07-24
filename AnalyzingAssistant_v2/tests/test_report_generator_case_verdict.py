"""core/report_generator.py — "유사문제" 리포트에 매칭 케이스의 원 판정을 참고
섹션으로 추가하는 기능 (분석 리포트 개선 PR 2).

핵심 불변식: score-tier 판정("## 판정: 유사문제") 헤딩은 이 섹션 유무와 무관하게
항상 고정 문자열로 프롬프트에 박혀 있다 — 케이스의 원 판정으로 대체되지 않는다.
과거 케이스 판정 연계 작업(PR #36/#41-45)이 정확히 이 라벨을 덮어썼다가 반복
revert된 전례가 있어, 이 테스트가 그 재발을 막는 회귀 지점이다.
"""

from core.kb_search import MatchedCase
from core.pattern_matcher import MatchResult, PatternResult
from core.report_generator import _fmt_case_verdict_section, _prompt_matched


def _matched_case(**overrides) -> MatchedCase:
    base = dict(
        case_id=1, name="케이스A", description="설명", keywords=[],
        relevance_score=0.95,
    )
    base.update(overrides)
    return MatchedCase(**base)


def _match_result() -> MatchResult:
    matched = [PatternResult(name="P1", type="PRESENCE", matched=True, weight=1.0,
                              evidence=[], analysis_guidelines="")]
    return MatchResult(matched=matched, unmatched=[], score=0.9)


# ── _fmt_case_verdict_section ────────────────────────────────────────────────

def test_fmt_case_verdict_section_empty_when_no_case():
    assert _fmt_case_verdict_section(None) == ""


def test_fmt_case_verdict_section_empty_when_verdict_unset():
    """레거시 케이스(verdict 미기재)는 참고 섹션 자체를 안 만든다."""
    case = _matched_case(verdict=None)
    assert _fmt_case_verdict_section(case) == ""


def test_fmt_case_verdict_section_renders_verdict_and_rationale():
    case = _matched_case(
        verdict="no_defect",
        verdict_rationale="정상 동작으로 확인됨",
        actions={"keep": {"detail": "regression_only"}},
        notes="재현 안 됨",
    )
    section = _fmt_case_verdict_section(case)

    assert "매칭 케이스의 원 판정" in section
    assert "판정      : 문제 아님" in section
    assert "정상 동작으로 확인됨" in section
    assert "regression_only" in section
    assert "재현 안 됨" in section


def test_fmt_case_verdict_section_maps_all_verdict_values():
    assert "판정      : 문제\n" in _fmt_case_verdict_section(_matched_case(verdict="defect"))
    assert "판정      : 문제 아님\n" in _fmt_case_verdict_section(_matched_case(verdict="no_defect"))
    assert "판정      : 판정 불가\n" in _fmt_case_verdict_section(_matched_case(verdict="undetermined"))


# ── _prompt_matched — 헤딩 불변식 + 섹션 유무 ─────────────────────────────────

def test_prompt_matched_verdict_heading_is_always_fixed_regardless_of_case_verdict():
    """핵심 불변식: 케이스 원 판정이 무엇이든 '## 판정: 유사문제' 헤딩은 고정."""
    for case_verdict in ("defect", "no_defect", "undetermined", None):
        case = _matched_case(verdict=case_verdict)
        prompt = _prompt_matched("문제 설명", _match_result(), case)
        assert "## 판정: 유사문제" in prompt
        assert "## 판정: 문제" not in prompt
        assert "## 판정: 문제 아님" not in prompt
        assert "## 판정: 판정 불가" not in prompt


def test_prompt_matched_includes_case_verdict_section_when_case_has_verdict():
    case = _matched_case(verdict="defect", verdict_rationale="알려진 결함 시그니처")
    prompt = _prompt_matched("문제 설명", _match_result(), case)
    assert "매칭 케이스의 원 판정" in prompt
    assert "알려진 결함 시그니처" in prompt


def test_prompt_matched_omits_case_verdict_section_when_case_is_none():
    """matched_case가 None(§4 불변식, fallback 채택 또는 Stage2 MISS)이면 섹션 자체가 없다."""
    prompt = _prompt_matched("문제 설명", _match_result(), None)
    assert "매칭 케이스의 원 판정" not in prompt


def test_prompt_matched_omits_case_verdict_section_for_legacy_case():
    case = _matched_case(verdict=None)
    prompt = _prompt_matched("문제 설명", _match_result(), case)
    assert "매칭 케이스의 원 판정" not in prompt
