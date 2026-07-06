"""
FastAPI 백엔드

Core(WebDownloader)를 REST API로 노출합니다.
WebSocket으로 실시간 로그를 전달합니다.

실행:
    uvicorn main:app --reload --port 8000
"""

import sys
import uuid
import asyncio
import logging
from pathlib import Path
from contextlib import asynccontextmanager
from enum import Enum

# Windows에서 Playwright subprocess 지원을 위해 ProactorEventLoop 사용
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from core import (
    WebDownloader, load_config, build_url, get_input_params,
    DownloadResult, InspectResult, ScanResult,
    ReadTableResult, ReadTextResult, FinalResult
)


# =============================================================================
# 로그 WebSocket 브로드캐스터
# =============================================================================

class LogBroadcaster:
    """실행 로그를 WebSocket 클라이언트에 브로드캐스트합니다."""

    def __init__(self):
        self.clients: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.clients.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.clients:
            self.clients.remove(ws)

    async def broadcast(self, message: str):
        for client in self.clients[:]:
            try:
                await client.send_text(message)
            except Exception:
                self.clients.remove(client)


broadcaster = LogBroadcaster()


class WebSocketLogHandler(logging.Handler):
    """print 출력을 WebSocket으로 전달하는 핸들러"""

    def emit(self, record):
        msg = self.format(record)
        asyncio.create_task(broadcaster.broadcast(msg))


# print를 logging으로 리디렉션
class PrintCapture:
    def __init__(self, original):
        self.original = original

    def write(self, text):
        self.original.write(text)
        if text.strip():
            asyncio.create_task(broadcaster.broadcast(text.strip()))

    def flush(self):
        self.original.flush()

    def isatty(self):
        return self.original.isatty()


sys.stdout = PrintCapture(sys.stdout)


# =============================================================================
# FastAPI 앱
# =============================================================================

app = FastAPI(title="Puller API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=False,
    expose_headers=["Content-Disposition"],
)


# =============================================================================
# 요청/응답 모델
# =============================================================================

class Credentials(BaseModel):
    id: str
    pw: str

class SiteRequest(BaseModel):
    site_name: str
    until_step_name: str | None = None
    param_values: dict[str, str] = {}
    credentials: Credentials | None = None


# =============================================================================
# 헬퍼
# =============================================================================

def get_site_config(site_name: str, param_values: dict, credentials: dict = None) -> dict:
    """사이트 설정을 로드하고 파라미터를 적용합니다."""
    config = load_config()
    sites  = config.get("sites", [])
    site   = next((s for s in sites if s["name"] == site_name), None)
    if not site:
        raise ValueError(f"사이트를 찾을 수 없습니다: '{site_name}'")

    final_url        = build_url(site["url"], site.get("parameters", []), param_values)
    site_with_params = {**site, "url": final_url, "_param_values": param_values}

    if credentials:
        site_with_params["_credentials"] = credentials

    return site_with_params


def get_browser_config() -> dict:
    config = load_config()
    return config.get("browser", {})


def make_downloader(site_name: str, param_values: dict, credentials: dict = None) -> WebDownloader:
    site_config    = get_site_config(site_name, param_values, credentials)
    browser_config = get_browser_config()
    return WebDownloader(site_config, browser_config)


# =============================================================================
# Job 관리 (비동기 실행)
# =============================================================================

class JobStatus(str, Enum):
    running = "running"
    done    = "done"
    error   = "error"

class Job:
    def __init__(self):
        self.status: JobStatus = JobStatus.running
        self.result: dict      = None
        self.error: str        = None

jobs: dict[str, Job] = {}


async def _run_final_job(job_id: str, site_name: str, param_values: dict, until_step_name: str | None, credentials: dict = None):
    job = jobs[job_id]
    try:
        downloader = make_downloader(site_name, param_values, credentials)
        result     = await downloader.final_result()

        print(f"[job:{job_id}] success: {result.success}")
        print(f"[job:{job_id}] files: {len(result.files)}개")

        job.result = {
            "success": result.success,
            "data": {
                "title":       result.title,
                "current_url": result.current_url,
                "files": [
                    {"filename": f.filename, "path": f.path, "status": f.status}
                    for f in result.files
                ],
                "texts":  result.texts,
                "tables": [
                    {"selector": t.selector, "headers": t.headers, "rows": t.rows}
                    for t in result.tables
                ],
                "comment_attachment_items": result.comment_attachment_items,
            },
            "error": result.error,
        }
        job.status = JobStatus.done
    except Exception as e:
        print(f"[job:{job_id}] exception: {e}")
        job.error  = str(e)
        job.status = JobStatus.error


