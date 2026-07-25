"""core/pipeline.py Pipeline._run_fallback() — 전역 재검색에서 매칭된 패턴을
원 소속 케이스 기준으로 재채점하는지 검증한다.

배경: fallback 은 케이스 자기 패턴 점수가 낮을 때 DB 전체 패턴으로 재시도
한다. 재시도 결과의 score 분모를 "DB 전체 패턴 weight 합"으로 그대로 쓰면,
매칭된 패턴이 사실 어떤 케이스의 유일/핵심 증거였어도 그 케이스와 무관한
다른 패턴들 때문에 점수가 부당히 희석된다 — 같은 증거인데 Stage 2가 처음
부터 그 케이스를 후보로 골랐을 때(score=1.0, "유사문제")와 fallback을
거쳤을 때(희석된 낮은 score, "불확실")의 결과가 달라지는 모순이 생긴다.

`_rescope_fallback_to_owning_case`는 매칭된 패턴의 원 소속 케이스를 찾아
그 케이스 자기 패턴만으로 다시 채점하고 더 높은 쪽을 채택한다. matched_case
자체는 여전히 재귀속하지 않는다(§4 불변식 1) — reference_cases로만 노출.
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


def _insert_pattern(dbp, name: str, regex: str, weight: float = 1.0) -> int:
    with db.get_conn(dbp) as conn:
        cur = conn.execute(
            "INSERT INTO patterns (name, type, keywords, pattern, weight) "
            "VALUES (?, 'PRESENCE', ?, ?, ?)",
            (name, json.dumps(["kernel"]), regex, weight),
        )
        return cur.lastrowid


def _link(dbp, case_id: int, pattern_id: int) -> None:
    with db.get_conn(dbp) as conn:
        conn.execute(
            "INSERT INTO case_patterns (case_id, pattern_id) VALUES (?, ?)",
            (case_id, pattern_id),
        )


def test_fallback_rescopes_diluted_score_to_owning_case(tmp_path, monkeypatch):
    """DB 전체 기준(희석) 점수는 threshold 미만이지만, 매칭된 패턴이 속한
    케이스(케이스B) 자기 패턴만으로 보면 그 패턴이 유일한 패턴이라 score=1.0
    — rescope 후에는 threshold 를 넘어 "유사문제"가 나와야 한다.

    (재현: 매칭 패턴이 있고 그게 유일한 패턴이면 거의 확정적으로 문제여야
    하는데, DB 전체 대비로 희석되어 "불확실"이 나오던 버그.)
    """
    dbp = tmp_path / "t.db"
    db.init_db(dbp)

    case_a_id = _insert_case(dbp, "케이스A")
    pattern_a = _insert_pattern(dbp, "PATTERN-A", "this-never-matches-xyz")
    _link(dbp, case_a_id, pattern_a)

    case_b_id = _insert_case(dbp, "케이스B")
    pattern_b = _insert_pattern(dbp, "PATTERN-B", "kernel panic")
    _link(dbp, case_b_id, pattern_b)

    monkeypatch.setattr(
        "core.report_generator.chat_stream",
        lambda **kwargs: "## 판정: 유사문제\n(테스트용 리포트)",
    )

    # DB 전체 기준 희석 점수(1/2=0.5)는 못 넘고, 케이스 기준 점수(1/1=1.0)는
    # 넘는 threshold 를 골라 rescope 의 효과를 명확히 드러낸다.
    pipeline = Pipeline(
        db_path=dbp, reflect=False, save_history=False,
        definite_threshold=0.9, llm_model="test-model",
    )

    result = pipeline.run(
        problem_text="kernel panic 재현",
        raw_logs={"kernel.log": "[1.000000] kernel panic detected\n"},
        pinned_case_name="케이스A",
    )

    assert result.verdict == "유사문제"
    assert result.match_result.score == 1.0
    assert [p.name for p in result.match_result.matched] == ["PATTERN-B"]

    # matched_case 는 여전히 재귀속하지 않는다 — §4 불변식 1 유지.
    assert result.matched_case is None
    # 대신 reference_cases 로 케이스B 가 노출된다.
    names = {c["name"] for c in result.reference_cases}
    assert names == {"케이스B"}


def test_fallback_rescope_does_not_inflate_when_owning_case_itself_partial(tmp_path, monkeypatch):
    """케이스B 자신도 패턴이 2개(하나만 매칭)라 rescope 해도 여전히 부분
    매칭이면 — 억지로 1.0으로 부풀리지 않고 그 케이스 자기 점수 그대로여야
    한다(0.5). DB 전체 희석(1/3)보다는 낫지만 여전히 threshold 미만."""
    dbp = tmp_path / "t.db"
    db.init_db(dbp)

    case_a_id = _insert_case(dbp, "케이스A")
    pattern_a = _insert_pattern(dbp, "PATTERN-A", "this-never-matches-xyz")
    _link(dbp, case_a_id, pattern_a)

    case_b_id = _insert_case(dbp, "케이스B")
    pattern_b1 = _insert_pattern(dbp, "PATTERN-B1", "kernel panic")
    pattern_b2 = _insert_pattern(dbp, "PATTERN-B2", "this-also-never-matches-xyz")
    _link(dbp, case_b_id, pattern_b1)
    _link(dbp, case_b_id, pattern_b2)

    monkeypatch.setattr(
        "core.report_generator.chat_stream",
        lambda **kwargs: "## 판정: 불확실\n(테스트용 리포트)",
    )

    pipeline = Pipeline(
        db_path=dbp, reflect=False, save_history=False,
        definite_threshold=0.9, llm_model="test-model",
    )

    result = pipeline.run(
        problem_text="kernel panic 재현",
        raw_logs={"kernel.log": "[1.000000] kernel panic detected\n"},
        pinned_case_name="케이스A",
    )

    assert result.verdict == "불확실"
    assert result.match_result.score == 0.5   # 케이스B 자기 점수(1/2), DB 전체(1/3=0.33)보다 높음


def test_fallback_rescope_picks_best_among_multiple_owning_cases(tmp_path, monkeypatch):
    """매칭된 패턴을 케이스B(패턴 1개, 자기 점수 1.0)와 케이스C(패턴 2개,
    자기 점수 0.5)가 공유하면 — 더 높은 케이스B 기준 점수를 채택한다."""
    dbp = tmp_path / "t.db"
    db.init_db(dbp)

    case_a_id = _insert_case(dbp, "케이스A")
    pattern_a = _insert_pattern(dbp, "PATTERN-A", "this-never-matches-xyz")
    _link(dbp, case_a_id, pattern_a)

    shared = _insert_pattern(dbp, "PATTERN-SHARED", "kernel panic")

    case_b_id = _insert_case(dbp, "케이스B")
    _link(dbp, case_b_id, shared)

    case_c_id = _insert_case(dbp, "케이스C")
    pattern_c2 = _insert_pattern(dbp, "PATTERN-C2", "this-also-never-matches-xyz")
    _link(dbp, case_c_id, shared)
    _link(dbp, case_c_id, pattern_c2)

    monkeypatch.setattr(
        "core.report_generator.chat_stream",
        lambda **kwargs: "## 판정: 유사문제\n(테스트용 리포트)",
    )

    pipeline = Pipeline(
        db_path=dbp, reflect=False, save_history=False,
        definite_threshold=0.9, llm_model="test-model",
    )

    result = pipeline.run(
        problem_text="kernel panic 재현",
        raw_logs={"kernel.log": "[1.000000] kernel panic detected\n"},
        pinned_case_name="케이스A",
    )

    assert result.verdict == "유사문제"
    assert result.match_result.score == 1.0
