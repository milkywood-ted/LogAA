"""core/report_generator.py — 유사도 판정(축 1) × 케이스 원 판정 인용(축 2).

Document/케이스 판정 연계 재도입/PR36 요구사항 및 결함 리포트.md, 2026-07-23
실사용 재현으로 정정된 모델의 회귀 테스트.

핵심 원칙: 두 축은 절대 섞이지 않는다.
- 유사도 판정(verdict)은 순수 score 기반이며 matched_case/fallback 여부와
  무관하다 — "score 100%인데 불확실"처럼 사람이 납득 못 할 결과를 만들지 않는다.
- 케이스 원 판정은 verdict 를 바꾸지 않는 "인용"일 뿐이다. fallback 채택 시
  (evidence 의 출처가 case 자신의 패턴이 아님) 인용 자체(케이스 이름 포함)를
  생략한다 — 그래야 "케이스 제목과 무관한 패턴이 섞여 보이는" 문제가 안 생긴다.
"""

import core.report_generator as report_generator_module
from core.kb_search import MatchedCase
from core.pattern_matcher import MatchResult, PatternResult
from core.report_generator import ReportGenerator, _prompt_matched, _prompt_uncertain


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


# ── _determine_verdict — 유사도 판정(축 1), 순수 score 기반 ─────────────────

def test_verdict_is_pure_score_based_high():
    gen = _gen(threshold=0.5)
    assert gen._determine_verdict(_match_result(0.9)) == "문제"


def test_verdict_is_pure_score_based_low():
    gen = _gen(threshold=0.5)
    assert gen._determine_verdict(_match_result(0.3)) == "불확실"


def test_verdict_no_matched_patterns_is_unknown():
    gen = _gen()
    assert gen._determine_verdict(_match_result(0.0, matched=False)) == "알 수 없음"


def test_determine_verdict_takes_only_match_result():
    """시그니처 자체가 matched_case/fallback 를 받지 않는다 — 두 축이 코드
    구조상으로도 섞일 수 없게 만든다."""
    import inspect
    sig = inspect.signature(ReportGenerator._determine_verdict)
    assert list(sig.parameters) == ["self", "r"]


# ── 핵심 회귀: score 가 높으면 fallback 이어도 verdict 는 "문제" ─────────────
# (2026-07-23 실사용 재현 — "score 100%인데 불확실"은 버그였다. verdict 는
# fallback 여부와 무관해야 한다. fallback 이 막아야 하는 건 케이스 "인용"뿐.)

def test_high_score_via_fallback_still_yields_problem_verdict():
    gen = _gen(threshold=0.5)
    r = _match_result(1.0)  # fallback 전역 재검색 결과 100%
    assert gen._determine_verdict(r) == "문제"


def test_low_score_via_fallback_still_yields_uncertain():
    gen = _gen(threshold=0.5)
    r = _match_result(0.2)
    assert gen._determine_verdict(r) == "불확실"


# ── 케이스 원 판정 "인용"(축 2) — fallback 미채택 시에만, 판정 로직 아님 ─────

def test_prompt_matched_cites_case_verdict_when_not_fallback():
    case = _case(
        case_verdict="no_defect",
        verdict_rationale="정상 종료 로그 확인됨",
        actions={"fix": {"selected": True, "entries": [{"module": "PMIC", "change": "전류 제한 상향"}]}},
        symptom_module="전원 관리",
    )
    prompt = _prompt_matched("문제 설명", _match_result(0.9), case, fallback_original_score=None)
    assert "판정: 문제" in prompt          # 유사도 판정은 그대로 "문제"
    assert "케이스 원 판정(인용) : 비결함" in prompt  # 인용은 별도로 존재
    assert "정상 종료 로그 확인됨" in prompt
    assert "전류 제한 상향" in prompt
    assert "전원 관리" in prompt


def test_prompt_matched_cites_undetermined_reason():
    case = _case(
        case_verdict="undetermined",
        undetermined_reason="insufficient_logs",
        undetermined_reason_note="재현 로그 부족",
    )
    prompt = _prompt_matched("문제 설명", _match_result(0.9), case, fallback_original_score=None)
    assert "케이스 원 판정(인용) : 판정불가" in prompt
    assert "재현 로그 부족" in prompt


