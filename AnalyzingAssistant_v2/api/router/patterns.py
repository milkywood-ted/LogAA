"""
api/router/patterns.py

패턴 CRUD API.

패턴은 SQLite 'patterns' 테이블에 저장된다.
단건 조회 시 타입별 세부 필드(steps, components 등)를 포함하여 반환한다.

엔드포인트 (prefix=/patterns):
    GET    /            패턴 목록 (id, name, type, description, weight, chip_tags)
    GET    /{pid}       단건 조회 (타입별 세부 필드 포함)
    POST   /            패턴 생성
    PUT    /{pid}       패턴 수정
    DELETE /{pid}       패턴 삭제 (의존 COMPOSITE 패턴도 함께 삭제)
    POST   /lint        패턴 문자열 정규식 검사 + 이스케이프/매칭 미리보기

정규식 검사:
    패턴 필드는 정규식으로 해석되므로 로그 원문을 그대로 붙여넣으면 의도와 다르게
    동작한다. 생성·수정 시 core.pattern_lint 로 검사하며,
      - 컴파일 실패는 차단한다 (되돌릴 여지가 없는 확정적 오류)
      - 그 밖의 경고는 confirm_warnings=true 로 재요청하면 그대로 저장한다
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from core.db import DB_PATH, get_conn
from core.pattern_db import insert_pattern
from core.pattern_lint import ERROR, WARNING, escape_at, lint, lint_pattern_fields

logger = logging.getLogger(__name__)

router = APIRouter()

_PATTERN_TYPES = ("PRESENCE", "SEQUENCE", "WINDOW", "ABSENCE", "COMPOSITE")
_OPERATORS = ("AND", "OR", "NOT")


# ── Pydantic 모델 ─────────────────────────────────────────────────────────────

class PatternSaveRequest(BaseModel):
    name: str
    type: str
    description: str = ""
    keywords: list[str] = []
    weight: float = 1.0
    analysis_guidelines: str = ""
    chip_tags: list[str] = []

    # PRESENCE / WINDOW 공용
    pattern: str | None = None
    # PRESENCE
    event_dedup_window_sec: float | None = None
    # SEQUENCE
    steps: list[str] = []
    step_dedup: bool = False
    non_overlapping: bool = False
    # WINDOW
    window_sec: float | None = None
    count_threshold: int | None = None
    count_unique_only: bool = False
    # ABSENCE
    trigger_pattern: str | None = None
    absent_pattern: str | None = None
    # COMPOSITE
    operator: str | None = None
    components: list[str] = []  # 구성 패턴 이름 목록

    # 정규식 경고를 확인했음 — true 면 경고가 있어도 그대로 저장한다.
    confirm_warnings: bool = False


class PatternLintRequest(BaseModel):
    """저장 전 정규식 검사용 — 패턴 편집 화면에서 실시간으로 호출한다."""
    pattern: str
    escape_positions: list[int] = []
    """리터럴로 처리할 문자의 인덱스. 일부만 지정해 혼합 케이스를 만들 수 있다."""
    sample_lines: list[str] = []
    """매칭 미리보기용 샘플 로그 라인."""


# ── 정규식 검사 ───────────────────────────────────────────────────────────────

def _check_pattern_syntax(req: PatternSaveRequest) -> list[dict]:
    """
    정규식 필드를 검사한다.

    Returns
    -------
    저장을 허용할 때 응답에 함께 담을 경고 목록 (사용자가 확인한 경고).

    Raises
    ------
    HTTPException(400) : 컴파일 실패, 또는 미확인 경고가 있을 때.
                         detail.code 로 두 경우를 구분한다.
    """
    issues = lint_pattern_fields(req.model_dump())

    errors = [i for i in issues if i.issue.severity == ERROR]
    if errors:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "PATTERN_LINT_ERROR",
                "message": "정규식으로 해석할 수 없는 패턴이 있습니다.",
                "issues": [i.to_dict() for i in errors],
            },
        )

    warnings = [i.to_dict() for i in issues if i.issue.severity == WARNING]
    if warnings and not req.confirm_warnings:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "PATTERN_LINT_WARNING",
                "message": (
                    "정규식 메타문자가 의도와 다르게 해석될 수 있습니다. "
                    "리터럴로 쓰려면 해당 문자 앞에 백슬래시(\\)를 붙이고, "
                    "의도한 정규식이라면 confirm_warnings=true 로 다시 요청하세요."
                ),
                "issues": warnings,
            },
        )

    return warnings


# ── DB 헬퍼 ───────────────────────────────────────────────────────────────────

def _parse_json_list(value: str | None) -> list:
    if not value:
        return []
    try:
        result = json.loads(value)
        return result if isinstance(result, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _load_pattern_summary(row: sqlite3.Row) -> dict:
    return {
        "id":          row["id"],
        "name":        row["name"],
        "type":        row["type"],
        "description": row["description"],
        "keywords":    _parse_json_list(row["keywords"]),
        "weight":      row["weight"],
        "chip_tags":   _parse_json_list(row["chip_tags"]),
    }


def _load_steps(pid: int, conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT pattern FROM pattern_steps WHERE pattern_id=? ORDER BY step_order",
        (pid,),
    ).fetchall()
    return [r["pattern"] for r in rows]


def _load_components(pid: int, conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT p2.name
        FROM pattern_components pc
        JOIN patterns p2 ON pc.ref_pattern_id = p2.id
        WHERE pc.pattern_id = ?
        ORDER BY pc.component_order
        """,
        (pid,),
    ).fetchall()
    return [r["name"] for r in rows]


