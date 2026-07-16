"""
core/db.py

SQLite 스키마 정의 및 커넥션 관리.

테이블 목록:
  patterns              — 패턴 정의 (PRESENCE / SEQUENCE / WINDOW / ABSENCE / COMPOSITE)
  pattern_steps         — SEQUENCE 단계 (patterns 의 자식)
  pattern_components    — COMPOSITE 구성 패턴 참조 (patterns 의 자식)
  cases                 — KB 케이스 (BGE-M3 임베딩 대상 description 포함)
  case_patterns         — 케이스 ↔ 패턴 다대다
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
    -- is_required 컬럼은 §9-1 검토로 기능 제거(2026-07-16) — 필수 패턴이 다형 발현·
    -- 이원인 케이스에서 오판을 강제할 수 있어 미도입 확정. 기존 DB의 잔존 컬럼은
    -- 무해(DEFAULT 0)하며 신규 DB에는 생성되지 않는다.

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

-- 케이스 외부 참조 ID (문제 관리 시스템 연동)
CREATE TABLE IF NOT EXISTS case_references (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id      INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    system       TEXT    NOT NULL,   -- 예: "Jira", "GitHub"
    reference_id TEXT    NOT NULL,   -- 예: "DTV-1234"
    created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ── 기타 ───────────────────────────────────────────────────────────────────────

-- noise_patterns 테이블은 §9-4 검토로 제거(2026-07-16) — 소비자 없는 휴면 스키마.
-- 노이즈 제거는 키워드 prefilter(화이트리스트)·파서 매칭·collapse 가 담당한다.
-- 기존 DB 의 빈 잔존 테이블은 무해하며 신규 DB 에는 생성되지 않는다.

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
    - WAL 저널 모드 — 읽기(SSE 폴링 등)와 쓰기(워커 스레드)가 서로 차단하지
      않도록 한다. DB 파일 속성이라 최초 1회만 실제 전환되고 이후는 no-op.
      DB 파일 옆에 -wal/-shm 파일이 생성되므로 백업 시 함께 복사한다.
    - busy_timeout 10초 — 쓰기끼리 경합 시 즉시 실패 대신 대기
    - synchronous NORMAL — WAL 표준 조합 (fsync 감소, 내구성 충분)
    - Row 팩토리 설정 (컬럼명으로 접근 가능)
    - 정상 종료 시 commit, 예외 시 rollback
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.execute("PRAGMA synchronous = NORMAL")
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
    """기존 테이블에 새 컬럼을 추가하는 마이그레이션. 이미 존재하면 무시.

    OperationalError 는 다양한 원인으로 발생할 수 있으므로(락, 권한, 테이블
    부재 등) 메시지에 'duplicate column name' 이 포함된 경우만 "이미 존재" 로
    간주하고 무시한다. 그 외 오류는 상위로 전파해 정상 진단되도록 한다.
    """
    migrations = [
        "ALTER TABLE patterns ADD COLUMN analysis_guidelines TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE cases ADD COLUMN profile_refs TEXT NOT NULL DEFAULT '[]'",
        "ALTER TABLE cases ADD COLUMN analysis TEXT NOT NULL DEFAULT ''",
        # 특정 칩에만 해당하는 패턴 구분용. JSON array  e.g. ["RheaM","RoseM"]
        # 비어 있으면 공통 패턴(모든 칩에 적용).
        "ALTER TABLE patterns ADD COLUMN chip_tags TEXT NOT NULL DEFAULT '[]'",
        # 특정 칩에 해당하는 케이스 구분용. JSON array  e.g. ["RheaM","RoseM"]
        # 유사 케이스 검색(Stage 2) 시 defect chip 과 일치하면 순위를 우대한다.
        "ALTER TABLE cases ADD COLUMN chip_tags TEXT NOT NULL DEFAULT '[]'",
        "ALTER TABLE history ADD COLUMN defect_id TEXT",
        # 도메인 지식 분류/칩 연동. category 는 프로파일 category 와 매칭하여
        # 분석 시 지식 후보를 필터링한다. 빈 문자열은 공통(모든 category 에 적용).
        "ALTER TABLE domain_knowledge ADD COLUMN category TEXT NOT NULL DEFAULT ''",
        # 하위 분류. 표시/관리용 (현재 필터에는 미사용).
        "ALTER TABLE domain_knowledge ADD COLUMN sub_category TEXT NOT NULL DEFAULT ''",
        # 특정 칩에만 적용되는 지식 구분용. JSON array  e.g. ["RheaM","RoseM"]
        # 비어 있으면 공통 지식(모든 칩에 적용).
        "ALTER TABLE domain_knowledge ADD COLUMN chip_tags TEXT NOT NULL DEFAULT '[]'",
        # ── 케이스 리포트 스키마 v2 (판정·조치·기본정보) ──
        # 설계: Document/로그분석 리포트 및 케이스 스키마 개선/케이스 스키마 개선 구현 설계.md
        # 기본 정보 (리포트 섹션 01)
        "ALTER TABLE cases ADD COLUMN analyst TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE cases ADD COLUMN owner_module TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE cases ADD COLUMN analysis_date TEXT",                    # YYYY-MM-DD
        "ALTER TABLE cases ADD COLUMN log_source TEXT NOT NULL DEFAULT ''",  # 로그 출처/기간
        # 판정 (리포트 섹션 03) — 집계 축은 개별 컬럼 + CHECK
        # verdict 는 API 저장 요청에서 필수. NULL 은 스키마 도입 전 레거시 행에서만 존재.
        "ALTER TABLE cases ADD COLUMN verdict TEXT"
        " CHECK(verdict IN ('defect','no_defect','undetermined'))",
        "ALTER TABLE cases ADD COLUMN symptom_module TEXT NOT NULL DEFAULT ''",  # 문제현상 발현 영역
        "ALTER TABLE cases ADD COLUMN defect_area_type TEXT"
        " CHECK(defect_area_type IN ('module','external','verification'))",
        "ALTER TABLE cases ADD COLUMN defect_area_module TEXT NOT NULL DEFAULT ''",   # type=module 일 때 모듈명
        "ALTER TABLE cases ADD COLUMN defect_area_items TEXT NOT NULL DEFAULT '[]'",  # external/verification 하위 항목 JSON array
        "ALTER TABLE cases ADD COLUMN undetermined_reason TEXT"
        " CHECK(undetermined_reason IN ('insufficient_logs','not_reproducible','other'))",
        "ALTER TABLE cases ADD COLUMN undetermined_reason_note TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE cases ADD COLUMN verdict_rationale TEXT NOT NULL DEFAULT ''",  # 판정 근거
        # 조치 (리포트 섹션 04) — 복수 선택 + 반복 구조라 JSON object
        "ALTER TABLE cases ADD COLUMN actions TEXT NOT NULL DEFAULT '{}'",
        # 특이사항/비고 (리포트 섹션 05)
        "ALTER TABLE cases ADD COLUMN notes TEXT NOT NULL DEFAULT ''",
    ]
    for sql in migrations:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e).lower():
                continue  # 컬럼이 이미 존재함 — 정상 케이스
            raise
