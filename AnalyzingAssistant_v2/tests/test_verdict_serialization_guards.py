"""P5 나머지(D7/D8/D9) + P6 first_hit 가드.

Document/케이스 판정 연계 재도입/PR36 요구사항 및 결함 리포트.md PR 4.
"""

import logging
from types import SimpleNamespace as NS

import core.db as db
import core.pipeline as pipeline_module
from core.kb_search import MatchedCase
from core.pattern_matcher import MatchResult, PatternResult
from core.pipeline import Pipeline, serialize_result
from core.report_generator import ReportGenerator


def _case(**overrides) -> MatchedCase:
    base = dict(case_id=1, name="케이스A", description="", keywords=[], relevance_score=0.9)
    base.update(overrides)
    return MatchedCase(**base)


def _match_result(score: float = 0.9) -> MatchResult:
    return MatchResult(
        matched=[PatternResult(name="P1", type="PRESENCE", matched=True, weight=1.0)],
        unmatched=[], score=score,
    )


def _gen() -> ReportGenerator:
    return ReportGenerator(model="test-model", definite_threshold=0.5, num_ctx=198000, suggest_kb=False)


# ── D7 — 토큰 추정에 rationale/actions/scope 반영 ────────────────────────────

def test_estimate_fixed_tokens_grows_with_case_verdict_sections():
    gen = _gen()
    r = _match_result()
    bare = gen._estimate_fixed_tokens("문제", r, [], matched_case=None)

    rich_case = _case(
        case_verdict="defect",
        verdict_rationale="아주 긴 판정 근거 텍스트입니다. " * 20,
        symptom_module="전원 관리",
        actions={"fix": {"selected": True, "entries": [{"module": "PMIC", "change": "전류 제한 상향"}]}},
    )
    with_case = gen._estimate_fixed_tokens("문제", r, [], matched_case=rich_case)

    assert with_case > bare


def test_estimate_fixed_tokens_unaffected_when_fallback_suppresses_case_block():
    """P1' 강등 시(불확실 + fallback)에는 케이스 섹션이 안 실리므로 토큰 추정도 그대로."""
    gen = _gen()
    r = _match_result()
    case = _case(case_verdict="defect", verdict_rationale="근거 텍스트 " * 20)
    without_fallback_flag = gen._estimate_fixed_tokens(
        "불확실", r, [], matched_case=case, fallback_original_score=None,
    )
    with_fallback_flag = gen._estimate_fixed_tokens(
        "불확실", r, [], matched_case=case, fallback_original_score=0.2,
    )
    assert with_fallback_flag < without_fallback_flag


# ── D8 — 미지 case_verdict 값 silent default 대신 경고 ───────────────────────

def test_unknown_case_verdict_defaults_to_problem_but_logs_warning(caplog):
    gen = _gen()
    r = _match_result()
    case = _case(case_verdict="이상한값")
    with caplog.at_level(logging.WARNING):
        verdict = gen._determine_verdict(r, case, None)
    assert verdict == "문제"
    assert any("알 수 없는 case_verdict" in rec.message for rec in caplog.records)


def test_known_case_verdict_does_not_warn(caplog):
    gen = _gen()
    r = _match_result()
    case = _case(case_verdict="defect")
    with caplog.at_level(logging.WARNING):
        gen._determine_verdict(r, case, None)
    assert not any("알 수 없는 case_verdict" in rec.message for rec in caplog.records)


# ── D9 — 출처 필드는 history 저장 시 제거, 라이브 응답엔 유지 ────────────────

def _fake_pipeline_result():
    mc = NS(
        case_id=1, name="케이스A", relevance_score=0.9, keywords=[], chip_tags=[], references=[],
        case_verdict="defect", verdict_rationale="근거", undetermined_reason=None,
        undetermined_reason_note="", actions={}, symptom_module="", defect_area_type=None,
        defect_area_module="", defect_area_items=[], notes="",
        analyst="홍길동", owner_module="전원팀", analysis_date="2026-07-20", log_source="현장 로그",
    )
    return NS(
        verdict="문제", report_md="# 리포트", matched_case=mc,
        match_result=NS(score=0.9, matched=[], unmatched=[]),
        reflection_notes="", pool_conflict_warning=None, history_id=None,
        selected_logs={}, warnings=[], minority_reports=[], winner_profile_names=[],
    )


def test_serialize_result_keeps_provenance_fields():
    """라이브 API 응답(serialize_result 직접 소비)에는 출처 필드가 그대로 남는다."""
    s = serialize_result(_fake_pipeline_result())
    assert s["matched_case"]["analyst"] == "홍길동"
    assert s["matched_case"]["analysis_date"] == "2026-07-20"
    assert s["matched_case"]["log_source"] == "현장 로그"


def test_save_history_strips_provenance_fields(tmp_path):
    """이력 저장 시점(_save_history)에는 D9 세 필드를 제거한다."""
    dbp = tmp_path / "t.db"
    db.init_db(dbp)
    result = _fake_pipeline_result()
    hid = Pipeline._save_history(NS(db_path=dbp), "문제 설명", {"f.log": "raw"}, result)
    assert hid is not None

    import json
    with db.get_conn(dbp) as conn:
        payload = json.loads(conn.execute(
            "SELECT result FROM history WHERE id=?", (hid,)).fetchone()["result"])
    full_case = payload["full"]["matched_case"]
    assert "analyst" not in full_case
    assert "analysis_date" not in full_case
    assert "log_source" not in full_case
    # case_verdict 등 나머지 필드는 그대로 유지
    assert full_case["case_verdict"] == "defect"


# ── P6 — first_hit 모드 전환 시 경고 ──────────────────────────────────────────

def test_pipeline_warns_on_first_hit_mode(monkeypatch, caplog):
    monkeypatch.setattr(
        pipeline_module.cfg_module, "get_str",
        lambda key, default="": "first_hit" if key == "pipeline.moe_traversal_mode" else default,
    )
    with caplog.at_level(logging.WARNING):
        Pipeline(reflect=False, save_history=False)
    assert any("first_hit" in rec.message for rec in caplog.records)


def test_pipeline_no_warning_on_ensemble_mode(monkeypatch, caplog):
    monkeypatch.setattr(
        pipeline_module.cfg_module, "get_str",
        lambda key, default="": "ensemble" if key == "pipeline.moe_traversal_mode" else default,
    )
    with caplog.at_level(logging.WARNING):
        Pipeline(reflect=False, save_history=False)
    assert not any("first_hit" in rec.message for rec in caplog.records)
