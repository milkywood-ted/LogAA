"""
api/job_store.py

SQLite 기반 job 상태 저장소.

기존 core/db.py 의 DB_PATH 를 그대로 사용하며,
jobs 테이블만 추가로 관리한다.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from core.db import DB_PATH, get_conn

# ── 스키마 ─────────────────────────────────────────────────────────────────────

_JOBS_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id     TEXT PRIMARY KEY,
    status     TEXT NOT NULL DEFAULT 'pending',  -- pending | running | done | error
    stage      TEXT NOT NULL DEFAULT '',          -- 현재 진행 stage 설명
    progress   INTEGER NOT NULL DEFAULT 0,        -- 0~100
    result     TEXT,                              -- JSON (done 시)
    error      TEXT,                              -- 에러 메시지 (error 시)
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
"""


def init_jobs_table(db_path: Path = DB_PATH) -> None:
    """jobs 테이블을 생성한다 (없을 때만)."""
    with get_conn(db_path) as conn:
        conn.executescript(_JOBS_SCHEMA)


# ── CRUD ──────────────────────────────────────────────────────────────────────

def create_job(db_path: Path = DB_PATH) -> str:
    """새 job을 생성하고 job_id를 반환한다."""
    job_id = str(uuid.uuid4())
    with get_conn(db_path) as conn:
        conn.execute(
            "INSERT INTO jobs (job_id, status) VALUES (?, 'pending')",
            (job_id,),
        )
    return job_id


def update_job_running(
    job_id: str,
    stage: str,
    progress: int,
    db_path: Path = DB_PATH,
) -> None:
    """job 상태를 running으로 업데이트한다."""
    with get_conn(db_path) as conn:
        conn.execute(
            """
            UPDATE jobs
            SET status = 'running',
                stage = ?,
                progress = ?,
                updated_at = ?
            WHERE job_id = ?
            """,
            (stage, progress, _now(), job_id),
        )


def update_job_done(
    job_id: str,
    result: dict[str, Any],
    db_path: Path = DB_PATH,
) -> None:
    """job 상태를 done으로 업데이트하고 결과를 저장한다."""
    with get_conn(db_path) as conn:
        conn.execute(
            """
            UPDATE jobs
            SET status = 'done',
                stage = '',
                progress = 100,
                result = ?,
                updated_at = ?
            WHERE job_id = ?
            """,
            (json.dumps(result, ensure_ascii=False), _now(), job_id),
        )


def update_job_error(
    job_id: str,
    error: str,
    db_path: Path = DB_PATH,
) -> None:
    """job 상태를 error로 업데이트한다."""
    with get_conn(db_path) as conn:
        conn.execute(
            """
            UPDATE jobs
            SET status = 'error',
                error = ?,
                updated_at = ?
            WHERE job_id = ?
            """,
            (error, _now(), job_id),
        )


def get_job(job_id: str, db_path: Path = DB_PATH) -> dict[str, Any] | None:
    """job 정보를 반환한다. 없으면 None."""
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchone()

    if row is None:
        return None

    d = dict(row)
    if d.get("result"):
        d["result"] = json.loads(d["result"])
    return d


def count_jobs_by_status(db_path: Path = DB_PATH) -> dict[str, int]:
    """status별 job 수를 반환한다."""
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM jobs GROUP BY status"
        ).fetchall()
    return {row["status"]: row["cnt"] for row in rows}


# ── 헬퍼 ─────────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")