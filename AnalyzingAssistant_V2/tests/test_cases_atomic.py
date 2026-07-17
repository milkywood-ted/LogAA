"""api/router/cases.py — 케이스+패턴 연결 원자적 저장 (§9-6 검증 정식화).

임시 DB + `_kb` 스텁으로 격리한다 (ChromaDB·임베딩 미접촉).
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import core.db as db
import api.router.cases as cm


@pytest.fixture
def cases_env(tmp_path, monkeypatch):
    """임시 DB에 패턴 3개를 심고, cases 모듈의 DB_PATH·_kb를 격리한다."""
    dbp = tmp_path / "t.db"
    db.init_db(dbp)
    with db.get_conn(dbp) as conn:
        for n in ("P1", "P2", "P3"):
            conn.execute("INSERT INTO patterns (name, type, keywords) VALUES (?, 'PRESENCE', '[]')", (n,))
        pids = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM patterns")}

    monkeypatch.setattr(cm, "DB_PATH", dbp)
    # ChromaDB 동기화 스텁 — 임베딩(네트워크) 차단
    monkeypatch.setattr(cm, "_kb", SimpleNamespace(
        add_case=lambda *a, **k: None,
        remove_case=lambda *a, **k: None,
    ))
    return {"db": dbp, "pids": pids}


def _links(dbp, cid):
    with db.get_conn(dbp) as conn:
        return sorted(r["pattern_id"] for r in conn.execute(
            "SELECT pattern_id FROM case_patterns WHERE case_id=?", (cid,)))


def _case_count(dbp):
    with db.get_conn(dbp) as conn:
        return conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0]


def _req(**kw):
    kw.setdefault("verdict", "no_defect")
    return cm.CaseSaveRequest(**kw)


def test_create_with_pattern_links(cases_env):
    p = cases_env["pids"]
    created = cm.create_case(_req(name="케이스A", pattern_ids=[p["P1"], p["P2"]]))
    assert _links(cases_env["db"], created["id"]) == sorted([p["P1"], p["P2"]])


def test_create_rolls_back_on_bad_pattern_id(cases_env):
    """★원자성★ 존재하지 않는 패턴 id → 422 + 케이스 생성 자체가 롤백."""
    p = cases_env["pids"]
    before = _case_count(cases_env["db"])
    with pytest.raises(HTTPException) as e:
        cm.create_case(_req(name="롤백케이스", pattern_ids=[p["P1"], 9999]))
    assert e.value.status_code == 422 and "9999" in str(e.value.detail)
    assert _case_count(cases_env["db"]) == before   # 케이스가 남지 않음


def test_update_diffs_links(cases_env):
    p = cases_env["pids"]
    cid = cm.create_case(_req(name="케이스A", pattern_ids=[p["P1"], p["P2"]]))["id"]
    cm.update_case(cid, _req(name="케이스A", pattern_ids=[p["P2"], p["P3"]]))
    assert _links(cases_env["db"], cid) == sorted([p["P2"], p["P3"]])


def test_update_none_keeps_links(cases_env):
    p = cases_env["pids"]
    cid = cm.create_case(_req(name="케이스A", pattern_ids=[p["P1"]]))["id"]
    cm.update_case(cid, _req(name="케이스A"))   # pattern_ids 생략 → 무변경
    assert _links(cases_env["db"], cid) == [p["P1"]]


def test_update_empty_list_clears_and_dedup(cases_env):
    p = cases_env["pids"]
    cid = cm.create_case(_req(name="케이스A", pattern_ids=[p["P1"], p["P2"]]))["id"]
    cm.update_case(cid, _req(name="케이스A", pattern_ids=[]))
    assert _links(cases_env["db"], cid) == []
    cm.update_case(cid, _req(name="케이스A", pattern_ids=[p["P1"], p["P1"]]))  # 중복
    assert _links(cases_env["db"], cid) == [p["P1"]]


def test_update_rolls_back_body_on_bad_link(cases_env):
    """★원자성★ 수정 중 연결 실패 → 본문 변경(이름)까지 롤백."""
    p = cases_env["pids"]
    cid = cm.create_case(_req(name="케이스A", pattern_ids=[p["P1"]]))["id"]
    with pytest.raises(HTTPException):
        cm.update_case(cid, _req(name="바뀐이름", pattern_ids=[8888]))
    with db.get_conn(cases_env["db"]) as conn:
        name = conn.execute("SELECT name FROM cases WHERE id=?", (cid,)).fetchone()["name"]
    assert name == "케이스A"   # 이름이 롤백됨


def test_duplicate_name_conflict(cases_env):
    cm.create_case(_req(name="중복"))
    with pytest.raises(HTTPException) as e:
        cm.create_case(_req(name="중복"))
    assert e.value.status_code == 409
