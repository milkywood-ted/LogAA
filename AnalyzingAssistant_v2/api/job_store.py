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
    status     TEXT NOT NULL DEFAULT 'pending',  -- pending | running | done | error | cancelled
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
            (json.dumps(_sanitize_for_json(result), ensure_ascii=False), _now(), job_id),
        )


def update_job_cancelled(
    job_id: str,
    db_path: Path = DB_PATH,
) -> None:
    """job 상태를 cancelled로 업데이트한다."""
    with get_conn(db_path) as conn:
        conn.execute(
            """
            UPDATE jobs
            SET status = 'cancelled',
                stage  = '취소됨',
                updated_at = ?
            WHERE job_id = ? AND status IN ('pending', 'running')
            """,
            (_now(), job_id),
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
            (_sanitize_surrogates(error), _now(), job_id),
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


def purge_old_jobs(ttl_minutes: int, db_path: Path = DB_PATH) -> int:
    """
    완료 상태(done / error / cancelled)이고 updated_at 이 ttl_minutes 이전인 job을 삭제한다.
    ttl_minutes=0 이면 모든 완료 job을 즉시 삭제한다.

    pending / running 상태의 job은 절대 삭제하지 않는다 — 재시작 직후 좀비 job 정리는
    mark_zombies_cancelled() 로 별도 처리한다.

    Returns: 삭제된 row 수
    """
    with get_conn(db_path) as conn:
        cur = conn.execute(
            """
            DELETE FROM jobs
            WHERE status IN ('done', 'error', 'cancelled')
              AND updated_at <= datetime('now', ? || ' minutes')
            """,
            (f"-{ttl_minutes}",),
        )
        return cur.rowcount


def mark_zombies_cancelled(db_path: Path = DB_PATH) -> int:
    """
    재시작 직후 호출 — 이전 프로세스에서 진행 중이던 pending / running job 을
    cancelled 상태로 마킹한다. 클라이언트가 폴링 시 404 가 아닌 cancelled 상태를
    받아 재제출 여부를 판단할 수 있다.

    Returns: 마킹된 row 수
    """
    with get_conn(db_path) as conn:
        cur = conn.execute(
            """
            UPDATE jobs
            SET status     = 'cancelled',
                stage      = '서버 재시작으로 취소됨',
                updated_at = ?
            WHERE status IN ('pending', 'running')
            """,
            (_now(),),
        )
        return cur.rowcount


# ── 헬퍼 ─────────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _sanitize_surrogates(s: str) -> str:
    """UTF-8로 인코딩할 수 없는 surrogate 문자를 제거한다."""
    return s.encode("utf-8", errors="replace").decode("utf-8")


def _sanitize_for_json(obj: Any) -> Any:
    """dict/list 구조 안에 있는 문자열의 surrogate를 재귀적으로 제거한다."""
    if isinstance(obj, str):
        return _sanitize_surrogates(obj)
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_for_json(item) for item in obj]
    return obj
