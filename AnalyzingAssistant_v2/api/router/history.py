"""
api/router/history.py

분석 이력 조회·삭제·사용자 입력 API.

history 테이블의 내용을 반환한다. result JSON은 목록에서 요약 필드만,
단건 조회에서 전체 + analysis_logs를 함께 반환한다.

엔드포인트 (prefix=/history):
    GET    /        목록 조회 (최신순, limit 파라미터)
    GET    /{hid}   단건 조회 (result 전체 + analysis_logs 포함)
    PATCH  /{hid}   사용자 분석 입력 저장 (user_content)
    DELETE /{hid}   단건 삭제
    DELETE /        전체 삭제
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from core.db import get_conn, DB_PATH

logger = logging.getLogger(__name__)

router = APIRouter()


class HistoryContentRequest(BaseModel):
    user_content: str = Field(..., description="'불확실' 판정에서 사용자가 직접 입력한 분석 내용")


def _parse_result(raw: str) -> dict:
    try:
        return json.loads(raw)
    except Exception:
        return {}


# ── 목록 조회 ─────────────────────────────────────────────────────────────────

@router.get("/")
def list_history(
    limit: int = Query(default=50, ge=1, le=500),
    defect_id: str | None = Query(default=None),
):
    """최신순 N건 — result에서 요약 필드만 반환. defect_id 지정 시 해당 건만."""
    with get_conn(DB_PATH) as conn:
        if defect_id is not None:
            rows = conn.execute(
                "SELECT id, input_hash, result, created_at, defect_id, user_content FROM history"
                " WHERE defect_id=? ORDER BY id DESC LIMIT ?",
                (defect_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, input_hash, result, created_at, defect_id, user_content FROM history"
                " ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()

    result = []
    for r in rows:
        parsed = _parse_result(r["result"])
        result.append({
            "id":            r["id"],
            "input_hash":    r["input_hash"],
            "created_at":    r["created_at"],
            "defect_id":     r["defect_id"],
            "verdict":       parsed.get("verdict"),
            "score":         parsed.get("score"),
            "matched_case":  parsed.get("matched_case"),
            "problem_text":  (parsed.get("problem_text") or "")[:120],
            "matched_patterns": parsed.get("matched_patterns", []),
            # 빈 문자열 = 미입력 — 이 값 자체로 "사용자 분석 입력" 버튼 노출 여부를
            # 판단한다(별도 상태 플래그 없음, 분석 리포트 개선 설계 §2/§4-2 검토).
            "user_content":  r["user_content"] or "",
        })
    return result


# ── 단건 조회 ─────────────────────────────────────────────────────────────────

@router.get("/{hid}")
def get_history(hid: int):
    """단건 조회 — result 전체 + analysis_logs 포함."""
    with get_conn(DB_PATH) as conn:
        row = conn.execute(
            "SELECT id, input_hash, result, created_at, defect_id, user_content "
            "FROM history WHERE id=?",
            (hid,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="history not found")

        logs = conn.execute(
            "SELECT id, stage, payload, created_at FROM analysis_logs WHERE history_id=? ORDER BY id",
            (hid,),
        ).fetchall()

    parsed_logs = []
    for lg in logs:
        try:
            payload = json.loads(lg["payload"])
        except Exception:
            payload = {}
        parsed_logs.append({
            "id":         lg["id"],
            "stage":      lg["stage"],
            "payload":    payload,
            "created_at": lg["created_at"],
        })

    return {
        "id":            row["id"],
        "input_hash":    row["input_hash"],
        "created_at":    row["created_at"],
        "defect_id":     row["defect_id"],
        "user_content":  row["user_content"] or "",
        "result":        _parse_result(row["result"]),
        "analysis_logs": parsed_logs,
    }


# ── 사용자 분석 입력 저장 ─────────────────────────────────────────────────────

@router.patch("/{hid}")
def update_history_content(hid: int, req: HistoryContentRequest):
    """'불확실' 판정에서 사용자가 입력한 분석 내용을 저장한다 (즉시/나중에 공용).

    분석 내용은 LLM 이 채우든 사용자가 직접 입력하든 같은 필드로 취급한다 —
    별도 "대기 상태" 플래그 없이, 이 필드가 비어있는지 여부만으로 이력
    페이지의 "사용자 분석 입력" 버튼 노출을 판단한다.
    """
    with get_conn(DB_PATH) as conn:
        cur = conn.execute(
            "UPDATE history SET user_content=? WHERE id=?",
            (req.user_content, hid),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="history not found")
    return {"id": hid, "user_content": req.user_content}


# ── 단건 삭제 ─────────────────────────────────────────────────────────────────

@router.delete("/{hid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_history(hid: int):
    with get_conn(DB_PATH) as conn:
        cur = conn.execute("DELETE FROM history WHERE id=?", (hid,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="history not found")


# ── 전체 삭제 ─────────────────────────────────────────────────────────────────

@router.delete("/", status_code=status.HTTP_200_OK)
def clear_history():
    with get_conn(DB_PATH) as conn:
        cur = conn.execute("DELETE FROM history")
        return {"deleted": cur.rowcount}
