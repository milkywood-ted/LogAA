"""core/pipeline.py Pipeline._run_fallback — §4 불변식 1 재귀속 (근본 수정).

실사용 재현(2026-07-25): "매칭 패턴은 맞게 나오는데 매칭 케이스가 다른 게
나온다." fallback 이 채택되면 evidence(match_result)의 출처가 matched_case
자신의 패턴이 아니게 되는데, 예전엔 matched_case 를 그대로 뒀다. 이 테스트는
실제 SQLite DB에 케이스 2개·패턴 2개를 심어 재현하고, 수정 후 fallback 이
실제로 패턴을 가진 케이스로 재귀속되는지 확인한다 — 추정이 아니라 실행
재현이다.
"""

import json

import core.db as db
from core.kb_search import MatchedCase
from core.log_refiner import LogLine
from core.observability import AnalysisLogger
from core.pattern_matcher import MatchResult, PatternResult
from core.pipeline import Pipeline


def _insert_case_with_pattern(dbp, case_name: str, pattern_name: str, regex: str) -> tuple[int, int]:
    """케이스 1개 + 그 케이스에만 연결된 패턴 1개를 만든다. (case_id, pattern_id) 반환."""
    with db.get_conn(dbp) as conn:
        cur = conn.execute(
            "INSERT INTO cases (name, description, keywords) VALUES (?, ?, ?)",
            (case_name, f"{case_name} 설명", json.dumps(["kernel"])),
        )
        case_id = cur.lastrowid
        cur = conn.execute(
            "INSERT INTO patterns (name, type, keywords, pattern, weight) "
            "VALUES (?, 'PRESENCE', ?, ?, 1.0)",
            (pattern_name, json.dumps(["kernel"]), regex),
        )
        pattern_id = cur.lastrowid
        conn.execute(
            "INSERT INTO case_patterns (case_id, pattern_id) VALUES (?, ?)",
            (case_id, pattern_id),
        )
    return case_id, pattern_id


def _pipeline(dbp) -> Pipeline:
    return Pipeline(
        db_path=dbp, reflect=False, save_history=False,
        definite_threshold=0.5, llm_model="test-model",
    )


def test_fallback_reattributes_to_case_that_actually_owns_the_fired_pattern(tmp_path):
    """핵심 회귀 — fallback 채택 시 matched_case 는 실제로 패턴을 가진 케이스여야
    하고, 애초에 자기 패턴이 하나도 안 걸린 원래 후보로 남으면 안 된다.
    """
    dbp = tmp_path / "t.db"
    db.init_db(dbp)

    # 케이스A: 패턴이 이 로그와 전혀 무관 (자기 패턴 점수 0 → fallback 유발)
    case_a_id, _ = _insert_case_with_pattern(dbp, "케이스A", "PATTERN-A", "this-never-matches-xyz")
    # 케이스B: 패턴이 실제로 이 로그와 일치 (fallback 전역 재검색에서 걸림)
    case_b_id, _ = _insert_case_with_pattern(dbp, "케이스B", "PATTERN-B", "kernel panic")

    pipeline = _pipeline(dbp)
    matched_case_a = pipeline._kb_search.load_case_by_id(case_a_id)
    assert matched_case_a is not None
    assert matched_case_a.name == "케이스A"

    l_normalized = [LogLine(raw="[1.000000] kernel panic detected", timestamp=1.0,
                             message="kernel panic detected")]

    # "케이스A 자체 패턴 점수 낮음(0%)" 상태를 그대로 시뮬레이션 — 실제 배선에서
    # run() 이 _run_stage3_4 로 이미 계산해 넘기는 값과 동일한 형태.
    original_match_result = MatchResult(
        matched=[], unmatched=[PatternResult(name="PATTERN-A", type="PRESENCE", matched=False, weight=1.0)],
        score=0.0,
    )

    (new_matched_case, new_entries, new_result,
     new_minority, fallback_original_score) = pipeline._run_fallback(
        matched_case    = matched_case_a,
        match_result    = original_match_result,
        refined_entries = [],
        l_normalized    = l_normalized,
        warnings        = [],
        notify          = lambda *a, **k: None,
        logger          = AnalysisLogger(enabled=False, db_path=dbp),
        chip            = None,
    )

    assert fallback_original_score == 0.0
    assert new_matched_case is not None
    # 핵심 단언 — 매칭 케이스가 실제로 패턴을 가진 케이스B 여야 한다.
    assert new_matched_case.case_id == case_b_id
    assert new_matched_case.name == "케이스B"
    assert new_matched_case.case_id != case_a_id
    # 매칭 패턴도 실제로 케이스B 소유 패턴이어야 한다.
    matched_names = [p.name for p in new_result.matched]
    assert "PATTERN-B" in matched_names
    assert new_result.score > 0


