"""Stage 5 판정 2축 분리 — 일치도(match_level) × 원 분석 판정(case_verdict).

일치도는 "이 로그가 케이스의 패턴 시그니처를 얼마나 재현하는가" 이고, 그것만으로는
결함 여부를 말할 수 없다. 결함 여부는 케이스 자신이 원 분석에서 받은 판정에 달려
있으므로 두 축을 합쳐 최종 판정을 낸다 (core/report_generator 모듈 docstring 참조).
"""

import json

import pytest

import core.db as db
from core.kb_search import KBSearch, MatchedCase
from core.pattern_matcher import MatchResult, PatternResult
from core.report_generator import ReportGenerator


def _mr(score: float, matched: bool = True) -> MatchResult:
    """지정 score 의 MatchResult 를 만든다. matched=False 면 매칭 0건."""
    p = PatternResult(name="P1", type="PRESENCE", matched=True, weight=1.0)
    return MatchResult(matched=[p] if matched else [], unmatched=[], score=score)


def _case(case_verdict=None, **kw) -> MatchedCase:
    return MatchedCase(
        case_id=1, name="케이스A", description="설명", keywords=[],
        relevance_score=0.9, case_verdict=case_verdict, **kw,
    )


@pytest.fixture
def gen() -> ReportGenerator:
    """LLM·config 접근 없이 판정 로직만 검사하기 위한 인스턴스."""
    return ReportGenerator(
        model="", definite_threshold=0.5, max_log_lines=10,
        suggest_kb=False, context_strategy="truncation",
        hybrid_overflow_ratio=0.3, num_ctx=None,
    )


# ── 축 1: 일치도 ──────────────────────────────────────────────────────────────

def test_match_level_from_score(gen):
    assert gen._determine_match_level(_mr(0.0, matched=False)) == "없음"
    assert gen._determine_match_level(_mr(0.4)) == "부분"
    assert gen._determine_match_level(_mr(0.5)) == "높음"    # 임계값 포함
    assert gen._determine_match_level(_mr(0.9)) == "높음"


# ── 축 2 결합: 최종 판정 ──────────────────────────────────────────────────────

@pytest.mark.parametrize("case_verdict,expected", [
    ("defect",       "문제"),
    ("no_defect",    "문제 아님"),
    ("undetermined", "판정 불가"),
    (None,           "문제"),      # 스키마 도입 전 레거시 행 — 기존 동작 유지
])
def test_verdict_follows_case_verdict_when_match_is_high(gen, case_verdict, expected):
    assert gen._determine_verdict("높음", _case(case_verdict)) == expected


def test_high_match_without_case_falls_back_to_problem(gen):
    """MISS 경로(matched_case=None)는 케이스 판정이 없으므로 기존대로 "문제"."""
    assert gen._determine_verdict("높음", None) == "문제"


@pytest.mark.parametrize("case_verdict", ["defect", "no_defect", "undetermined", None])
def test_partial_and_no_match_ignore_case_verdict(gen, case_verdict):
    """케이스가 확정되지 않은 상태에서 그 케이스의 판정을 끌어오면 근거가 되지 않는다."""
    assert gen._determine_verdict("부분", _case(case_verdict)) == "불확실"
    assert gen._determine_verdict("없음", _case(case_verdict)) == "알 수 없음"


# ── 판정별 프롬프트 ───────────────────────────────────────────────────────────

def test_no_defect_prompt_does_not_assert_problem(gen):
    """무결함 케이스에 "판정: 문제" 출력 형식을 요구하면 원 분석 결론이 뒤집힌다."""
    case = _case("no_defect", verdict_rationale="전원 시퀀스상 정상 동작으로 확인됨")
    prompt = gen._build_prompt("문제 아님", "증상 설명", _mr(0.9), case, "", [])

    assert "## 판정: 문제 아님" in prompt
    assert "## 판정: 문제\n" not in prompt
    assert "전원 시퀀스상 정상 동작으로 확인됨" in prompt


def test_case_undetermined_prompt_carries_reason(gen):
    case = _case("undetermined", undetermined_reason="insufficient_logs")
    prompt = gen._build_prompt("판정 불가", "증상 설명", _mr(0.8), case, "", [])

    assert "## 판정: 판정 불가" in prompt
    assert "로그 부족" in prompt


def test_matched_prompt_includes_verdict_rationale(gen):
    case = _case("defect", verdict_rationale="ATA 링크 리셋 반복 확인")
    prompt = gen._build_prompt("문제", "증상 설명", _mr(0.9), case, "", [])

    assert "## 판정: 문제" in prompt
    assert "ATA 링크 리셋 반복 확인" in prompt


# ── 원 분석의 조치 이력 주입 ──────────────────────────────────────────────────

_ACCEPTED_DEFECT = {"keep": {"selected": True, "detail": "accept_defect",
                             "reason": "우선순위 낮음"}}


