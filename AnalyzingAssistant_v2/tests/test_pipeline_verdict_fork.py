"""core/pipeline.py Pipeline.run() — "불확실"/"유사문제 없음" 판정에서는 Stage 5(LLM
리포트)·Stage 6(Reflection)·이력 저장을 건너뛰고 즉시 반환하는지 검증한다.

배경(분석 리포트 개선 설계 §2-1): verdict는 match_result(점수·매칭 패턴 유무)만
보는 순수 계산이라 Stage 3/4 + Fallback 직후 이미 알 수 있다 — LLM을 부를 필요가
없다. "불확실"/"유사문제 없음"은 사용자의 후속 선택이 나와야 완결되는 미완결
상태이므로, 그 전에 LLM 호출·이력 저장을 해버리면 미완결 상태를 완결된 것처럼
다루는 것이다. 이 테스트는 chat_stream 이 호출되면 즉시 실패하도록 만들어
"LLM이 실제로 안 불렸다"를 직접 증명한다(출력값만 보고 추정하지 않는다).
"""

import json

import core.db as db
from core.pipeline import Pipeline


def _fail_if_llm_called(**kwargs):
    raise AssertionError(
        "'불확실'/'유사문제 없음' 경로에서는 LLM(chat_stream)이 호출되면 안 된다"
    )


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


def test_run_stops_before_stage5_for_uncertain(tmp_path, monkeypatch):
    """패턴 2개 중 1개만 매칭(score=0.5) + definite_threshold=0.9 → '불확실'."""
    dbp = tmp_path / "t.db"
    db.init_db(dbp)

    case_id = _insert_case(dbp, "케이스A")
    matching = _insert_pattern(dbp, "PATTERN-MATCH", "kernel panic")
    nonmatching = _insert_pattern(dbp, "PATTERN-MISS", "this-never-matches-xyz")
    _link(dbp, case_id, matching)
    _link(dbp, case_id, nonmatching)

    monkeypatch.setattr("core.report_generator.chat_stream", _fail_if_llm_called)

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
    assert result.report_md == ""
    assert result.history_id is None
    assert result.match_result.score == 0.5

    with db.get_conn(dbp) as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM history").fetchone()["n"]
    assert count == 0


def test_run_stops_before_stage5_for_no_similar_problem(tmp_path, monkeypatch):
    """어떤 패턴도(케이스 고유·전역 모두) 매칭 안 됨 → '유사문제 없음'."""
    dbp = tmp_path / "t.db"
    db.init_db(dbp)

    case_id = _insert_case(dbp, "케이스A")
    pattern_id = _insert_pattern(dbp, "PATTERN-A", "this-never-matches-xyz")
    _link(dbp, case_id, pattern_id)

    monkeypatch.setattr("core.report_generator.chat_stream", _fail_if_llm_called)
    monkeypatch.setattr("core.pattern_generator.PatternGenerator.generate", _fail_if_llm_called)

    pipeline = Pipeline(
        db_path=dbp, reflect=False, save_history=False,
        definite_threshold=0.5, llm_model="test-model",
    )
    result = pipeline.run(
        problem_text="원인 불명 재현",
        raw_logs={"kernel.log": "[1.000000] totally unrelated line\n"},
        pinned_case_name="케이스A",
    )

    assert result.verdict == "유사문제 없음"
    assert result.report_md == ""
    assert result.history_id is None
    assert result.kb_suggestion is None   # PatternGenerator 도 자동 호출 안 됨

    with db.get_conn(dbp) as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM history").fetchone()["n"]
    assert count == 0


def test_run_still_completes_normally_for_유사문제(tmp_path, monkeypatch):
    """회귀 방지 — '유사문제'는 이 변경으로 영향받지 않는다(Stage 5/6/저장 그대로)."""
    dbp = tmp_path / "t.db"
    db.init_db(dbp)

    case_id = _insert_case(dbp, "케이스A")
    pattern_id = _insert_pattern(dbp, "PATTERN-A", "kernel panic")
    _link(dbp, case_id, pattern_id)

    monkeypatch.setattr(
        "core.report_generator.chat_stream",
        lambda **kwargs: "## 판정: 유사문제\n(테스트용 리포트)",
    )

    pipeline = Pipeline(
        db_path=dbp, reflect=False, save_history=True,
        definite_threshold=0.5, llm_model="test-model",
    )
    result = pipeline.run(
        problem_text="kernel panic 재현",
        raw_logs={"kernel.log": "[1.000000] kernel panic detected\n"},
        pinned_case_name="케이스A",
    )

    assert result.verdict == "유사문제"
    assert result.report_md != ""
    assert result.history_id is not None