def _load_full_pattern(pid: int) -> dict | None:
    with get_conn(DB_PATH) as conn:
        row = conn.execute("SELECT * FROM patterns WHERE id=?", (pid,)).fetchone()
        if row is None:
            return None
        p = _load_pattern_summary(row)
        p["analysis_guidelines"] = row["analysis_guidelines"] or ""

        ptype = row["type"]
        if ptype == "PRESENCE":
            p["pattern"] = row["pattern"]
            p["event_dedup_window_sec"] = row["event_dedup_window_sec"]
        elif ptype == "SEQUENCE":
            p["steps"] = _load_steps(pid, conn)
            p["step_dedup"] = bool(row["step_dedup"])
            p["non_overlapping"] = bool(row["non_overlapping"])
        elif ptype == "WINDOW":
            p["pattern"] = row["pattern"]
            p["window_sec"] = row["window_sec"]
            p["count_threshold"] = row["count_threshold"]
            p["count_unique_only"] = bool(row["count_unique_only"])
        elif ptype == "ABSENCE":
            p["trigger_pattern"] = row["trigger_pattern"]
            p["absent_pattern"] = row["absent_pattern"]
            p["window_sec"] = row["window_sec"]
        elif ptype == "COMPOSITE":
            p["operator"] = row["operator"]
            p["components"] = _load_components(pid, conn)

    return p


def _pattern_exists(pid: int) -> bool:
    with get_conn(DB_PATH) as conn:
        return conn.execute("SELECT id FROM patterns WHERE id=?", (pid,)).fetchone() is not None


def _update_pattern(pid: int, req: PatternSaveRequest) -> None:
    with get_conn(DB_PATH) as conn:
        conn.execute(
            """
            UPDATE patterns SET
              name=?, type=?, description=?, keywords=?,
              pattern=?, event_dedup_window_sec=?,
              step_dedup=?, non_overlapping=?,
              window_sec=?, count_threshold=?, count_unique_only=?,
              trigger_pattern=?, absent_pattern=?,
              operator=?, weight=?,
              analysis_guidelines=?,
              updated_at=datetime('now')
            WHERE id=?
            """,
            (
                req.name.strip(), req.type, req.description,
                json.dumps(req.keywords, ensure_ascii=False),
                req.pattern, req.event_dedup_window_sec,
                int(req.step_dedup), int(req.non_overlapping),
                req.window_sec, req.count_threshold, int(req.count_unique_only),
                req.trigger_pattern, req.absent_pattern,
                req.operator,
                req.weight,
                req.analysis_guidelines,
                pid,
            ),
        )

        conn.execute("DELETE FROM pattern_steps WHERE pattern_id=?", (pid,))
        for i, step in enumerate(req.steps or []):
            conn.execute(
                "INSERT INTO pattern_steps (pattern_id, step_order, pattern) VALUES (?,?,?)",
                (pid, i, step),
            )

        conn.execute("DELETE FROM pattern_components WHERE pattern_id=?", (pid,))
        for i, comp_name in enumerate(req.components or []):
            ref = conn.execute("SELECT id FROM patterns WHERE name=?", (comp_name,)).fetchone()
            if ref:
                conn.execute(
                    "INSERT INTO pattern_components (pattern_id, component_order, ref_pattern_id) VALUES (?,?,?)",
                    (pid, i, ref["id"]),
                )


def _delete_pattern_cascade(pid: int) -> list[str]:
    """패턴 삭제. 이 패턴을 구성요소로 참조하는 COMPOSITE 패턴도 함께 삭제한다.

    ref_pattern_id 에 ON DELETE CASCADE 가 없으므로:
      1. 삭제 대상 전체의 ref_pattern_id 참조를 먼저 제거
      2. 그 후 실제 DELETE

    Returns: 함께 삭제된 COMPOSITE 패턴 이름 목록
    """
    with get_conn(DB_PATH) as conn:
        composites = conn.execute(
            """
            SELECT DISTINCT p.id, p.name
            FROM pattern_components pc
            JOIN patterns p ON pc.pattern_id = p.id
            WHERE pc.ref_pattern_id = ?
            """,
            (pid,),
        ).fetchall()

        composite_ids  = [c["id"]   for c in composites]
        deleted_names  = [c["name"] for c in composites]

        for cid in composite_ids + [pid]:
            conn.execute("DELETE FROM pattern_components WHERE ref_pattern_id=?", (cid,))

        for cid in composite_ids:
            conn.execute("DELETE FROM patterns WHERE id=?", (cid,))

        conn.execute("DELETE FROM patterns WHERE id=?", (pid,))

    return deleted_names


