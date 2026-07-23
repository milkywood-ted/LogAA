"""core/pipeline.py Pipeline.run() — §4 불변식 1, fallback 채택 시 matched_case 를
근원(run())에서 비운다 (A안, 2026-07-23).

실사용 재현: "매칭 패턴은 맞게 나오는데 매칭 케이스가 다른 게 나온다." 케이스
자기 패턴 점수가 낮아 _run_fallback 이 전역 재매칭을 채택하면 evidence(패턴)의
출처가 matched_case 자신의 패턴이 아니게 된다(§4 불변식 1). B안(케이스
재귀속, case_patterns n:n 역조회)은 패턴이 여러 케이스에 걸쳐 있을 때 후보
풀 구성이 복잡해져 혼선 여지가 있다고 판단해 폐기하고, A안(단순히 matched_case
를 비움)으로 재구현했다 — 이후 모든 소비자가 이미 있는 "케이스 없음" 처리
경로를 그대로 타서 화면·리포트·이력이 항상 일관된다.

Pipeline.run() 전체를 실제로 실행해 재현한다(pinned_case_name 으로 벡터
검색/임베딩을 우회하고 chat_stream 만 스텁 — 추정이 아니라 실행 재현).
"""

import json

import core.db as db
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


def test_run_nulls_matched_case_when_fallback_adopts_unrelated_pattern(tmp_path, monkeypatch):
    """케이스A 에 pin 했지만 자기 패턴이 이번 로그와 무관해 fallback 이 발동하고,
    fallback 이 케이스B 소유 패턴을 전역에서 채택하면 — 최종 result.matched_case
    는 (엉뚱한 케이스A가 아니라) None 이어야 하고, 매칭 패턴에는 케이스B 의
    패턴이 그대로 보여야 한다.
    """
    dbp = tmp_path / "t.db"
    db.init_db(dbp)

    # 케이스A: 자기 패턴이 이 로그와 전혀 무관 (자기 패턴 점수 0 → fallback 유발)
    case_a_id, _ = _insert_case_with_pattern(dbp, "케이스A", "PATTERN-A", "this-never-matches-xyz")
    # 케이스B: 패턴이 실제로 이 로그와 일치 (fallback 전역 재검색에서 걸림)
    case_b_id, _ = _insert_case_with_pattern(dbp, "케이스B", "PATTERN-B", "kernel panic")

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

    # 핵심 단언 — fallback 채택 후 matched_case 는 엉뚱한 케이스A 로 남지
    # 않고 None 이어야 한다 (§4 불변식 1 — case 없음 + 패턴 있음 조합은
    # 항상 "케이스 없음" 경로로 일관되게 처리된다).
    assert result.matched_case is None

    # 매칭 패턴 자체는 fallback 이 실제로 찾아낸 케이스B 의 패턴이어야 한다
    # — evidence 는 사라지지 않는다.
    matched_names = [p.name for p in result.match_result.matched]
    assert "PATTERN-B" in matched_names
    assert result.match_result.score > 0

    # serialize_result 소비자(화면 "매칭 케이스" 패널 등)도 동일하게
    # case-less 로 직렬화되어야 앞뒤가 맞는다.
    serialized = result.matched_case
    assert serialized is None


def test_run_keeps_matched_case_when_own_score_sufficient(tmp_path, monkeypatch):
    """자기 패턴 점수가 이미 threshold 이상이면 fallback 자체가 발동하지 않고
    matched_case 가 그대로 살아있어야 한다 (회귀 방지 — A안이 정상 케이스까지
    억지로 비우면 안 된다)."""
    dbp = tmp_path / "t.db"
    db.init_db(dbp)
    case_a_id, _ = _insert_case_with_pattern(dbp, "케이스A", "PATTERN-A", "kernel panic")

    monkeypatch.setattr(
        "core.report_generator.chat_stream",
        lambda **kwargs: "## 판정: 문제\n(테스트용 리포트)",
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
    assert result.matched_case.case_id == case_a_id
    assert result.matched_case.name == "케이스A"