def test_matched_prompt_carries_action_history(gen):
    """결함이지만 미수정 수용된 건임을 리포트가 알 수 있어야 한다."""
    case = _case("defect", actions=_ACCEPTED_DEFECT)
    prompt = gen._build_prompt("문제", "증상 설명", _mr(0.9), case, "", [])

    assert "원 분석의 조치 이력" in prompt
    assert "결함 수용·보류 (사유: 우선순위 낮음)" in prompt
    assert "이미 종결된 대응을 다시 제안하지 마세요" in prompt


def test_uncertain_prompt_omits_action_history(gen):
    """일치도가 낮으면 케이스가 확정되지 않았으므로 대응 이력을 붙이지 않는다."""
    case = _case("defect", actions=_ACCEPTED_DEFECT)
    prompt = gen._build_prompt("불확실", "증상 설명", _mr(0.3), case, "", [])

    assert "원 분석의 조치 이력" not in prompt
    assert "## 판정: 불확실" in prompt


def test_prompt_omits_action_section_when_no_action_recorded(gen):
    case = _case("defect", actions={})
    prompt = gen._build_prompt("문제", "증상 설명", _mr(0.9), case, "", [])

    assert "원 분석의 조치 이력" not in prompt


# ── 원 분석이 확정한 문제 범위 ────────────────────────────────────────────────

def _scoped_case() -> MatchedCase:
    """증상은 display 에서 보이지만 결함은 pm_core 에 있는 케이스."""
    return _case(
        "defect", symptom_module="display", defect_area_type="module",
        defect_area_module="pm_core", notes="저온 부팅에서만 재현",
        analyst="홍길동", analysis_date="2026-07-01", log_source="D-1 dmesg",
    )


def test_matched_prompt_separates_symptom_from_defect_area(gen):
    prompt = gen._build_prompt("문제", "화면 깜빡임", _mr(0.9), _scoped_case(), "", [])

    assert "원 분석이 확정한 문제 범위" in prompt
    assert "문제현상 발현 영역: display" in prompt
    assert "결함영역: 특정 모듈 (pm_core)" in prompt
    assert "특이사항: 저온 부팅에서만 재현" in prompt


def test_prompt_excludes_provenance_fields(gen):
    """분석자·일자·로그 출처는 진단 근거가 아니므로 프롬프트에 넣지 않는다."""
    prompt = gen._build_prompt("문제", "화면 깜빡임", _mr(0.9), _scoped_case(), "", [])

    assert "홍길동" not in prompt
    assert "2026-07-01" not in prompt
    assert "D-1 dmesg" not in prompt


def test_uncertain_prompt_omits_scope_section(gen):
    prompt = gen._build_prompt("불확실", "화면 깜빡임", _mr(0.3), _scoped_case(), "", [])

    assert "원 분석이 확정한 문제 범위" not in prompt


def test_prompt_omits_scope_section_when_unrecorded(gen):
    prompt = gen._build_prompt("문제", "화면 깜빡임", _mr(0.9), _case("defect"), "", [])

    assert "원 분석이 확정한 문제 범위" not in prompt


# ── KB 조회가 판정 필드를 함께 싣는지 ─────────────────────────────────────────

def _insert_case(dbp, name: str, **cols) -> None:
    fields = {"name": name, "description": "설명", "keywords": json.dumps([]), **cols}
    placeholders = ",".join("?" * len(fields))
    with db.get_conn(dbp) as conn:
        conn.execute(
            f"INSERT INTO cases ({','.join(fields)}) VALUES ({placeholders})",
            list(fields.values()),
        )


def test_load_case_by_name_carries_verdict_fields(tmp_path):
    dbp = tmp_path / "t.db"
    db.init_db(dbp)
    _insert_case(dbp, "무결함케이스", verdict="no_defect",
                 verdict_rationale="정상 동작 범위로 확인")

    kb = KBSearch(db_path=dbp, chroma_path=tmp_path / "chroma")
    case = kb.load_case_by_name("무결함케이스")

    assert case is not None
    assert case.case_verdict == "no_defect"
    assert case.verdict_rationale == "정상 동작 범위로 확인"


def test_load_case_by_name_parses_actions_json(tmp_path):
    dbp = tmp_path / "t.db"
    db.init_db(dbp)
    _insert_case(dbp, "보류케이스", verdict="defect",
                 actions=json.dumps(_ACCEPTED_DEFECT, ensure_ascii=False))

    kb = KBSearch(db_path=dbp, chroma_path=tmp_path / "chroma")
    case = kb.load_case_by_name("보류케이스")

    assert case is not None
    assert case.actions["keep"]["detail"] == "accept_defect"


def test_load_case_by_name_tolerates_legacy_null_verdict(tmp_path):
    """스키마 도입 전 행은 verdict 가 NULL — None 으로 실려 기존 동작을 유지한다."""
    dbp = tmp_path / "t.db"
    db.init_db(dbp)
    _insert_case(dbp, "레거시케이스")

    kb = KBSearch(db_path=dbp, chroma_path=tmp_path / "chroma")
    case = kb.load_case_by_name("레거시케이스")

    assert case is not None
    assert case.case_verdict is None
    assert case.verdict_rationale == ""