def test_prompt_matched_verdict_and_citation_do_not_contradict_instruction():
    """유사도(문제)와 케이스 인용(비결함)이 같은 의미가 아니라는 걸 LLM에게도 명시."""
    case = _case(case_verdict="no_defect")
    prompt = _prompt_matched("문제 설명", _match_result(0.9), case, fallback_original_score=None)
    assert "같은 의미인 것처럼" in prompt


def test_prompt_matched_fallback_omits_citation_and_case_name():
    """P1' 실질 — fallback 채택 시 케이스 이름도 인용도 전부 생략. verdict 는
    여전히 "문제"(호출자가 그렇게 부른 것)이지만 프롬프트엔 케이스 정체성이
    전혀 안 남는다."""
    case = _case(
        name="정상-전원재인가",
        case_verdict="no_defect",
        verdict_rationale="정상 종료 로그 확인됨(다른 케이스 근거)",
    )
    prompt = _prompt_matched(
        "커널 패닉 반복", _match_result(1.0), case, fallback_original_score=0.15,
    )
    assert "정상-전원재인가" not in prompt
    assert "케이스 원 판정" not in prompt
    assert "정상 종료 로그 확인됨" not in prompt
    assert "매칭 케이스 :" not in prompt
    assert "특정 케이스에 귀속되지 않습니다" in prompt
    assert "케이스명을 언급" in prompt


def test_prompt_matched_no_case_has_no_citation():
    prompt = _prompt_matched("문제 설명", _match_result(0.9), None, fallback_original_score=None)
    assert "케이스 원 판정" not in prompt


# ── 불확실 경로 — 기존 동작(2026-07-23 이전 수정분) 유지 ────────────────────

def test_prompt_uncertain_low_score_shows_reference_verdict_line():
    case = _case(case_verdict="no_defect", verdict_rationale="정상 종료 로그 확인됨")
    prompt = _prompt_uncertain("문제 설명", _match_result(0.3), case, fallback_original_score=None)
    assert "참고 케이스의 원 분석 판정" in prompt
    assert "정상 종료 로그 확인됨" in prompt


def test_prompt_uncertain_fallback_omits_case_name_and_citation():
    case = _case(name="정상-전원재인가", case_verdict="no_defect", verdict_rationale="정상 종료 로그 확인됨")
    prompt = _prompt_uncertain(
        "부팅 중 커널 패닉", _match_result(0.2), case, fallback_original_score=0.15,
    )
    assert "정상-전원재인가" not in prompt
    assert "참고 케이스" not in prompt
    assert "특정 케이스에 귀속되지 않습니다" in prompt


# ── generate() 전체 경로 통합 ─────────────────────────────────────────────────

def test_generate_high_score_fallback_yields_problem_without_case_leak(monkeypatch):
    """2026-07-23 재현 시나리오 그대로: fallback 로 score=100%. verdict 는
    "문제"(유사도 판정 그대로)이고, 그러면서도 케이스 이름/인용은 전혀 안 샌다.
    """
    captured = {}

    def fake_chat_stream(messages, model, temperature, cancel_event=None):
        captured["prompt"] = messages[0]["content"]
        return "# 리포트\n## 판정: 문제\n"

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
        match_result=_match_result(1.0),
        matched_case=case,
        fallback_original_score=0.15,
    )

    assert result.verdict == "문제"  # 100% 인데 "불확실"이 되는 일은 없다
    assert "정상-전원재인가" not in captured["prompt"]
    assert "정상 종료 로그 확인됨" not in captured["prompt"]


def test_generate_uncertain_low_score_no_fallback(monkeypatch):
    monkeypatch.setattr(
        report_generator_module, "chat_stream",
        lambda **kwargs: "# 리포트\n## 판정: 불확실\n",
    )
    gen = _gen(threshold=0.5)
    result = gen.generate(
        problem_text="문제", l_common=[], match_result=_match_result(0.2),
        matched_case=_case(), fallback_original_score=None,
    )
    assert result.verdict == "불확실"


def test_pinned_case_fallback_omits_citation_too():
    """D5 — pinned 케이스도 fallback 발동 시 동일하게 인용이 생략된다."""
    case = _case(case_verdict="no_defect", pinned=True)
    prompt = _prompt_matched("문제 설명", _match_result(1.0), case, fallback_original_score=0.1)
    assert "케이스 원 판정" not in prompt
