"""core/pipeline.py Pipeline.run() — "유사문제"인데 matched_case 가 특정 안 되는
경우(§4 불변식), 매칭된 패턴을 가진 기존 케이스들을 reference_cases 로 채우는지
검증한다 (분석 리포트 개선 PR 3).

패턴 하나를 여러 케이스가 공유(case_patterns n:n)하는 게 정상 케이스이므로,
reference_cases 는 여러 건일 수 있고 특정 케이스 하나로 좁히지 않는다.
"""

import json

import core.db as db
from core.pipeline import Pipeline


def _insert_case(dbp, name: str) -> int:
    with db.get_conn(dbp) as conn:
        cur = conn.execute(
            "INSERT INTO cases (name, description, keywords) VALUES (?, ?, ?)",
            (name, f"{name} 설명", json.dumps(["kernel"])),
        )
        return cur.lastrowid


def _insert_pattern(dbp, name: str, regex: str) -> int:
    with db.get_conn(dbp) as conn:
        cur = conn.execute(
            "INSERT INTO patterns (name, type, keywords, pattern, weight) "
            "VALUES (?, 'PRESENCE', ?, ?, 1.0)",
            (name, json.dumps(["kernel"]), regex),
        )
        return cur.lastrowid


def _link(dbp, case_id: int, pattern_id: int) -> None:
    with db.get_conn(dbp) as conn:
        conn.execute(
            "INSERT INTO case_patterns (case_id, pattern_id) VALUES (?, ?)",
            (case_id, pattern_id),
        )


def test_reference_cases_lists_all_cases_sharing_the_matched_pattern(tmp_path, monkeypatch):
    """케이스A 에 pin 했지만 자기 패턴은 무관 → fallback 발동. fallback 이 전역에서
    찾은 패턴을 케이스B·케이스C 가 공유하면, matched_case=None 이어도 둘 다
    reference_cases 에 나와야 한다."""
    dbp = tmp_path / "t.db"
    db.init_db(dbp)

    case_a_id = _insert_case(dbp, "케이스A")
    pattern_a = _insert_pattern(dbp, "PATTERN-A", "this-never-matches-xyz")
    _link(dbp, case_a_id, pattern_a)

    case_b_id = _insert_case(dbp, "케이스B")
    case_c_id = _insert_case(dbp, "케이스C")
    shared_pattern = _insert_pattern(dbp, "PATTERN-SHARED", "kernel panic")
    _link(dbp, case_b_id, shared_pattern)
    _link(dbp, case_c_id, shared_pattern)

    monkeypatch.setattr(
        "core.report_generator.chat_stream",
        lambda **kwargs: "## 판정: 유사문제\n(테스트용 리포트)",
    )

    pipeline = Pipeline(
        db_path=dbp, reflect=False, save_history=False,
        definite_threshold=0.5, llm_model="test-model",
    )

    result = pipeline.run(
        problem_text="kernel panic 재현",
        raw_logs={"kernel.log": "[1.000000] kernel panic detected\n"},
        pinned_case_name="케이스A",
    )

    assert result.verdict == "유사문제"
    assert result.matched_case is None   # §4 불변식 — fallback 채택으로 비워짐

    names = {c["name"] for c in result.reference_cases}
    assert names == {"케이스B", "케이스C"}
    ids = {c["case_id"] for c in result.reference_cases}
    assert ids == {case_b_id, case_c_id}


def test_reference_cases_empty_when_matched_case_present(tmp_path, monkeypatch):
    """케이스가 특정된 경우(matched_case 존재)엔 reference_cases 를 채우지 않는다 —
    4-1(케이스 참조)과 4-2(참고 목록)는 상호 배타적."""
    dbp = tmp_path / "t.db"
    db.init_db(dbp)

    case_a_id = _insert_case(dbp, "케이스A")
    pattern_a = _insert_pattern(dbp, "PATTERN-A", "kernel panic")
    _link(dbp, case_a_id, pattern_a)

    monkeypatch.setattr(
        "core.report_generator.chat_stream",
        lambda **kwargs: "## 판정: 유사문제\n(테스트용 리포트)",
    )

    pipeline = Pipeline(
        db_path=dbp, reflect=False, save_history=False,
        definite_threshold=0.5, llm_model="test-model",
    )

    result = pipeline.run(
        problem_text="kernel panic 재현",
        raw_logs={"kernel.log": "[1.000000] kernel panic detected\n"},
        pinned_case_name="케이스A",
    )

    assert result.matched_case is not None
    assert result.reference_cases == []
