"""
api/router/cases.py

케이스 및 케이스 관련 CRUD API.

케이스는 SQLite 'cases' 테이블에 저장되며, ChromaDB 에도 임베딩된다.
SQLite 변경과 ChromaDB 동기화는 각 핸들러에서 직접 처리한다.

엔드포인트 (prefix=/cases):
    GET    /                        케이스 목록
    GET    /{cid}                   단건 조회 (연결 패턴 + 참조 ID 포함)
    POST   /                        케이스 생성 + ChromaDB 임베딩
    PUT    /{cid}                   케이스 수정 + ChromaDB 재임베딩
    DELETE /{cid}                   케이스 삭제 + ChromaDB 제거
    POST   /sync                    ChromaDB 전체 동기화

    GET    /{cid}/patterns          연결 패턴 목록
    POST   /{cid}/patterns/{pid}    패턴 연결
    DELETE /{cid}/patterns/{pid}    패턴 연결 해제

    GET    /{cid}/references        외부 참조 ID 목록
    POST   /{cid}/references        참조 추가
    DELETE /{cid}/references/{rid}  참조 삭제
"""

from __future__ import annotations

import json
import logging
import sqlite3

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from core.db import DB_PATH, get_conn
from core.kb_search import KBSearch

logger = logging.getLogger(__name__)

router = APIRouter()

# 모듈 레벨 싱글턴 — ChromaDB 연결 오버헤드 회피
_kb = KBSearch()


# ── Pydantic 모델 ─────────────────────────────────────────────────────────────

class CaseSaveRequest(BaseModel):
    name: str
    description: str = ""
    keywords: list[str] = []
    analysis: str = ""
    profile_refs: list[str] = []
    chip_tags: list[str] = []


class ReferenceAddRequest(BaseModel):
    system: str
    reference_id: str


# ── DB 헬퍼 ───────────────────────────────────────────────────────────────────

def _load_cases() -> list[dict]:
    with get_conn(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT id, name, description, keywords, analysis, profile_refs, chip_tags, created_at, updated_at "
            "FROM cases ORDER BY id DESC"
        ).fetchall()
    return [_row_to_case_summary(r) for r in rows]


def _load_case(cid: int) -> dict | None:
    with get_conn(DB_PATH) as conn:
        row = conn.execute(
            "SELECT id, name, description, keywords, analysis, profile_refs, chip_tags, created_at, updated_at "
            "FROM cases WHERE id=?",
            (cid,),
        ).fetchone()
        if row is None:
            return None
        case = _row_to_case_summary(row)
        case["patterns"] = _load_patterns_for_case_conn(cid, conn)
        case["references"] = _load_references_for_case_conn(cid, conn)
    return case


