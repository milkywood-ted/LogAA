"""
core/pattern_db.py

패턴 DB 삽입 공유 헬퍼.

Page 3 (케이스+패턴 통합 입력)과 Page 4 (패턴 관리) 모두에서 사용한다.
conn 을 전달하면 호출자의 트랜잭션 안에서 실행하고, 생략하면 독립 트랜잭션으로 실행한다.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from core.db import DB_PATH, get_conn


def insert_pattern(
    p: dict,
    *,
    conn: sqlite3.Connection | None = None,
    db_path: Path = DB_PATH,
) -> int:
    """
    패턴 dict 를 DB 에 삽입하고 새 id 를 반환한다.

    Parameters
    ----------
    p       : 패턴 정보 dict. 필수 키: name, type.
              type 별 추가 키:
                PRESENCE  — pattern, event_dedup_window_sec
                SEQUENCE  — steps (list[str]), step_dedup, non_overlapping
                WINDOW    — pattern, window_sec, count_threshold, count_unique_only
                ABSENCE   — trigger_pattern, absent_pattern, window_sec
                COMPOSITE — operator, components (list[str] 패턴 이름)
    conn    : 호출자가 이미 열어둔 Connection. 전달하면 그 트랜잭션 안에서 실행.
    db_path : conn 이 None 일 때 사용할 DB 경로.
    """
    if conn is not None:
        return _do_insert(p, conn)
    with get_conn(db_path) as c:
        return _do_insert(p, c)


def _do_insert(p: dict, conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        """
        INSERT INTO patterns
          (name, type, description, keywords,
           pattern, event_dedup_window_sec,
           step_dedup, non_overlapping,
           window_sec, count_threshold, count_unique_only,
           trigger_pattern, absent_pattern,
           operator, weight, analysis_guidelines)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            p["name"],
            p["type"],
            p.get("description", ""),
            json.dumps(p.get("keywords", []), ensure_ascii=False),
            p.get("pattern"),
            p.get("event_dedup_window_sec"),
            int(bool(p.get("step_dedup", False))),
            int(bool(p.get("non_overlapping", False))),
            p.get("window_sec"),
            p.get("count_threshold"),
            int(bool(p.get("count_unique_only", False))),
            p.get("trigger_pattern"),
            p.get("absent_pattern"),
            p.get("operator"),
            float(p.get("weight", 1.0)),
            p.get("analysis_guidelines", ""),
        ),
    )
    pid = cur.lastrowid

    for i, step in enumerate(p.get("steps") or []):
        conn.execute(
            "INSERT INTO pattern_steps (pattern_id, step_order, pattern) VALUES (?,?,?)",
            (pid, i, step),
        )

    for i, comp_name in enumerate(p.get("components") or []):
        ref = conn.execute(
            "SELECT id FROM patterns WHERE name=?", (comp_name,)
        ).fetchone()
        if ref:
            conn.execute(
                "INSERT INTO pattern_components "
                "(pattern_id, component_order, ref_pattern_id) VALUES (?,?,?)",
                (pid, i, ref["id"]),
            )

    return pid
