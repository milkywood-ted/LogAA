"""
routers/cases.py

케이스 / 패턴 관리 프록시 라우터.

AnalyzingAssistant(AA) 서버의 /cases, /patterns CRUD 엔드포인트를
프론트엔드용 /api/* 경로로 패스스루한다. AA 가 반환하는 4xx/5xx
상태코드는 그대로 프론트에 전파한다.

저장 계열(케이스·패턴·참조)의 요청 본문은 **검증 없이 raw JSON 그대로
AA 에 전달**한다 (§9-4). 스키마의 유일한 규범은 AA 의 Pydantic 모델
(AA api/router/cases.py — 케이스 v2 조건부 필수 검증 포함)이며, 프록시가
필드 목록을 알 필요가 없으므로 미러 동기화·조용한 필드 유실이 구조적으로
발생하지 않는다. 검증 오류(422)는 propagate_aa_errors 로 원문 전파된다.
"""

from fastapi import APIRouter, Request

from AnalyzingAssistant_client import aa_client
from routers._errors import propagate_aa_errors

router = APIRouter()


# ── 케이스 ────────────────────────────────────────────────────────────────────

@router.get("/api/cases")
async def list_cases():
    with propagate_aa_errors():
        return await aa_client.get_cases()


@router.get("/api/cases/{cid}")
async def get_case(cid: int):
    with propagate_aa_errors():
        return await aa_client.get_case(cid)


@router.post("/api/cases")
async def create_case(request: Request):
    with propagate_aa_errors():
        return await aa_client.create_case(await request.json())


@router.put("/api/cases/{cid}")
async def update_case(cid: int, request: Request):
    with propagate_aa_errors():
        return await aa_client.update_case(cid, await request.json())


@router.delete("/api/cases/{cid}")
async def delete_case(cid: int):
    with propagate_aa_errors():
        return await aa_client.delete_case(cid)


@router.post("/api/cases/sync")
async def sync_cases():
    with propagate_aa_errors():
        return await aa_client.sync_cases()


# ── 케이스 ↔ 패턴 연결 ────────────────────────────────────────────────────────

@router.get("/api/cases/{cid}/patterns")
async def list_case_patterns(cid: int):
    with propagate_aa_errors():
        return await aa_client.get_case_patterns(cid)


@router.post("/api/cases/{cid}/patterns/{pid}")
async def link_pattern(cid: int, pid: int):
    with propagate_aa_errors():
        return await aa_client.link_pattern(cid, pid)


@router.delete("/api/cases/{cid}/patterns/{pid}")
async def unlink_pattern(cid: int, pid: int):
    with propagate_aa_errors():
        return await aa_client.unlink_pattern(cid, pid)


# ── 케이스 외부 참조 ID ───────────────────────────────────────────────────────

@router.get("/api/cases/{cid}/references")
async def list_case_references(cid: int):
    with propagate_aa_errors():
        return await aa_client.get_case_references(cid)


@router.post("/api/cases/{cid}/references")
async def add_case_reference(cid: int, request: Request):
    with propagate_aa_errors():
        return await aa_client.add_case_reference(cid, await request.json())


@router.delete("/api/cases/{cid}/references/{rid}")
async def delete_case_reference(cid: int, rid: int):
    with propagate_aa_errors():
        return await aa_client.delete_case_reference(cid, rid)


# ── 패턴 ─────────────────────────────────────────────────────────────────────

@router.get("/api/patterns")
async def list_patterns(type: str | None = None):
    with propagate_aa_errors():
        return await aa_client.get_patterns(type)


@router.get("/api/patterns/{pid}")
async def get_pattern(pid: int):
    with propagate_aa_errors():
        return await aa_client.get_pattern(pid)


@router.post("/api/patterns")
async def create_pattern(request: Request):
    with propagate_aa_errors():
        return await aa_client.create_pattern(await request.json())


@router.put("/api/patterns/{pid}")
async def update_pattern(pid: int, request: Request):
    with propagate_aa_errors():
        return await aa_client.update_pattern(pid, await request.json())


@router.delete("/api/patterns/{pid}")
async def delete_pattern(pid: int):
    with propagate_aa_errors():
        return await aa_client.delete_pattern(pid)