# =============================================================================
# 엔드포인트
# =============================================================================

@app.get("/api/config")
async def get_config():
    """사이트 목록 및 설정을 반환합니다."""
    config = load_config()
    sites  = config.get("sites", [])
    return {
        "sites": [
            {
                "name":         s["name"],
                "url":          s["url"],
                "input_params": get_input_params(s.get("parameters", [])),
                "steps":        [
                    {"name": step["name"], "final": step.get("final", False)}
                    for step in s.get("interactions", {}).get("steps", [])
                ],
                "has_individual_download": any(
                    action.get("type") == "individual_download"
                    for step in s.get("interactions", {}).get("steps", [])
                    for action in step.get("actions", [])
                )
            }
            for s in sites
        ]
    }


@app.post("/api/scan")
async def scan(req: SiteRequest):
    """셀렉터 스캔을 실행합니다."""
    try:
        downloader = make_downloader(req.site_name, req.param_values, req.credentials.model_dump() if req.credentials else None)
        result     = await downloader.scan(until_step_name=req.until_step_name)
        return {"success": result.success, "data": result.__dict__, "error": result.error}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/inspect")
async def inspect(req: SiteRequest):
    """페이지 탐색을 실행합니다."""
    try:
        downloader = make_downloader(req.site_name, req.param_values, req.credentials.model_dump() if req.credentials else None)
        result     = await downloader.inspect(until_step_name=req.until_step_name)
        return {"success": result.success, "data": result.__dict__, "error": result.error}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/read_table")