def test_fallback_original_candidate_appears_as_minority_when_still_relevant(tmp_path):
    """원래 후보가 자기 패턴으로 재매칭해도 여전히 score>0 이면(그냥 threshold
    미만이었을 뿐) minority 로 남아야 한다 — 정보가 사라지면 안 된다.

    케이스A 는 패턴을 2개 갖게 해 그 중 하나만 이번 로그와 일치하도록
    구성한다(재확인 시 score=0.5) — 케이스B 는 패턴 1개가 완전히 일치해
    score=1.0 이 되므로, 동점(1.0 vs 1.0) 없이 케이스B 가 결정적으로
    승리하는 걸 검증할 수 있다. (동점이면 _run_stage3_4 의 stable sort 상
    후보 리스트에 먼저 들어간 원래 후보가 이겨버려 이 테스트의 의도와
    무관하게 통과/실패가 갈린다 — 그래서 동점을 피하도록 설계함.)
    """
    dbp = tmp_path / "t.db"
    db.init_db(dbp)

    case_a_id, pattern_a1_id = _insert_case_with_pattern(dbp, "케이스A", "PATTERN-A1", "kernel")
    with db.get_conn(dbp) as conn:
        cur = conn.execute(
            "INSERT INTO patterns (name, type, keywords, pattern, weight) "
            "VALUES ('PATTERN-A2', 'PRESENCE', ?, ?, 1.0)",
            (json.dumps(["kernel"]), "this-never-matches-xyz"),
        )
        pattern_a2_id = cur.lastrowid
        conn.execute(
            "INSERT INTO case_patterns (case_id, pattern_id) VALUES (?, ?)",
            (case_a_id, pattern_a2_id),
        )
    case_b_id, _ = _insert_case_with_pattern(dbp, "케이스B", "PATTERN-B", "kernel panic")

    pipeline = _pipeline(dbp)
    matched_case_a = pipeline._kb_search.load_case_by_id(case_a_id)

    l_normalized = [LogLine(raw="[1.000000] kernel panic detected", timestamp=1.0,
                             message="kernel panic detected")]

    original_match_result = MatchResult(
        matched=[], unmatched=[
            PatternResult(name="PATTERN-A1", type="PRESENCE", matched=False, weight=1.0),
            PatternResult(name="PATTERN-A2", type="PRESENCE", matched=False, weight=1.0),
        ],
        score=0.0,
    )

    (new_matched_case, _, _, new_minority, _) = pipeline._run_fallback(
        matched_case=matched_case_a, match_result=original_match_result,
        refined_entries=[], l_normalized=l_normalized, warnings=[],
        notify=lambda *a, **k: None, logger=AnalysisLogger(enabled=False, db_path=dbp), chip=None,
    )

    assert new_matched_case.case_id == case_b_id
    # 케이스A 도 "kernel" 패턴(PATTERN-A1)이 걸려 score=0.5>0 이므로 minority 로 남아야 한다.
    minority_ids = [mr.matched_case.case_id for mr in new_minority]
    assert case_a_id in minority_ids
    minority_a = next(mr for mr in new_minority if mr.matched_case.case_id == case_a_id)
    assert minority_a.match_result.score == 0.5


def test_fallback_not_triggered_when_own_score_sufficient(tmp_path):
    """트리거 조건 자체는 그대로 — 자기 패턴 점수가 threshold 이상이면 fallback
    이 아예 발동 안 하고 원래 값을 그대로 반환한다(None 시그널)."""
    dbp = tmp_path / "t.db"
    db.init_db(dbp)
    case_a_id, _ = _insert_case_with_pattern(dbp, "케이스A", "PATTERN-A", "kernel panic")
    pipeline = _pipeline(dbp)
    matched_case_a = pipeline._kb_search.load_case_by_id(case_a_id)

    good_match_result = MatchResult(
        matched=[PatternResult(name="PATTERN-A", type="PRESENCE", matched=True, weight=1.0)],
        unmatched=[], score=0.9,
    )

    result = pipeline._run_fallback(
        matched_case=matched_case_a, match_result=good_match_result,
        refined_entries=[], l_normalized=[], warnings=[],
        notify=lambda *a, **k: None, logger=AnalysisLogger(enabled=False, db_path=dbp), chip=None,
    )

    assert result == (matched_case_a, [], good_match_result, None, None)


def test_fallback_orphan_pattern_returns_no_case(tmp_path):
    """고아 패턴(어느 케이스에도 안 연결된 패턴)만 fallback 에서 걸리는 극히
    드문 경우 — 특정 케이스로 귀속시키지 않는다(추측으로 케이스를 지어내지
    않음). 원래 후보 자신의 패턴도 이 로그와 무관하므로 재확인해도 안 걸린다.
    """
    dbp = tmp_path / "t.db"
    db.init_db(dbp)
    case_a_id, _ = _insert_case_with_pattern(dbp, "케이스A", "PATTERN-A", "this-never-matches-xyz")
    # 고아 패턴 — case_patterns 연결 없이 patterns 테이블에만 존재.
    with db.get_conn(dbp) as conn:
        conn.execute(
            "INSERT INTO patterns (name, type, keywords, pattern, weight) "
            "VALUES ('ORPHAN', 'PRESENCE', ?, ?, 1.0)",
            (json.dumps(["kernel"]), "kernel panic"),
        )

    pipeline = _pipeline(dbp)
    matched_case_a = pipeline._kb_search.load_case_by_id(case_a_id)

    l_normalized = [LogLine(raw="[1.000000] kernel panic detected", timestamp=1.0,
                             message="kernel panic detected")]
    original_match_result = MatchResult(
        matched=[], unmatched=[PatternResult(name="PATTERN-A", type="PRESENCE", matched=False, weight=1.0)],
        score=0.0,
    )

    (new_matched_case, new_entries, new_result,
     new_minority, fallback_original_score) = pipeline._run_fallback(
        matched_case=matched_case_a, match_result=original_match_result,
        refined_entries=[], l_normalized=l_normalized, warnings=[],
        notify=lambda *a, **k: None, logger=AnalysisLogger(enabled=False, db_path=dbp), chip=None,
    )

    assert fallback_original_score == 0.0
    # 최종 결과는 원래 후보(A)의 자기 패턴만 재확인한 것이다 — ORPHAN 패턴은
    # 어느 케이스에도 안 걸려 후보 풀에 반영되지 않고, A 의 패턴은 이 로그와
    # 무관해 재확인해도 안 걸린다. 그래서 "케이스 없음"과 "패턴 없음"이 같이 간다.
    assert new_matched_case is None
    assert new_result.matched == []
    assert new_result.score == 0.0
    assert new_minority == []
