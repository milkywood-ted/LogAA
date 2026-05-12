"""
core/db.py

SQLite 스키마 정의 및 커넥션 관리.

테이블 목록:
  patterns              — 패턴 정의 (PRESENCE / SEQUENCE / WINDOW / ABSENCE / COMPOSITE)
  pattern_steps         — SEQUENCE 단계 (patterns 의 자식)
  pattern_components    — COMPOSITE 구성 패턴 참조 (patterns 의 자식)
  cases                 — KB 케이스 (BGE-M3 임베딩 대상 description 포함)
  case_patterns         — 케이스 ↔ 패턴 다대다
  noise_patterns        — 노이즈 필터 regex
  master_rules          — 매칭 전 전역 로그 스트림 정규화 규칙
  history               — 분석 이력
  analysis_logs         — Stage별 상세 로그 (Observability)
  domain_knowledge      — 사전지식 (SQLite 구조화 / ChromaDB 비구조화)
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

DB_PATH: Path = Path(__file__).parent.parent / "db" / "loganalyzer.db"

# ── 스키마 ─────────────────────────────────────────────────────────────────────

_SCHEMA = """
-- ── 패턴 ──────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS patterns (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    name                   TEXT    NOT NULL UNIQUE,
    type                   TEXT    NOT NULL
                               CHECK(type IN ('PRESENCE','SEQUENCE','WINDOW','ABSENCE','COMPOSITE')),
    description            TEXT    NOT NULL DEFAULT '',
    keywords               TEXT    NOT NULL,        -- JSON array  e.g. ["ata","error"]

    -- PRESENCE: 특정 패턴이 등장하는지
    -- WINDOW:   일정 시간 내 N회 이상 등장하는지
    pattern                TEXT,                    -- regex (PRESENCE·WINDOW 공용)
    event_dedup_window_sec REAL,                    -- PRESENCE: 중복 이벤트 무시 윈도우(초)

    -- SEQUENCE: 순서대로 등장하는지  (단계는 pattern_steps 테이블)
    step_dedup             INTEGER NOT NULL DEFAULT 0,   -- 각 step 내 중복 제거
    non_overlapping        INTEGER NOT NULL DEFAULT 0,   -- 시퀀스 겹침 불허

    -- WINDOW: 시간 창 내 N회 이상
    window_sec             REAL,                    -- 시간 창 크기(초)  WINDOW·ABSENCE 공용
    count_threshold        INTEGER,                 -- WINDOW: 최소 발생 횟수
    count_unique_only      INTEGER NOT NULL DEFAULT 0,   -- fingerprint 기준 중복 제거 후 카운트

    -- ABSENCE: trigger 발생 후 window 내 absent 미출현
    trigger_pattern        TEXT,                    -- regex: 감시 시작 조건
    absent_pattern         TEXT,                    -- regex: 이 패턴이 없어야 함

    -- COMPOSITE: 하위 패턴 AND/OR/NOT 조합  (구성 요소는 pattern_components 테이블)
    operator               TEXT    CHECK(operator IN ('AND','OR','NOT')),

    -- 공통
    weight                 REAL    NOT NULL DEFAULT 1.0,
    is_required            INTEGER NOT NULL DEFAULT 0,   -- 미매칭 시 케이스 즉시 제외

    created_at             TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at             TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- SEQUENCE 의 각 단계
CREATE TABLE IF NOT EXISTS pattern_steps (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_id  INTEGER NOT NULL REFERENCES patterns(id) ON DELETE CASCADE,
    step_order  INTEGER NOT NULL,
    pattern     TEXT    NOT NULL    -- 해당 단계의 매칭 regex
);

-- COMPOSITE 의 구성 패턴 참조
CREATE TABLE IF NOT EXISTS pattern_components (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_id      INTEGER NOT NULL REFERENCES patterns(id) ON DELETE CASCADE,
    component_order INTEGER NOT NULL,
    ref_pattern_id  INTEGER NOT NULL REFERENCES patterns(id)
);

-- ── KB 케이스 ──────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS cases (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,
    description TEXT    NOT NULL,   -- BGE-M3 임베딩 대상
    keywords    TEXT    NOT NULL,   -- JSON array: Stage 3 재필터링용
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- 케이스 ↔ 패턴 다대다
CREATE TABLE IF NOT EXISTS case_patterns (
    case_id    INTEGER NOT NULL REFERENCES cases(id)    ON DELETE CASCADE,
    pattern_id INTEGER NOT NULL REFERENCES patterns(id) ON DELETE CASCADE,
    PRIMARY KEY (case_id, pattern_id)
);

-- ── 기타 ───────────────────────────────────────────────────────────────────────

-- 노이즈 필터: Stage 1-1 에서 제거할 라인 패턴
CREATE TABLE IF NOT EXISTS noise_patterns (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern TEXT    NOT NULL UNIQUE,
    comment TEXT
);

-- 마스터 룰: 패턴 매칭 전 전역 로그 스트림 정규화 규칙
-- 현재 지원 rule_type:
--   DEDUP_CONSECUTIVE : 같은 패턴에 매칭되는 연속 라인을 1개로 축약
CREATE TABLE IF NOT EXISTS master_rules (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,
    rule_type   TEXT    NOT NULL CHECK(rule_type IN ('DEDUP_CONSECUTIVE')),
    pattern     TEXT    NOT NULL,   -- 적용 대상 regex
    comment     TEXT,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- 분석 이력
CREATE TABLE IF NOT EXISTS history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    input_hash  TEXT    NOT NULL,   -- 입력 로그 SHA256
    result      TEXT    NOT NULL,   -- JSON: stage 결과 전체
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Stage별 상세 로그 (Observability)
-- 한 분석 실행(history_id)의 각 Stage 판단 근거·입출력을 기록한다.
-- payload 스키마는 Stage 마다 다르며, JSON 자유 스키마로 저장한다.
CREATE TABLE IF NOT EXISTS analysis_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    history_id  INTEGER REFERENCES history(id) ON DELETE CASCADE,
    stage       TEXT    NOT NULL,   -- stage 식별자 (자유 문자열; 현재 값은 pipeline.py 참조)
    payload     TEXT    NOT NULL,   -- JSON
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_analysis_logs_history_id
    ON analysis_logs(history_id);

-- 사전지식: 프로파일이 참조하는 구조화/비구조화 지식
CREATE TABLE IF NOT EXISTS domain_knowledge (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,
    store_type  TEXT    NOT NULL CHECK(store_type IN ('sqlite', 'chromadb')),
    content     TEXT    NOT NULL DEFAULT '',  -- SQLite 타입: 직접 내용
    description TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

"""

# ── 커넥션 관리 ───────────────────────────────────────────────────────────────

@contextmanager
def get_conn(db_path: Path = DB_PATH) -> Generator[sqlite3.Connection, None, None]:
    """
    SQLite 커넥션 컨텍스트 매니저.

    - foreign key 제약 활성화
    - Row 팩토리 설정 (컬럼명으로 접근 가능)
    - 정상 종료 시 commit, 예외 시 rollback
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Path = DB_PATH) -> None:
    """스키마를 생성하고 누락된 컬럼을 마이그레이션한다."""
    with get_conn(db_path) as conn:
        conn.executescript(_SCHEMA)
        _migrate(conn)


def _migrate(conn: sqlite3.Connection) -> None:
    """기존 테이블에 새 컬럼을 추가하는 마이그레이션. 이미 존재하면 무시."""
    migrations = [
        "ALTER TABLE patterns ADD COLUMN analysis_guidelines TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE cases ADD COLUMN profile_refs TEXT NOT NULL DEFAULT '[]'",
    ]
    for sql in migrations:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass  # 컬럼이 이미 존재함
