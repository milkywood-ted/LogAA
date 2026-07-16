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
from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, model_validator

from core.db import DB_PATH, get_conn
from core.kb_search import KBSearch

logger = logging.getLogger(__name__)

router = APIRouter()

# 모듈 레벨 싱글턴 — ChromaDB 연결 오버헤드 회피
_kb = KBSearch()


# ── Pydantic 모델 ─────────────────────────────────────────────────────────────
# 리포트 스키마 v2 (판정·조치·기본정보):
#   Document/로그분석 리포트 및 케이스 스키마 개선/케이스 스키마 개선 구현 설계.md

class FixEntry(BaseModel):
    """수정 및 해결(Fix and Resolve)의 개별 수정 항목 — 반복 리스트."""
    type: Literal["fix", "defensive"]   # 오류 수정 / 방어적·회피 수정 — 단일 선택
    module: str                          # 수정 모듈
    change: str = ""                     # 변경 내용 / 대상 버전


class FixAction(BaseModel):
    selected: bool = False
    entries: list[FixEntry] = []


class AdditionalAction(BaseModel):
    selected: bool = False
    items: list[str] = []   # add_logs / secure_repro / wait_recurrence / other:<서술>
    plan: str = ""


class KeepAction(BaseModel):
    selected: bool = False
    detail: Literal["close_no_defect", "accept_defect", "close_undetermined"] | None = None
    reason: str = ""        # detail=accept_defect 일 때 필수 (기술적 불가 / 우선순위·비용)


class HandoverAction(BaseModel):
    selected: bool = False
    items: list[str] = []   # other_module / customer / third_party / hw / verification
    owner: str = ""         # 해결 주체
    channel: str = ""       # 티켓 / 전달 채널


class CaseActions(BaseModel):
    """조치(Action) — 구분 기준은 수정 위치. 복수 선택 가능."""
    fix: FixAction = FixAction()
    additional: AdditionalAction = AdditionalAction()
    keep: KeepAction = KeepAction()
    handover: HandoverAction = HandoverAction()


class CaseSaveRequest(BaseModel):
    name: str
    description: str = ""       # 리포트 2.1 보고된 현상 — BGE-M3 임베딩 대상
    keywords: list[str] = []
    analysis: str = ""          # 리포트 2.2 분석 내용
    profile_refs: list[str] = []
    chip_tags: list[str] = []
    # ── 기본 정보 (섹션 01) ── 이슈 번호/제목은 case_references 를 사용
    analyst: str = ""
    owner_module: str = ""
    analysis_date: str | None = None    # YYYY-MM-DD
    log_source: str = ""                # 로그 출처 / 기간
    # ── 판정 (섹션 03) ──
    verdict: Literal["defect", "no_defect", "undetermined"]
    symptom_module: str = ""            # 문제현상 발현 영역
    defect_area_type: Literal["module", "external", "verification"] | None = None
    defect_area_module: str = ""        # type=module 일 때 모듈명
    defect_area_items: list[str] = []   # external/verification 하위 항목 토큰
    undetermined_reason: Literal["insufficient_logs", "not_reproducible", "other"] | None = None
    undetermined_reason_note: str = ""  # 사유=other 일 때 서술
    verdict_rationale: str = ""         # 판정 근거
    # ── 조치 (섹션 04) / 비고 (섹션 05) ──
    actions: CaseActions = CaseActions()
    notes: str = ""
    # ── 패턴 연결 (원자적 저장, frontend §9-6) ──
    pattern_ids: list[int] | None = None
    """연결할 패턴 id 목록 (목표 상태). None 이면 연결을 변경하지 않는다(하위호환).
    리스트 제공 시 케이스 본문과 **같은 트랜잭션**에서 diff 반영되어, 실패 시
    케이스 저장까지 전체 롤백된다."""

    @model_validator(mode="after")
    def _validate_report_rules(self) -> "CaseSaveRequest":
        """분류 체계 v2.0 의 조건부 필수 규칙 (구현 설계 §2.2). 위반 시 422."""
        errors: list[str] = []

        if self.analysis_date == "":
            self.analysis_date = None
        if self.analysis_date is not None:
            try:
                date.fromisoformat(self.analysis_date)
            except ValueError:
                errors.append("분석 일자는 YYYY-MM-DD 형식이어야 합니다.")

        if self.verdict == "defect":
            if not self.symptom_module.strip():
                errors.append("결함 판정에는 문제현상 발현 영역(모듈명)이 필수입니다.")
            if self.defect_area_type is None:
                errors.append("결함 판정에는 결함영역이 필수입니다.")
        if self.defect_area_type == "module" and not self.defect_area_module.strip():
            errors.append("결함영역이 '특정 모듈'이면 모듈명이 필수입니다.")
        if self.defect_area_type in ("external", "verification") and not self.defect_area_items:
            errors.append("결함영역이 '외부요인/검증계'이면 하위 항목을 1개 이상 선택해야 합니다.")
        if self.verdict == "undetermined" and self.undetermined_reason is None:
            errors.append("판정불가에는 사유가 필수입니다.")
        if self.undetermined_reason == "other" and not self.undetermined_reason_note.strip():
            errors.append("판정불가 사유가 '기타'이면 사유 서술이 필수입니다.")

        a = self.actions
        if a.fix.selected:
            if not a.fix.entries:
                errors.append("'수정 및 해결' 조치에는 수정 항목이 1개 이상 필요합니다.")
            if any(not e.module.strip() for e in a.fix.entries):
                errors.append("수정 항목에는 수정 모듈이 필수입니다.")
        if a.keep.selected:
            if a.keep.detail is None:
                errors.append("'유지' 조치에는 하위 구분이 필수입니다.")
            elif a.keep.detail == "accept_defect" and not a.keep.reason.strip():
                errors.append("'결함 수용·보류'에는 사유가 필수입니다.")
        if a.handover.selected and not a.handover.owner.strip():
            errors.append("'이관/외부 대응' 조치에는 해결 주체가 필수입니다.")

        if errors:
            raise ValueError(" / ".join(errors))
        return self


