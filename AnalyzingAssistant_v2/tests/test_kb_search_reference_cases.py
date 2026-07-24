"""core/kb_search.py KBSearch.find_cases_by_pattern_names — case_patterns 역조회
(패턴 → 케이스). 분석 리포트 개선 PR 3.
"""

import json

import core.db as db
from core.kb_search import KBSearch


def _insert_case(dbp, name: str, verdict: str | None = None) -> int:
    with db.get_conn(dbp) as conn:
        cur = conn.execute(
            "INSERT INTO cases (name, description, keywords, verdict) VALUES (?, ?, ?, ?)",
            (name, f"{name} 설명", json.dumps([]), verdict),
        )
        return cur.lastrowid


def _insert_pattern(dbp, name: str) -> int:
    with db.get_conn(dbp) as conn:
        cur = conn.execute(
            "INSERT INTO patterns (name, type, keywords, pattern, weight) "
            "VALUES (?, 'PRESENCE', ?, ?, 1.0)",
            (name, json.dumps([]), "x"),
        )
        return cur.lastrowid


def _link(dbp, case_id: int, pattern_id: int) -> None:
    with db.get_conn(dbp) as conn:
        conn.execute(
            "INSERT INTO case_patterns (case_id, pattern_id) VALUES (?, ?)",
            (case_id, pattern_id),
        )


def test_returns_empty_list_for_empty_input(tmp_path):
    dbp = tmp_path / "t.db"
    db.init_db(dbp)
    kb = KBSearch(db_path=dbp, chroma_path=tmp_path / "chroma")
    assert kb.find_cases_by_pattern_names([]) == []


def test_finds_single_case_owning_pattern(tmp_path):
    dbp = tmp_path / "t.db"
    db.init_db(dbp)
    case_id = _insert_case(dbp, "케이스A", verdict="defect")
    pattern_id = _insert_pattern(dbp, "PATTERN-A")
    _link(dbp, case_id, pattern_id)

    kb = KBSearch(db_path=dbp, chroma_path=tmp_path / "chroma")
    results = kb.find_cases_by_pattern_names(["PATTERN-A"])

    assert len(results) == 1
    assert results[0]["case_id"] == case_id
    assert results[0]["name"] == "케이스A"
    assert results[0]["verdict"] == "defect"


def test_pattern_shared_by_multiple_cases_returns_all(tmp_path):
    """n:n — 패턴 하나를 여러 케이스가 공유하는 게 정상이며, 단일 케이스로 안 좁힌다."""
    dbp = tmp_path / "t.db"
    db.init_db(dbp)
    case_b = _insert_case(dbp, "케이스B")
    case_c = _insert_case(dbp, "케이스C")
    shared = _insert_pattern(dbp, "PATTERN-SHARED")
    _link(dbp, case_b, shared)
    _link(dbp, case_c, shared)

    kb = KBSearch(db_path=dbp, chroma_path=tmp_path / "chroma")
    results = kb.find_cases_by_pattern_names(["PATTERN-SHARED"])

    assert {r["name"] for r in results} == {"케이스B", "케이스C"}


def test_case_not_owning_any_matched_pattern_is_excluded(tmp_path):
    dbp = tmp_path / "t.db"
    db.init_db(dbp)
    case_a = _insert_case(dbp, "케이스A")
    pattern_a = _insert_pattern(dbp, "PATTERN-A")
    _link(dbp, case_a, pattern_a)
    _insert_pattern(dbp, "PATTERN-UNRELATED")  # 링크 없음

    kb = KBSearch(db_path=dbp, chroma_path=tmp_path / "chroma")
    results = kb.find_cases_by_pattern_names(["PATTERN-UNRELATED"])

    assert results == []


def test_no_duplicate_case_when_multiple_matched_patterns_belong_to_same_case(tmp_path):
    dbp = tmp_path / "t.db"
    db.init_db(dbp)
    case_a = _insert_case(dbp, "케이스A")
    p1 = _insert_pattern(dbp, "PATTERN-1")
    p2 = _insert_pattern(dbp, "PATTERN-2")
    _link(dbp, case_a, p1)
    _link(dbp, case_a, p2)

    kb = KBSearch(db_path=dbp, chroma_path=tmp_path / "chroma")
    results = kb.find_cases_by_pattern_names(["PATTERN-1", "PATTERN-2"])

    assert len(results) == 1
    assert results[0]["case_id"] == case_a