def _load_patterns_for_case_conn(cid: int, conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT p.id, p.name, p.type, p.description, p.weight, p.is_required, p.chip_tags
        FROM patterns p
        JOIN case_patterns cp ON cp.pattern_id = p.id
        WHERE cp.case_id = ?
        ORDER BY p.name
        """,
        (cid,),
    ).fetchall()
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "type": r["type"],
            "description": r["description"],
            "weight": r["weight"],
            "is_required": bool(r["is_required"]),
            "chip_tags": _parse_json_list(r["chip_tags"]),
        }
        for r in rows
    ]


def _load_references_for_case_conn(cid: int, conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT id, system, reference_id FROM case_references WHERE case_id=? ORDER BY id",
        (cid,),
    ).fetchall()
    return [{"id": r["id"], "system": r["system"], "reference_id": r["reference_id"]} for r in rows]


def _row_to_case_summary(row: sqlite3.Row) -> dict:
    return {
        "id":          row["id"],
        "name":        row["name"],
        "description": row["description"],
        "keywords":    _parse_json_list(row["keywords"]),
        "analysis":    row["analysis"],
        "profile_refs": _parse_json_list(row["profile_refs"]),
        "chip_tags":   _parse_json_list(row["chip_tags"]),
        "created_at":  row["created_at"],
        "updated_at":  row["updated_at"],
    }


def _parse_json_list(value: str | None) -> list:
    if not value:
        return []
    try:
        result = json.loads(value)
        return result if isinstance(result, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _case_exists(cid: int) -> bool:
    with get_conn(DB_PATH) as conn:
        row = conn.execute("SELECT id FROM cases WHERE id=?", (cid,)).fetchone()
    return row is not None


def _pattern_exists(pid: int) -> bool:
    with get_conn(DB_PATH) as conn:
        row = conn.execute("SELECT id FROM patterns WHERE id=?", (pid,)).fetchone()
    return row is not None


# ── 케이스 엔드포인트 ─────────────────────────────────────────────────────────

@router.get("", summary="케이스 목록 조회")
def list_cases() -> list[dict]:
    return _load_cases()


@router.get("/sync", summary="ChromaDB 동기화 상태 확인 (GET 방지용 더미)")
def sync_status() -> dict:
    """POST /sync 를 잘못 GET 으로 호출했을 때 안내 메시지 반환."""
    return {"message": "ChromaDB 동기화는 POST /cases/sync 를 사용하세요."}


@router.get("/{cid}", summary="케이스 단건 조회")
def get_case(cid: int) -> dict:
    case = _load_case(cid)
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"케이스 id={cid} 를 찾을 수 없습니다.",
        )
    return case


@router.post("", status_code=status.HTTP_201_CREATED, summary="케이스 생성")
def create_case(req: CaseSaveRequest) -> dict:
    if not req.name.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="케이스 이름을 입력하세요.",
        )
    try:
        with get_conn(DB_PATH) as conn:
            cur = conn.execute(
                "INSERT INTO cases (name, description, keywords, analysis, profile_refs, chip_tags) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    req.name.strip(),
                    req.description,
                    json.dumps(req.keywords, ensure_ascii=False),
                    req.analysis,
                    json.dumps(req.profile_refs, ensure_ascii=False),
                    json.dumps(req.chip_tags, ensure_ascii=False),
                ),
            )
            cid = cur.lastrowid
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"케이스 '{req.name}' 이 이미 존재합니다.",
        )

    _kb.add_case(cid, req.name.strip(), req.description, req.keywords, req.analysis, req.chip_tags)
    return get_case(cid)


@router.put("/{cid}", summary="케이스 수정")
def update_case(cid: int, req: CaseSaveRequest) -> dict:
    if _load_case(cid) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"케이스 id={cid} 를 찾을 수 없습니다.",
        )
    if not req.name.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="케이스 이름을 입력하세요.",
        )
    try:
        with get_conn(DB_PATH) as conn:
            conn.execute(
                "UPDATE cases SET name=?, description=?, keywords=?, analysis=?, "
                "profile_refs=?, chip_tags=?, updated_at=datetime('now') WHERE id=?",
                (
                    req.name.strip(),
                    req.description,
                    json.dumps(req.keywords, ensure_ascii=False),
                    req.analysis,
                    json.dumps(req.profile_refs, ensure_ascii=False),
                    json.dumps(req.chip_tags, ensure_ascii=False),
                    cid,
                ),
            )
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"케이스 '{req.name}' 이 이미 존재합니다.",
        )

    _kb.add_case(cid, req.name.strip(), req.description, req.keywords, req.analysis, req.chip_tags)
    return get_case(cid)


@router.delete("/{cid}", summary="케이스 삭제")
def delete_case(cid: int) -> dict:
    if not _case_exists(cid):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"케이스 id={cid} 를 찾을 수 없습니다.",
        )
    with get_conn(DB_PATH) as conn:
        conn.execute("DELETE FROM cases WHERE id=?", (cid,))
    _kb.remove_case(cid)
    return {"result": "ok"}


@router.post("/sync", summary="ChromaDB 전체 동기화")
def sync_chromadb() -> dict:
    """SQLite cases 전체를 ChromaDB 에 재임베딩한다. 긴급 복구용."""
    count = _kb.sync_from_db()
    return {"result": "ok", "synced": count}


# ── 패턴 연결 엔드포인트 ──────────────────────────────────────────────────────

@router.get("/{cid}/patterns", summary="연결 패턴 목록 조회")
def list_case_patterns(cid: int) -> list[dict]:
    if not _case_exists(cid):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"케이스 id={cid} 를 찾을 수 없습니다.",
        )
    with get_conn(DB_PATH) as conn:
        return _load_patterns_for_case_conn(cid, conn)


@router.post("/{cid}/patterns/{pid}", status_code=status.HTTP_201_CREATED, summary="패턴 연결")
def link_pattern(cid: int, pid: int) -> dict:
    if not _case_exists(cid):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"케이스 id={cid} 를 찾을 수 없습니다.",
        )
    if not _pattern_exists(pid):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"패턴 id={pid} 를 찾을 수 없습니다.",
        )
    with get_conn(DB_PATH) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO case_patterns (case_id, pattern_id) VALUES (?, ?)",
            (cid, pid),
        )
    return {"result": "ok"}


@router.delete("/{cid}/patterns/{pid}", summary="패턴 연결 해제")
def unlink_pattern(cid: int, pid: int) -> dict:
    if not _case_exists(cid):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"케이스 id={cid} 를 찾을 수 없습니다.",
        )
    with get_conn(DB_PATH) as conn:
        conn.execute(
            "DELETE FROM case_patterns WHERE case_id=? AND pattern_id=?",
            (cid, pid),
        )
    return {"result": "ok"}


# ── 외부 참조 ID 엔드포인트 ──────────────────────────────────────────────────

@router.get("/{cid}/references", summary="외부 참조 ID 목록 조회")
def list_case_references(cid: int) -> list[dict]:
    if not _case_exists(cid):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"케이스 id={cid} 를 찾을 수 없습니다.",
        )
    with get_conn(DB_PATH) as conn:
        return _load_references_for_case_conn(cid, conn)


@router.post("/{cid}/references", status_code=status.HTTP_201_CREATED, summary="외부 참조 ID 추가")
def add_case_reference(cid: int, req: ReferenceAddRequest) -> dict:
    if not _case_exists(cid):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"케이스 id={cid} 를 찾을 수 없습니다.",
        )
    if not req.system.strip() or not req.reference_id.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="system 과 reference_id 를 모두 입력하세요.",
        )
    with get_conn(DB_PATH) as conn:
        cur = conn.execute(
            "INSERT INTO case_references (case_id, system, reference_id) VALUES (?, ?, ?)",
            (cid, req.system.strip(), req.reference_id.strip()),
        )
        rid = cur.lastrowid
    return {"id": rid, "system": req.system.strip(), "reference_id": req.reference_id.strip()}


@router.delete("/{cid}/references/{rid}", summary="외부 참조 ID 삭제")
def delete_case_reference(cid: int, rid: int) -> dict:
    if not _case_exists(cid):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"케이스 id={cid} 를 찾을 수 없습니다.",
        )
    with get_conn(DB_PATH) as conn:
        conn.execute(
            "DELETE FROM case_references WHERE id=? AND case_id=?",
            (rid, cid),
        )
    return {"result": "ok"}