class ReferenceAddRequest(BaseModel):
    system: str
    reference_id: str


# ── DB 헬퍼 ───────────────────────────────────────────────────────────────────

# INSERT / UPDATE 공용 컬럼 목록 (id, created_at, updated_at 제외)
_CASE_COLUMNS = [
    "name", "description", "keywords", "analysis", "profile_refs", "chip_tags",
    "analyst", "owner_module", "analysis_date", "log_source",
    "verdict", "symptom_module", "defect_area_type", "defect_area_module",
    "defect_area_items", "undetermined_reason", "undetermined_reason_note",
    "verdict_rationale", "actions", "notes",
]


def _case_params(req: CaseSaveRequest) -> dict:
    """CaseSaveRequest → SQL named-parameter dict. JSON 필드는 직렬화한다."""
    return {
        "name":                     req.name.strip(),
        "description":              req.description,
        "keywords":                 json.dumps(req.keywords, ensure_ascii=False),
        "analysis":                 req.analysis,
        "profile_refs":             json.dumps(req.profile_refs, ensure_ascii=False),
        "chip_tags":                json.dumps(req.chip_tags, ensure_ascii=False),
        "analyst":                  req.analyst,
        "owner_module":             req.owner_module,
        "analysis_date":            req.analysis_date,
        "log_source":               req.log_source,
        "verdict":                  req.verdict,
        "symptom_module":           req.symptom_module,
        "defect_area_type":         req.defect_area_type,
        "defect_area_module":       req.defect_area_module,
        "defect_area_items":        json.dumps(req.defect_area_items, ensure_ascii=False),
        "undetermined_reason":      req.undetermined_reason,
        "undetermined_reason_note": req.undetermined_reason_note,
        "verdict_rationale":        req.verdict_rationale,
        "actions":                  json.dumps(req.actions.model_dump(), ensure_ascii=False),
        "notes":                    req.notes,
    }


def _load_cases() -> list[dict]:
    with get_conn(DB_PATH) as conn:
        rows = conn.execute("SELECT * FROM cases ORDER BY id DESC").fetchall()
    return [_row_to_case_summary(r) for r in rows]