# ── 엔드포인트 ────────────────────────────────────────────────────────────────

@router.get("", summary="패턴 목록 조회")
def list_patterns(type: str | None = None) -> list[dict]:
    """type 파라미터로 필터링 가능: PRESENCE / SEQUENCE / WINDOW / ABSENCE / COMPOSITE"""
    with get_conn(DB_PATH) as conn:
        if type and type.upper() in _PATTERN_TYPES:
            rows = conn.execute(
                "SELECT id, name, type, description, keywords, weight, chip_tags "
                "FROM patterns WHERE type=? ORDER BY id",
                (type.upper(),),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, name, type, description, keywords, weight, chip_tags "
                "FROM patterns ORDER BY id"
            ).fetchall()
    return [_load_pattern_summary(r) for r in rows]


@router.get("/{pid}", summary="패턴 단건 조회 (타입별 세부 필드 포함)")
def get_pattern(pid: int) -> dict:
    p = _load_full_pattern(pid)
    if p is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"패턴 id={pid} 를 찾을 수 없습니다.",
        )
    return p


@router.post("", status_code=status.HTTP_201_CREATED, summary="패턴 생성")
def create_pattern(req: PatternSaveRequest) -> dict:
    if not req.name.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="패턴 이름을 입력하세요.",
        )
    if req.type not in _PATTERN_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"type 은 {_PATTERN_TYPES} 중 하나여야 합니다.",
        )
    if req.type == "COMPOSITE" and req.operator not in _OPERATORS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"COMPOSITE 타입은 operator({_OPERATORS}) 가 필요합니다.",
        )
    lint_warnings = _check_pattern_syntax(req)
    try:
        pid = insert_pattern({
            "name":                 req.name.strip(),
            "type":                 req.type,
            "description":          req.description,
            "keywords":             req.keywords,
            "weight":               req.weight,
            "analysis_guidelines":  req.analysis_guidelines,
            "pattern":              req.pattern,
            "event_dedup_window_sec": req.event_dedup_window_sec,
            "steps":                req.steps,
            "step_dedup":           req.step_dedup,
            "non_overlapping":      req.non_overlapping,
            "window_sec":           req.window_sec,
            "count_threshold":      req.count_threshold,
            "count_unique_only":    req.count_unique_only,
            "trigger_pattern":      req.trigger_pattern,
            "absent_pattern":       req.absent_pattern,
            "operator":             req.operator,
            "components":           req.components,
        })
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"패턴 '{req.name}' 이 이미 존재합니다.",
        )
    return {**get_pattern(pid), "lint_warnings": lint_warnings}


@router.put("/{pid}", summary="패턴 수정")
def update_pattern(pid: int, req: PatternSaveRequest) -> dict:
    if not _pattern_exists(pid):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"패턴 id={pid} 를 찾을 수 없습니다.",
        )
    if not req.name.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="패턴 이름을 입력하세요.",
        )
    if req.type not in _PATTERN_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"type 은 {_PATTERN_TYPES} 중 하나여야 합니다.",
        )
    if req.type == "COMPOSITE" and req.operator not in _OPERATORS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"COMPOSITE 타입은 operator({_OPERATORS}) 가 필요합니다.",
        )
    lint_warnings = _check_pattern_syntax(req)
    try:
        _update_pattern(pid, req)
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"패턴 '{req.name}' 이 이미 존재합니다.",
        )
    return {**get_pattern(pid), "lint_warnings": lint_warnings}


@router.delete("/{pid}", summary="패턴 삭제")
def delete_pattern(pid: int) -> dict:
    """패턴을 삭제한다. 이 패턴을 구성요소로 참조하는 COMPOSITE 패턴도 함께 삭제된다."""
    if not _pattern_exists(pid):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"패턴 id={pid} 를 찾을 수 없습니다.",
        )
    deleted_composites = _delete_pattern_cascade(pid)
    result: dict = {"result": "ok"}
    if deleted_composites:
        result["deleted_composites"] = deleted_composites
    return result


@router.post("/lint", summary="패턴 문자열 정규식 검사 + 이스케이프/매칭 미리보기")
def lint_pattern_text(req: PatternLintRequest) -> dict:
    """
    저장하지 않고 패턴 문자열 하나만 검사한다.

    escape_positions 로 일부 문자만 리터럴 처리할 수 있어,
    정규식과 리터럴이 섞인 패턴을 단계적으로 다듬을 수 있다.
    sample_lines 를 주면 실제 매칭 결과를 함께 돌려준다 — 이스케이프 문법을
    따지는 것보다 무엇이 매칭되는지 직접 확인하는 편이 확실하다.
    """
    escaped = escape_at(req.pattern, req.escape_positions)
    issues = lint(escaped)

    matched: list[str] = []
    if not any(i.severity == ERROR for i in issues):
        compiled = re.compile(escaped, re.IGNORECASE)   # Stage 4 와 동일한 플래그
        matched = [line for line in req.sample_lines if compiled.search(line)]

    return {
        "pattern": req.pattern,
        "escaped": escaped,
        "issues":  [i.to_dict() for i in issues],
        "matched_samples": matched,
    }
