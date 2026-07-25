"""core/pipeline.py Pipeline._rescore_global_matches() — fallback·MISS 공용
전역 재매칭에서 매칭된 패턴이 여러 개일 때(케이스 연결 패턴 + orphan 패턴이
섞여도 포함) 각 후보의 possibility를 재채점하고, 승자 외 나머지를
minority_reports(실제 케이스)/unclassified_patterns(orphan)로 분리하는지
검증한다.

배경: `Document/Fallback 점수 재채점/fallback·MISS 점수 재채점 설계.md` §4~§6.
  - 케이스에 연결된 패턴은 전역 매칭 개수와 무관하게 항상 그 케이스 자기
    비율을 쓴다(§4 정정) — orphan 패턴만 "자기 자신 하나만 담은 가상 케이스"로
    간주해 분모=분자로 확정한다(§4-1).
  - 매칭 후보가 2개 이상이면 최고점을 메인으로, 나머지는 실제 케이스면
    minority_reports로, orphan이면 unclassified_patterns로 분리한다(§5).
  - 동점이면 케이스 정보 있는 쪽을 메인으로 우선한다(§6-2).
  - 후보 전체가 orphan이면(2개 이상) 승자를 가리지 않고 전부 동등한
    후보로 취급한다(§6-1).
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


def _insert_pattern(dbp, name: str, regex: str, weight: float = 1.0, keywords=None) -> int:
    with db.get_conn(dbp) as conn:
        cur = conn.execute(
            "INSERT INTO patterns (name, type, keywords, pattern, weight) "
            "VALUES (?, 'PRESENCE', ?, ?, ?)",
            (name, json.dumps(keywords or ["kernel"]), regex, weight),
        )
        return cur.lastrowid


def _link(dbp, case_id: int, pattern_id: int) -> None:
    with db.get_conn(dbp) as conn:
        conn.execute(
            "INSERT INTO case_patterns (case_id, pattern_id) VALUES (?, ?)",
            (case_id, pattern_id),
        )


def _pipeline(dbp, threshold=0.9):
    return Pipeline(
        db_path=dbp, reflect=False, save_history=False,
        definite_threshold=threshold, llm_model="test-model",
    )


def _stub_llm(monkeypatch, verdict_line="유사문제"):
    monkeypatch.setattr(
        "core.report_generator.chat_stream",
        lambda **kwargs: f"## 판정: {verdict_line}\n(테스트용 리포트)",
    )


# ── fallback 경로 ────────────────────────────────────────────────────────────

def test_fallback_orphan_pattern_alone_confirms_without_ratio(tmp_path, monkeypatch):
    """orphan 패턴 1개만 매칭되면(후보 1개, 모호함 없음) 비율 계산 없이 그대로
    확정(score=1.0)되고, 별도 unclassified_patterns/minority_reports 는 비어있다."""
    dbp = tmp_path / "t.db"
    db.init_db(dbp)

    case_a_id = _insert_case(dbp, "케이스A")
    _link(dbp, case_a_id, _insert_pattern(dbp, "PATTERN-A", "this-never-matches-xyz"))
    _insert_pattern(dbp, "PATTERN-ORPHAN", "kernel panic")   # 케이스 연결 없음

    _stub_llm(monkeypatch)
    pipeline = _pipeline(dbp)

    result = pipeline.run(
        problem_text="kernel panic 재현",
        raw_logs={"kernel.log": "[1.000000] kernel panic detected\n"},
        pinned_case_name="케이스A",
    )

    assert result.verdict == "유사문제"
    assert result.match_result.score == 1.0
    assert [p.name for p in result.match_result.matched] == ["PATTERN-ORPHAN"]
    assert result.matched_case is None
    assert result.minority_reports == []
    assert result.unclassified_patterns == []


def test_fallback_tie_prefers_case_and_orphan_becomes_unclassified(tmp_path, monkeypatch):
    """케이스B(패턴 1개, 재채점 시 1.0)와 orphan 패턴(1.0)이 동점이면 §6-2에
    따라 케이스 정보 있는 쪽이 메인으로 채택되고, orphan은 손실 후보로
    unclassified_patterns 에 담긴다(MinorityReport 는 matched_case 가 필수라
    orphan 을 담을 수 없음)."""
    dbp = tmp_path / "t.db"
    db.init_db(dbp)

    case_a_id = _insert_case(dbp, "케이스A")
    _link(dbp, case_a_id, _insert_pattern(dbp, "PATTERN-A", "this-never-matches-xyz"))

    case_b_id = _insert_case(dbp, "케이스B")
    _link(dbp, case_b_id, _insert_pattern(dbp, "PATTERN-B", "kernel panic"))

    _insert_pattern(dbp, "PATTERN-ORPHAN", "kernel panic")   # 케이스 연결 없음, 같은 로그에 매칭

    _stub_llm(monkeypatch)
    pipeline = _pipeline(dbp)

    result = pipeline.run(
        problem_text="kernel panic 재현",
        raw_logs={"kernel.log": "[1.000000] kernel panic detected\n"},
        pinned_case_name="케이스A",
    )

    assert result.verdict == "유사문제"
    assert result.match_result.score == 1.0
    assert [p.name for p in result.match_result.matched] == ["PATTERN-B"]   # 케이스B 가 메인
    assert result.matched_case is None   # §4 불변식 — 여전히 재귀속 안 함

    assert result.minority_reports == []
    assert [p.name for p in result.unclassified_patterns] == ["PATTERN-ORPHAN"]


def test_fallback_real_case_strictly_lower_score_still_becomes_minority(tmp_path, monkeypatch):
    """orphan(1.0)이 케이스B(패턴 2개 중 1개만 매칭 → 0.5)보다 순수하게 더
    높으면 동점이 아니므로 orphan 이 메인이 되고, 점수가 더 낮은 케이스B는
    minority_reports 로 밀린다."""
    dbp = tmp_path / "t.db"
    db.init_db(dbp)

    case_a_id = _insert_case(dbp, "케이스A")
    _link(dbp, case_a_id, _insert_pattern(dbp, "PATTERN-A", "this-never-matches-xyz"))

    case_b_id = _insert_case(dbp, "케이스B")
    _link(dbp, case_b_id, _insert_pattern(dbp, "PATTERN-B1", "kernel panic"))
    _link(dbp, case_b_id, _insert_pattern(dbp, "PATTERN-B2", "this-also-never-matches-xyz"))

    _insert_pattern(dbp, "PATTERN-ORPHAN", "kernel panic")

    _stub_llm(monkeypatch)
    pipeline = _pipeline(dbp)

    result = pipeline.run(
        problem_text="kernel panic 재현",
        raw_logs={"kernel.log": "[1.000000] kernel panic detected\n"},
        pinned_case_name="케이스A",
    )

    assert result.verdict == "유사문제"
    assert result.match_result.score == 1.0
    assert [p.name for p in result.match_result.matched] == ["PATTERN-ORPHAN"]
    assert result.unclassified_patterns == []

    assert len(result.minority_reports) == 1
    assert result.minority_reports[0].matched_case.name == "케이스B"
    assert result.minority_reports[0].match_result.score == 0.5


def test_fallback_all_orphan_patterns_form_equal_peer_group(tmp_path, monkeypatch):
    """§6-1 — 매칭된 패턴이 전부 orphan(2개 이상)이면 승자를 가리지 않는다.
    전부 동등한 확률의 서로 다른 문제 가능성이므로, match_result.matched 와
    unclassified_patterns 양쪽에 모두 나열된다."""
    dbp = tmp_path / "t.db"
    db.init_db(dbp)

    case_a_id = _insert_case(dbp, "케이스A")
    _link(dbp, case_a_id, _insert_pattern(dbp, "PATTERN-A", "this-never-matches-xyz"))

    _insert_pattern(dbp, "PATTERN-ORPHAN1", "kernel panic")
    _insert_pattern(dbp, "PATTERN-ORPHAN2", "oom-killer invoked", keywords=["oom-killer"])

    _stub_llm(monkeypatch)
    pipeline = _pipeline(dbp)

    result = pipeline.run(
        problem_text="여러 이상 신호 동시 재현",
        raw_logs={"kernel.log": "[1.000000] kernel panic detected\n[2.000000] oom-killer invoked\n"},
        pinned_case_name="케이스A",
    )

    assert result.verdict == "유사문제"
    assert result.match_result.score == 1.0
    assert result.matched_case is None
    assert {p.name for p in result.match_result.matched} == {"PATTERN-ORPHAN1", "PATTERN-ORPHAN2"}
    assert {p.name for p in result.unclassified_patterns} == {"PATTERN-ORPHAN1", "PATTERN-ORPHAN2"}
    assert result.minority_reports == []


# ── MISS 경로 (Stage 2 가 애초에 후보를 못 찾은 경우) ────────────────────────

def test_miss_path_orphan_pattern_alone(tmp_path, monkeypatch):
    """Stage 2 가 후보 자체를 못 찾아도(순수 MISS), 전역 재매칭에서 매칭된
    orphan 패턴 1개는 fallback 과 동일한 원리로 그대로 확정된다."""
    dbp = tmp_path / "t.db"
    db.init_db(dbp)

    _insert_pattern(dbp, "PATTERN-ORPHAN", "kernel panic")

    _stub_llm(monkeypatch)
    pipeline = _pipeline(dbp)
    monkeypatch.setattr(pipeline._kb_search, "search", lambda **kwargs: [])

    result = pipeline.run(
        problem_text="kernel panic 재현",
        raw_logs={"kernel.log": "[1.000000] kernel panic detected\n"},
    )

    assert result.verdict == "유사문제"
    assert result.match_result.score == 1.0
    assert [p.name for p in result.match_result.matched] == ["PATTERN-ORPHAN"]
    assert result.matched_case is None
    assert result.unclassified_patterns == []


def test_miss_path_tie_prefers_case_over_orphan(tmp_path, monkeypatch):
    """순수 MISS 에서도 케이스 연결 패턴과 orphan 패턴이 동점이면 §6-2 에
    따라 케이스 쪽이 메인이 된다 — fallback 경로와 동일한 재채점이 MISS
    경로에도 적용된다."""
    dbp = tmp_path / "t.db"
    db.init_db(dbp)

    case_b_id = _insert_case(dbp, "케이스B")
    _link(dbp, case_b_id, _insert_pattern(dbp, "PATTERN-B", "kernel panic"))
    _insert_pattern(dbp, "PATTERN-ORPHAN", "kernel panic")

    _stub_llm(monkeypatch)
    pipeline = _pipeline(dbp)
    monkeypatch.setattr(pipeline._kb_search, "search", lambda **kwargs: [])

    result = pipeline.run(
        problem_text="kernel panic 재현",
        raw_logs={"kernel.log": "[1.000000] kernel panic detected\n"},
    )

    assert result.verdict == "유사문제"
    assert result.match_result.score == 1.0
    assert [p.name for p in result.match_result.matched] == ["PATTERN-B"]
    assert result.matched_case is None
    assert [p.name for p in result.unclassified_patterns] == ["PATTERN-ORPHAN"]
