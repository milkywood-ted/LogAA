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
"""

from __future__ import annotations

import json
import logging
import sqlite3

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from core.db import DB_PATH, get_conn
from core.pattern_db import insert_pattern

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
    return get_pattern(pid)


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
    try:
        _update_pattern(pid, req)
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"패턴 '{req.name}' 이 이미 존재합니다.",
        )
    return get_pattern(pid)


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