def _load_case(cid: int) -> dict | None:
    with get_conn(DB_PATH) as conn:
        row = conn.execute(
            "SELECT * FROM cases WHERE id=?",
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
        SELECT p.id, p.name, p.type, p.description, p.weight, p.chip_tags
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
        # ── 리포트 스키마 v2 ──
        "analyst":                  row["analyst"],
        "owner_module":             row["owner_module"],
        "analysis_date":            row["analysis_date"],
        "log_source":               row["log_source"],
        "verdict":                  row["verdict"],   # NULL = 레거시(판정 미기재)
        "symptom_module":           row["symptom_module"],
        "defect_area_type":         row["defect_area_type"],
        "defect_area_module":       row["defect_area_module"],
        "defect_area_items":        _parse_json_list(row["defect_area_items"]),
        "undetermined_reason":      row["undetermined_reason"],
        "undetermined_reason_note": row["undetermined_reason_note"],
        "verdict_rationale":        row["verdict_rationale"],
        "actions":                  _parse_json_obj(row["actions"]),
        "notes":                    row["notes"],
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


def _parse_json_obj(value: str | None) -> dict:
    if not value:
        return {}
    try:
        result = json.loads(value)
        return result if isinstance(result, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _case_exists(cid: int) -> bool:
    with get_conn(DB_PATH) as conn:
        row = conn.execute("SELECT id FROM cases WHERE id=?", (cid,)).fetchone()
    return row is not None


def _pattern_exists(pid: int) -> bool:
    with get_conn(DB_PATH) as conn:
        row = conn.execute("SELECT id FROM patterns WHERE id=?", (pid,)).fetchone()
    return row is not None


def _apply_pattern_links(conn, cid: int, pattern_ids: list[int]) -> None:
    """케이스↔패턴 연결을 pattern_ids 목표 상태로 diff 반영한다.

    케이스 저장과 **같은 커넥션(트랜잭션)** 안에서 호출된다 — 존재하지 않는
    패턴 id 가 있으면 예외를 던져 케이스 본문 저장까지 전체 롤백된다(§9-6).
    """
    ids = list(dict.fromkeys(pattern_ids))  # 중복 제거 (순서 유지)
    if ids:
        placeholders = ",".join("?" * len(ids))
        found = {r["id"] for r in conn.execute(
            f"SELECT id FROM patterns WHERE id IN ({placeholders})", ids
        )}
        missing = [p for p in ids if p not in found]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"존재하지 않는 패턴 id: {missing}",
            )
    current = {r["pattern_id"] for r in conn.execute(
        "SELECT pattern_id FROM case_patterns WHERE case_id=?", (cid,)
    )}
    target = set(ids)
    for pid in target - current:
        conn.execute(
            "INSERT INTO case_patterns (case_id, pattern_id) VALUES (?, ?)",
            (cid, pid),
        )
    for pid in current - target:
        conn.execute(
            "DELETE FROM case_patterns WHERE case_id=? AND pattern_id=?",
            (cid, pid),
        )


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
                f"INSERT INTO cases ({', '.join(_CASE_COLUMNS)}) "
                f"VALUES ({', '.join(':' + c for c in _CASE_COLUMNS)})",
                _case_params(req),
            )
            cid = cur.lastrowid
            # 패턴 연결을 같은 트랜잭션에서 반영 — 실패 시 케이스 생성까지 롤백
            if req.pattern_ids is not None:
                _apply_pattern_links(conn, cid, req.pattern_ids)
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
                "UPDATE cases SET "
                + ", ".join(f"{c}=:{c}" for c in _CASE_COLUMNS)
                + ", updated_at=datetime('now') WHERE id=:id",
                {**_case_params(req), "id": cid},
            )
            # 패턴 연결 diff 를 같은 트랜잭션에서 반영 — 실패 시 본문 수정까지 롤백
            if req.pattern_ids is not None:
                _apply_pattern_links(conn, cid, req.pattern_ids)
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
    system = req.system.strip()
    reference_id = req.reference_id.strip()
    with get_conn(DB_PATH) as conn:
        # 같은 케이스 내 중복 방지 (대소문자 무시)
        dup = conn.execute(
            "SELECT 1 FROM case_references "
            "WHERE case_id=? AND system=? COLLATE NOCASE AND reference_id=? COLLATE NOCASE",
            (cid, system, reference_id),
        ).fetchone()
        if dup:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"이미 등록된 외부 참조입니다: {system}: {reference_id}",
            )
        cur = conn.execute(
            "INSERT INTO case_references (case_id, system, reference_id) VALUES (?, ?, ?)",
            (cid, system, reference_id),
        )
        rid = cur.lastrowid
    return {"id": rid, "system": system, "reference_id": reference_id}


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