async def read_table(req: SiteRequest):
    """테이블 읽기를 실행합니다."""
    try:
        downloader = make_downloader(req.site_name, req.param_values, req.credentials.model_dump() if req.credentials else None)
        result     = await downloader.read_table()
        return {
            "success": result.success,
            "data": {
                "title":       result.title,
                "current_url": result.current_url,
                "tables": [
                    {
                        "selector": t.selector,
                        "headers":  t.headers,
                        "rows":     t.rows,
                    }
                    for t in result.tables
                ]
            },
            "error": result.error
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/read_text")
async def read_text(req: SiteRequest):
    """텍스트 읽기를 실행합니다."""
    try:
        downloader = make_downloader(req.site_name, req.param_values, req.credentials.model_dump() if req.credentials else None)
        result     = await downloader.read_text()
        return {
            "success": result.success,
            "data": {
                "title":       result.title,
                "current_url": result.current_url,
                "texts":       result.texts,
            },
            "error": result.error
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/download")
async def download(req: SiteRequest):
    """다운로드를 실행합니다."""
    try:
        downloader = make_downloader(req.site_name, req.param_values, req.credentials.model_dump() if req.credentials else None)
        result     = await downloader.download(until_step_name=req.until_step_name)
        return {
            "success": result.success,
            "data": {
                "total":         result.total,
                "success_count": result.success_count,
                "failed_count":  result.failed_count,
                "files": [
                    {
                        "filename": f.filename,
                        "path":     f.path,
                        "status":   f.status,
                    }
                    for f in result.files
                ],
                "comment_attachment_items": result.comment_attachment_items,
            },
            "error": result.error
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/lookup_comment_attachment")
async def lookup_comment_attachment(req: SiteRequest):
    """comment attachment 목록을 조회합니다."""
    try:
        downloader = make_downloader(req.site_name, req.param_values, req.credentials.model_dump() if req.credentials else None)
        items      = await downloader.lookup_comment_attachment(until_step_name=req.until_step_name)
        return {"success": True, "data": {"items": items}}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/download_comment_attachments")
async def download_comment_attachments(req: SiteRequest):
    """댓글 첨부파일을 다운로드합니다 (lookup → download 통합 실행)."""
    try:
        downloader = make_downloader(req.site_name, req.param_values, req.credentials.model_dump() if req.credentials else None)
        result     = await downloader.download(until_step_name=req.until_step_name)
        return {
            "success": result.success,
            "data": {
                "comment_attachment_items": result.comment_attachment_items,
            },
            "error": result.error,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/final")
async def final(req: SiteRequest):
    """통합 실행 (download + read_text + read_table)을 실행합니다."""
    try:
        downloader = make_downloader(req.site_name, req.param_values, req.credentials.model_dump() if req.credentials else None)
        result     = await downloader.final_result()

        print(f"[/api/final] success: {result.success}")
        print(f"[/api/final] files: {len(result.files)}개")
        print(f"[/api/final] texts: {result.texts}")
        print(f"[/api/final] tables: {len(result.tables)}개")
        if result.error:
            print(f"[/api/final] error: {result.error}")

        return {
            "success": result.success,
            "data": {
                "title":       result.title,
                "current_url": result.current_url,
                "files": [
                    {
                        "filename": f.filename,
                        "path":     f.path,
                        "status":   f.status,
                    }
                    for f in result.files
                ],
                "texts":  result.texts,
                "tables": [
                    {
                        "selector": t.selector,
                        "headers":  t.headers,
                        "rows":     t.rows,
                    }
                    for t in result.tables
                ],
                "comment_attachment_items": result.comment_attachment_items,
            },
            "error": result.error
        }
    except Exception as e:
        print(f"[/api/final] exception: {e}")
        return {"success": False, "error": str(e)}


@app.post("/api/final/start")
async def final_start(req: SiteRequest, background_tasks: BackgroundTasks):
    """비동기 통합 실행 — 즉시 job_id를 반환하고 백그라운드에서 실행합니다."""
    job_id = str(uuid.uuid4())
    jobs[job_id] = Job()
    background_tasks.add_task(
        _run_final_job,
        job_id,
        req.site_name,
        req.param_values,
        req.until_step_name,
        req.credentials.model_dump() if req.credentials else None,
    )
    return {"job_id": job_id}


@app.get("/api/job/{job_id}")
async def get_job(job_id: str):
    """Job 상태 및 결과를 반환합니다 (폴링용)."""
    job = jobs.get(job_id)
    if not job:
        return JSONResponse({"success": False, "error": f"job을 찾을 수 없습니다: {job_id}"}, status_code=404)

    if job.status == JobStatus.running:
        return {"status": "running"}
    if job.status == JobStatus.error:
        del jobs[job_id]
        return {"status": "error", "error": job.error}

    result = job.result
    del jobs[job_id]
    return {"status": "done", **result}


# =============================================================================
# 파일 전송
# =============================================================================

@app.get("/api/comment_attachments/{defect_id}")
async def list_comment_attachments(defect_id: str, site_name: str = None):
    """
    다운로드된 댓글 첨부파일 목록을 반환합니다.
    메타 파일(comment_attachments_meta.json)을 기반으로 반환합니다.
    """
    try:
        config   = load_config()
        sites    = config.get("sites", [])
        site     = next((s for s in sites if s["name"] == site_name), sites[0]) if sites else None
        if not site:
            return JSONResponse({"success": False, "error": "사이트 설정을 찾을 수 없습니다."})

        base_dir  = Path(site.get("download_dir", "./downloads"))
        ca_dir    = base_dir / defect_id / "CommentAttachment"
        meta_path = ca_dir / "comment_attachments_meta.json"

        if not meta_path.exists():
            return JSONResponse({"success": False, "error": f"메타 파일이 없습니다: {defect_id}"})

        import json
        meta  = json.loads(meta_path.read_text(encoding="utf-8"))
        items = meta.get("items", [])

        result = []
        for item in items:
            files = []
            for sel, els in item.get("sub_elements", {}).items():
                for el in els:
                    for path in el.get("download_path") or []:
                        filename = Path(path).name
                        idx      = item["index"]
                        files.append({
                            "filename": filename,
                            "url":      f"/api/comment_attachments/{defect_id}/{idx}/{filename}",
                            "downloaded": el.get("downloaded", False),
                            "error":      el.get("error"),
                        })
            result.append({
                "index": item["index"],
                "text":  item.get("text", ""),
                "files": files,
            })

        return {"success": True, "defect_id": defect_id, "items": result}

    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@app.get("/api/comment_attachments/{defect_id}/{index}/{filename}")
async def download_comment_attachment_file(defect_id: str, index: str, filename: str, site_name: str = None):
    """다운로드된 댓글 첨부파일을 전송합니다."""
    try:
        config   = load_config()
        sites    = config.get("sites", [])
        site     = next((s for s in sites if s["name"] == site_name), sites[0]) if sites else None
        if not site:
            return JSONResponse({"success": False, "error": "사이트 설정을 찾을 수 없습니다."})

        base_dir  = Path(site.get("download_dir", "./downloads"))
        file_path = base_dir / defect_id / "CommentAttachment" / index / filename

        if not file_path.exists():
            return JSONResponse({"success": False, "error": f"파일이 없습니다: {filename}"})

        return FileResponse(
            path       = str(file_path),
            filename   = filename,
            media_type = "application/octet-stream",
        )

    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@app.get("/api/files/{defect_id}")
async def list_files(defect_id: str, site_name: str = None):
    """
    특정 defect_id 폴더의 파일 목록을 반환합니다.

    Args:
        defect_id: Defect ID (하위 폴더명)
        site_name: 사이트명 (선택, 없으면 첫 번째 사이트 기준)
    """
    try:
        config   = load_config()
        sites    = config.get("sites", [])
        site     = next((s for s in sites if s["name"] == site_name), sites[0]) if sites else None
        if not site:
            return JSONResponse({"success": False, "error": "사이트 설정을 찾을 수 없습니다."})

        base_dir   = Path(site.get("download_dir", "./downloads"))
        target_dir = base_dir / defect_id

        if not target_dir.exists():
            return JSONResponse({"success": False, "error": f"폴더가 없습니다: {defect_id}"})

        files = [
            {
                "filename": f.name,
                "size":     f.stat().st_size,
                "size_kb":  round(f.stat().st_size / 1024, 1),
                "url":      f"/api/files/{defect_id}/{f.name}"
            }
            for f in sorted(target_dir.iterdir())
            if f.is_file()
        ]

        return {"success": True, "defect_id": defect_id, "files": files}

    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@app.get("/api/files/{defect_id}/{filename}")
async def download_file(defect_id: str, filename: str, site_name: str = None):
    """
    특정 파일을 스트림으로 전송합니다.

    Args:
        defect_id: Defect ID (하위 폴더명)
        filename: 파일명
        site_name: 사이트명 (선택, 없으면 첫 번째 사이트 기준)
    """
    try:
        config   = load_config()
        sites    = config.get("sites", [])
        site     = next((s for s in sites if s["name"] == site_name), sites[0]) if sites else None
        if not site:
            return JSONResponse({"success": False, "error": "사이트 설정을 찾을 수 없습니다."})

        base_dir  = Path(site.get("download_dir", "./downloads"))
        file_path = base_dir / defect_id / filename

        if not file_path.exists():
            return JSONResponse({"success": False, "error": f"파일이 없습니다: {filename}"})

        return FileResponse(
            path         = str(file_path),
            filename     = filename,
            media_type   = "application/octet-stream"
        )

    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


# =============================================================================
# WebSocket - 실시간 로그
# =============================================================================

@app.websocket("/ws/logs")
async def websocket_logs(ws: WebSocket):
    """실시간 로그를 WebSocket으로 전달합니다."""
    await broadcaster.connect(ws)
    try:
        while True:
            await ws.receive_text()  # 클라이언트 연결 유지
    except WebSocketDisconnect:
        broadcaster.disconnect(ws)


if __name__ == '__main__':
    import uvicorn
    from pathlib import Path as _Path
    _base = _Path(__file__).parent.parent.parent  # puller/
    uvicorn.run(
        app,
        host='0.0.0.0',
        port=8000,
        loop='asyncio',
        ssl_keyfile=str(_base / 'certs' / 'server.key'),
        ssl_certfile=str(_base / 'certs' / 'server.crt'),
    )
