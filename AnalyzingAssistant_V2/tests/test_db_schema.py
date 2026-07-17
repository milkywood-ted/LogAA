"""core/db.py — 스키마·PRAGMA (§9-7 WAL / §9-1 is_required 제거 / §9-4 noise_patterns 제거)."""

import core.db as db


def test_wal_pragmas_applied(tmp_path):
    dbp = tmp_path / "t.db"
    db.init_db(dbp)
    with db.get_conn(dbp) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 10000
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1   # NORMAL


def test_foreign_keys_on(tmp_path):
    dbp = tmp_path / "t.db"
    db.init_db(dbp)
    with db.get_conn(dbp) as conn:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_is_required_column_removed(tmp_path):
    dbp = tmp_path / "t.db"
    db.init_db(dbp)
    with db.get_conn(dbp) as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(patterns)")}
    assert "is_required" not in cols
    assert "weight" in cols   # 나머지 공통 컬럼은 유지


def test_noise_patterns_table_removed(tmp_path):
    dbp = tmp_path / "t.db"
    db.init_db(dbp)
    with db.get_conn(dbp) as conn:
        tables = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
    assert "noise_patterns" not in tables
    for expected in ("patterns", "cases", "case_patterns", "master_rules",
                     "history", "domain_knowledge"):
        assert expected in tables


def test_case_patterns_cascade_on_case_delete(tmp_path):
    dbp = tmp_path / "t.db"
    db.init_db(dbp)
    with db.get_conn(dbp) as conn:
        conn.execute("INSERT INTO cases (name, description, keywords, verdict) "
                     "VALUES ('C', '', '[]', 'no_defect')")
        cid = conn.execute("SELECT id FROM cases").fetchone()["id"]
        conn.execute("INSERT INTO patterns (name, type, keywords) VALUES ('P', 'PRESENCE', '[]')")
        pid = conn.execute("SELECT id FROM patterns").fetchone()["id"]
        conn.execute("INSERT INTO case_patterns (case_id, pattern_id) VALUES (?, ?)", (cid, pid))
    # 케이스 삭제 → 연결 CASCADE 제거
    with db.get_conn(dbp) as conn:
        conn.execute("DELETE FROM cases WHERE id=?", (cid,))
    with db.get_conn(dbp) as conn:
        assert conn.execute("SELECT COUNT(*) FROM case_patterns").fetchone()[0] == 0
