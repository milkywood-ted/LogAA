"""core/report_generator.py — §1.2-A 판정 로직 + P1'(fallback 강제 강등).

Document/케이스 판정 연계 재도입/PR36 요구사항 및 결함 리포트.md PR 2 핵심 테스트.
D2(fallback mixing)를 정확히 재현했던 시나리오를 회귀 테스트로 고정한다:
fallback 채택 시 score 가 아무리 높아도 "불확실"로 강등되고, 케이스의 판정
근거/조치/범위가 프롬프트에 전혀 주입되지 않아야 한다.
"""

import core.report_generator as report_generator_module
from core.kb_search import MatchedCase
from core.pattern_matcher import MatchResult, PatternResult
from core.report_generator import (
    ReportGenerator,
    _prompt_matched,
    _prompt_no_defect,
    _prompt_case_undetermined,
    _prompt_uncertain,
)


def _case(**overrides) -> MatchedCase:
    base = dict(
        case_id=1, name="케이스A", description="설명", keywords=[], relevance_score=0.9,
    )
    base.update(overrides)
    return MatchedCase(**base)


def _match_result(score: float, matched: bool = True) -> MatchResult:
    patterns = [PatternResult(name="P1", type="PRESENCE", matched=True, weight=1.0)] if matched else []
    return MatchResult(matched=patterns, unmatched=[], score=score)


def _gen(threshold: float = 0.5) -> ReportGenerator:
    return ReportGenerator(model="test-model", definite_threshold=threshold, suggest_kb=False)


# ── _determine_verdict — §1.2-A 3구간 ────────────────────────────────────────

def test_pass_through_defect():
    gen = _gen()
    r = _match_result(0.9)
    case = _case(case_verdict="defect")
    assert gen._determine_verdict(r, case, None) == "문제"


def test_pass_through_no_defect():
    gen = _gen()
    r = _match_result(0.9)
    case = _case(case_verdict="no_defect")
    assert gen._determine_verdict(r, case, None) == "문제 아님"


def test_pass_through_undetermined():
    gen = _gen()
    r = _match_result(0.9)
    case = _case(case_verdict="undetermined")
    assert gen._determine_verdict(r, case, None) == "판정 불가"


def test_legacy_case_defaults_to_problem():
    """C2 — verdict IS NULL(레거시 행)은 기존 동작("문제") 유지."""
    gen = _gen()
    r = _match_result(0.9)
    case = _case(case_verdict=None)
    assert gen._determine_verdict(r, case, None) == "문제"


def test_no_matched_case_defaults_to_problem():
    """matched_case 가 없어도(MISS 경로에서 전역 패턴이 매칭된 경우) 기존 동작 유지."""
    gen = _gen()
    r = _match_result(0.9)
    assert gen._determine_verdict(r, None, None) == "문제"


def test_low_score_is_uncertain_regardless_of_case_verdict():
    """C4 — 일치도 낮으면 케이스가 미확정이므로 판정을 결론으로 채택하지 않음."""
    gen = _gen(threshold=0.7)
    r = _match_result(0.3)
    case = _case(case_verdict="no_defect")
    assert gen._determine_verdict(r, case, None) == "불확실"


def test_no_matched_patterns_is_unknown():
    gen = _gen()
    r = _match_result(0.0, matched=False)
    assert gen._determine_verdict(r, None, None) == "알 수 없음"


# ── P1' — fallback 강제 강등 (D2 회귀 방지, 핵심) ────────────────────────────

def test_fallback_forces_uncertain_even_with_high_score_and_defect_verdict():
    """D1 시나리오 축 — fallback 이어도 defect 는 그대로 "불확실"(안전한 방향)."""
    gen = _gen(threshold=0.5)
    r = _match_result(0.9)  # 전역 재매칭 결과 score 는 threshold 이상
    case = _case(case_verdict="defect")
    assert gen._determine_verdict(r, case, fallback_original_score=0.2) == "불확실"


def test_fallback_forces_uncertain_even_with_high_score_and_no_defect_verdict():
    """D2 재현 시나리오 그 자체 — fallback 채택 + case_verdict=no_defect.

    이 조건에서 강등 없이 pass-through 하면 "커널 패닉 evidence + 다른 케이스의
    '정상 종결' 근거 = 문제 아님"이 재현된다. 반드시 "불확실"이어야 한다.
    """
    gen = _gen(threshold=0.5)
    r = _match_result(0.9)
    case = _case(case_verdict="no_defect")
    assert gen._determine_verdict(r, case, fallback_original_score=0.2) == "불확실"


# ── 프롬프트 주입 (R4/R5/R6) ──────────────────────────────────────────────────

def test_prompt_matched_injects_rationale_actions_scope():
    case = _case(
        case_verdict="defect",
        verdict_rationale="커널 패닉 재현 확인됨",
        actions={"fix": {"selected": True, "entries": [{"module": "PMIC", "change": "전류 제한 상향"}]}},
        symptom_module="전원 관리",
    )
    prompt = _prompt_matched("문제 설명", _match_result(0.9), case)
    assert "커널 패닉 재현 확인됨" in prompt
    assert "전류 제한 상향" in prompt
    assert "전원 관리" in prompt


