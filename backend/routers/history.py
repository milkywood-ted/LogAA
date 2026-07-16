"""
routers/history.py

분석 이력 조회·삭제 프록시 라우터.

AnalyzingAssistant(AA) 서버의 /history 엔드포인트를
/api/history/* 경로로 패스스루한다.
"""

from fastapi import APIRouter, Query

from AnalyzingAssistant_client import aa_client
from routers._errors import propagate_aa_errors

router = APIRouter()


@router.get("/api/history")
async def list_history(
    limit: int = Query(default=50, ge=1, le=500),
    defect_id: str | None = Query(default=None),
):
    with propagate_aa_errors():
        return await aa_client.get_history_list(limit=limit, defect_id=defect_id)


@router.get("/api/history/{hid}")
async def get_history(hid: int):
    with propagate_aa_errors():
        return await aa_client.get_history(hid)


@router.delete("/api/history/{hid}", status_code=204)
async def delete_history(hid: int):
    with propagate_aa_errors():
        await aa_client.delete_history(hid)


@router.delete("/api/history")
async def clear_history():
    with propagate_aa_errors():
        return await aa_client.clear_history()
