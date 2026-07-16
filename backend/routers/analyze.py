import json
import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from config import config
from chip_resolver import resolve_meta
from AnalyzingAssistant_client import aa_client
from routers._errors import propagate_aa_errors

router = APIRouter()


class AnalyzeRequest(BaseModel):
    defect_id: str
    profile_names: list[str] = []
    pinned_case_name: str | None = None
    parser_names: list[str] = []
    input_keywords: list[str] = []
    anchors: list[str] = []
    selected_files: list[str] | None = None  # None이면 전체 대상, 지정 시 해당 파일만 분석


@router.post("/api/defect/analyze")
async def defect_analyze(req: AnalyzeRequest):
    meta_path = config.workspace() / req.defect_id / "meta.json"
    if not meta_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"defect_id '{req.defect_id}' 를 찾을 수 없습니다. 먼저 /api/defect/fetch 를 호출하세요.",
        )

    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)

    description = meta.get("description", {})
    if isinstance(description, dict):
        problem_text = "\n".join(f"{k}: {v}" for k, v in description.items() if v)
    else:
        problem_text = str(description)

    if not problem_text.strip():
        problem_text = meta.get("title", req.defect_id)

    if req.selected_files:
        log_paths = req.selected_files
        recursive = False
    else:
        log_paths = [meta["workspace"]]
        recursive = True

    # 로그 출처는 defect_id 기준 상대경로로 표기 (workspace = defect_id의 부모)
    log_path_base = str(config.workspace())

    # 1단계에서 meta.json 에 저장된 칩 정보 — 패턴 chip_tags 필터링에 사용.
    # chip 미보정 레거시 defect는 메모리에서만 resolve해 사용한다 (파일 미갱신, §9-6).
    # 매핑 미히트 시 None 이며, 이 경우 필터링하지 않는다 (AA 측 chip_filter).
    chip = resolve_meta(meta).get("chip")

    with propagate_aa_errors():
        job_id = await aa_client.submit_analyze_job(
            problem_text=problem_text,
            log_paths=log_paths,
            profile_names=req.profile_names,
            pinned_case_name=req.pinned_case_name,
            parser_names=req.parser_names,
            input_keywords=req.input_keywords,
            anchors=req.anchors,
            recursive=recursive,
            log_path_base=log_path_base,
            chip=chip,
            defect_id=req.defect_id,
        )
    return {"job_id": job_id}


@router.get("/api/defect/analyze/{job_id}")
async def get_defect_analyze(job_id: str):
    with propagate_aa_errors():
        return await aa_client.get_analyze_job(job_id)


@router.delete("/api/defect/analyze/{job_id}")
async def cancel_defect_analyze(job_id: str):
    with propagate_aa_errors():
        return await aa_client.cancel_analyze_job(job_id)


@router.get("/api/defect/analyze/{job_id}/stream")
async def stream_defect_analyze(job_id: str):
    """AA의 SSE 스트림을 프론트엔드에 그대로 포워딩한다.

    스트림 도중 오류는 HTTP 상태코드로 변환할 수 없는 구조이므로
    오류 전파 규약(§4.2) 적용 대상에서 제외한다.
    """
    url, headers = aa_client.stream_url(job_id)

    async def proxy():
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("GET", url, headers=headers) as res:
                async for chunk in res.aiter_bytes():
                    yield chunk

    return StreamingResponse(
        proxy(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":     "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
