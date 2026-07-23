"""core/reflection.py — P3 풀 내 상충 후보 경고 (Stage 6 확장).

Document/케이스 판정 연계 재도입/PR36 요구사항 및 결함 리포트.md PR 3.
LLM 프롬프트가 아니라 코드가 결정적으로 판단해야 한다(D3 재발 방지) — 이
테스트는 LLM 응답 내용과 무관하게 pool_conflict_warning 이 채워지는지 검증한다.
"""

import core.reflection as reflection_module
from core.kb_search import MatchedCase
from core.pattern_matcher import MatchResult, PatternResult
from core.pipeline import MinorityReport
from core.reflection import Reflector, _check_pool_conflict


def _case(case_id: int, name: str, case_verdict) -> MatchedCase:
    return MatchedCase(
        case_id=case_id, name=name, description="", keywords=[], relevance_score=0.5,
        case_verdict=case_verdict,
    )


def _minority(case_id: int, name: str, case_verdict, score: float = 0.6) -> MinorityReport:
    mr = MatchResult(matched=[PatternResult(name="P", type="PRESENCE", matched=True, weight=1.0)],
                      unmatched=[], score=score)
    return MinorityReport(matched_case=_case(case_id, name, case_verdict), match_result=mr)


# ── _check_pool_conflict ─────────────────────────────────────────────────────

def test_warns_when_winner_no_defect_and_minority_has_defect():
    warning = _check_pool_conflict("문제 아님", [_minority(2, "케이스B", "defect")])
    assert warning is not None
    assert "케이스B" in warning


def test_warns_when_winner_undetermined_and_minority_has_defect():
    warning = _check_pool_conflict("판정 불가", [_minority(2, "케이스B", "defect")])
    assert warning is not None


def test_no_warning_when_minority_has_no_conflicting_defect():
    warning = _check_pool_conflict("문제 아님", [_minority(2, "케이스B", "no_defect")])
    assert warning is None


def test_no_warning_when_no_minority_reports():
    assert _check_pool_conflict("문제 아님", []) is None


def test_asymmetric_no_warning_when_winner_is_problem():
    """핵심 설계 결정 — 반대 방향(winner=문제, no_defect 후보)은 검사하지 않는다."""
    warning = _check_pool_conflict("문제", [_minority(2, "케이스B", "no_defect")])
    assert warning is None


def test_no_warning_for_uncertain_or_unknown_verdicts():
    assert _check_pool_conflict("불확실", [_minority(2, "케이스B", "defect")]) is None
    assert _check_pool_conflict("알 수 없음", [_minority(2, "케이스B", "defect")]) is None


def test_zero_score_minority_excluded():
    """minority_reports 는 이미 score>0 로 필터돼 들어오지만, 재확인 동작도 검증."""
    warning = _check_pool_conflict("문제 아님", [_minority(2, "케이스B", "defect", score=0.0)])
    assert warning is None


# ── Reflector.reflect() 배선 — LLM 응답 내용과 무관하게 결정적으로 채워짐 ─────

def test_reflect_populates_warning_regardless_of_llm_output(monkeypatch):
    monkeypatch.setattr(
        reflection_module, "chat_stream",
        lambda **kwargs: "### REFLECTION_NOTES\n변경 없음\n### REPORT_FINAL\n# 리포트\n",
    )
    reflector = Reflector(model="test-model")
    result = reflector.reflect(
        report_md="# 리포트", verdict="문제 아님", score=0.9,
        match_result=MatchResult(matched=[], unmatched=[], score=0.9),
        l_common=[],
        minority_reports=[_minority(2, "케이스B", "defect")],
    )
    assert result.pool_conflict_warning is not None
    assert "케이스B" in result.pool_conflict_warning


def test_reflect_no_warning_when_minority_reports_omitted(monkeypatch):
    monkeypatch.setattr(
        reflection_module, "chat_stream",
        lambda **kwargs: "### REFLECTION_NOTES\n변경 없음\n### REPORT_FINAL\n# 리포트\n",
    )
    reflector = Reflector(model="test-model")
    result = reflector.reflect(
        report_md="# 리포트", verdict="문제 아님", score=0.9,
        match_result=MatchResult(matched=[], unmatched=[], score=0.9),
        l_common=[],
    )
    assert result.pool_conflict_warning is None