def test_prompt_no_defect_injects_rationale():
    case = _case(case_verdict="no_defect", verdict_rationale="정상 종료 로그 확인됨")
    prompt = _prompt_no_defect("문제 설명", _match_result(0.9), case)
    assert "판정: 문제 아님" in prompt
    assert "정상 종료 로그 확인됨" in prompt


def test_prompt_case_undetermined_includes_reason():
    case = _case(
        case_verdict="undetermined",
        undetermined_reason="insufficient_logs",
        undetermined_reason_note="재현 로그 부족",
    )
    prompt = _prompt_case_undetermined("문제 설명", _match_result(0.9), case)
    assert "판정: 판정 불가" in prompt
    assert "재현 로그 부족" in prompt


def test_prompt_uncertain_low_score_shows_reference_verdict_line():
    """fallback 미채택(일치도만 낮음) — 케이스 판정을 "참고"로는 보여줘도 된다."""
    case = _case(case_verdict="no_defect", verdict_rationale="정상 종료 로그 확인됨")
    prompt = _prompt_uncertain("문제 설명", _match_result(0.3), case, fallback_original_score=None)
    assert "참고 케이스의 원 분석 판정" in prompt
    assert "정상 종료 로그 확인됨" in prompt


def test_prompt_uncertain_fallback_suppresses_case_verdict_reference():
    """P1' 핵심 — fallback 채택 시 케이스 판정/근거를 프롬프트에서 완전히 제거.

    D2 재현 프롬프트("원 분석의 판정 근거: ... 결함 아님으로 종결")가 다시
    새지 않는지 직접 확인한다.
    """
    case = _case(case_verdict="no_defect", verdict_rationale="정상 종료 로그 확인됨")
    prompt = _prompt_uncertain("문제 설명", _match_result(0.9), case, fallback_original_score=0.2)
    assert "참고 케이스의 원 분석 판정" not in prompt
    assert "정상 종료 로그 확인됨" not in prompt
    assert "원 분석의 판정 근거" not in prompt


def test_prompt_uncertain_fallback_does_not_leak_case_name():
    """실사용 재현(2026-07-23) — fallback 발동 시 케이스 이름 자체도 evidence
    와 나란히 노출하지 않는다. 이름만 남아도 LLM 이 무관한 패턴을 그 케이스
    것처럼 서술해 "케이스 제목과 패턴이 믹스"되는 증상이 재발한다.
    """
    case = _case(name="정상-전원재인가", case_verdict="no_defect")
    prompt = _prompt_uncertain(
        "부팅 중 커널 패닉", _match_result(0.95), case, fallback_original_score=0.15,
    )
    assert "정상-전원재인가" not in prompt
    assert "참고 케이스" not in prompt
    assert "특정 케이스에 귀속되지 않습니다" in prompt
    assert "케이스명을 언급" in prompt  # LLM 에게도 명시적으로 지시


def test_prompt_uncertain_low_score_without_fallback_still_shows_case_name():
    """fallback 이 아닌 단순 저점수 불확실은 귀속이 안 깨졌으므로 이름 유지."""
    case = _case(name="케이스A")
    prompt = _prompt_uncertain("문제 설명", _match_result(0.3), case, fallback_original_score=None)
    assert "케이스A" in prompt


# ── generate() 전체 경로 통합 — D2 재현 시나리오, 실제 LLM 프롬프트까지 확인 ───

def test_generate_end_to_end_fallback_does_not_leak_other_case_rationale(monkeypatch):
    """D2 원 재현 그대로: 커널 패닉 evidence(score=0.9) + no_defect 케이스,
    단 fallback 로 도달했다는 조건 하나만 걸고 generate() 전체를 돌린다.

    verdict 는 "불확실"이어야 하고(문제 아님 아님), LLM 에 실제로 전달되는
    프롬프트에 케이스의 판정 근거 텍스트가 전혀 없어야 한다.
    """
    captured = {}

    def fake_chat_stream(messages, model, temperature, cancel_event=None):
        captured["prompt"] = messages[0]["content"]
        return "# 리포트\n## 판정: 불확실\n"

    monkeypatch.setattr(report_generator_module, "chat_stream", fake_chat_stream)

    gen = _gen(threshold=0.5)
    case = _case(
        name="정상-전원재인가",
        case_verdict="no_defect",
        verdict_rationale="정상 종료 로그 확인됨(다른 케이스 근거)",
    )
    result = gen.generate(
        problem_text="커널 패닉 반복",
        l_common=[],
        match_result=_match_result(0.9),
        matched_case=case,
        fallback_original_score=0.2,  # fallback 채택 신호
    )

    assert result.verdict == "불확실"
    assert "정상 종료 로그 확인됨" not in captured["prompt"]
    assert "케이스의 원 분석 판정" not in captured["prompt"]
    assert "정상-전원재인가" not in captured["prompt"]  # 케이스 이름 자체도 미노출


def test_fallback_forces_uncertain_for_pinned_case_too():
    """D5 — pinned 케이스도 fallback 발동 시 동일하게 강등된다(_run_fallback 은
    pinned 을 구분하지 않으므로 report_generator 쪽도 별도 분기가 필요 없어야 함)."""
    gen = _gen(threshold=0.5)
    r = _match_result(0.9)
    case = _case(case_verdict="no_defect", pinned=True)
    assert gen._determine_verdict(r, case, fallback_original_score=0.2) == "불확실"
