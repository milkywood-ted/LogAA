"""
FastAPI 백엔드

Core(WebDownloader)를 REST API로 노출합니다.
WebSocket으로 실시간 로그를 전달합니다.

실행:
    uvicorn main:app --reload --port 8000
"""

import sys
import asyncio
import logging
from pathlib import Path
from contextlib import asynccontextmanager

# Windows에서 Playwright subprocess 지원을 위해 ProactorEventLoop 사용
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
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


# =============================================================================
# FastAPI 앱
# =============================================================================

app = FastAPI(title="Puller API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# =============================================================================
# 요청/응답 모델
# =============================================================================

class SiteRequest(BaseModel):
    site_name: str
    until_step_name: str | None = None
    param_values: dict[str, str] = {}


# =============================================================================
# 헬퍼
# =============================================================================

def get_site_config(site_name: str, param_values: dict) -> dict:
    """사이트 설정을 로드하고 파라미터를 적용합니다."""
    config = load_config()
    sites  = config.get("sites", [])
    site   = next((s for s in sites if s["name"] == site_name), None)
    if not site:
        raise ValueError(f"사이트를 찾을 수 없습니다: '{site_name}'")

    final_url        = build_url(site["url"], site.get("parameters", []), param_values)
    site_with_params = {**site, "url": final_url}
    return site_with_params


def get_browser_config() -> dict:
    config = load_config()
    return config.get("browser", {})


def make_downloader(site_name: str, param_values: dict) -> WebDownloader:
    site_config    = get_site_config(site_name, param_values)
    browser_config = get_browser_config()
    return WebDownloader(site_config, browser_config)


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
                    action.get("individual_download")
                    for step in s.get("interactions", {}).get("steps", [])
                    for action in step.get("actions", [])
                    if action.get("type") == "download"
                )
            }
            for s in sites
        ]
    }


@app.post("/api/scan")
async def scan(req: SiteRequest):
    """셀렉터 스캔을 실행합니다."""
    try:
        downloader = make_downloader(req.site_name, req.param_values)
        result     = await downloader.scan(until_step_name=req.until_step_name)
        return {"success": result.success, "data": result.__dict__, "error": result.error}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/inspect")
async def inspect(req: SiteRequest):
    """페이지 탐색을 실행합니다."""
    try:
        downloader = make_downloader(req.site_name, req.param_values)
        result     = await downloader.inspect(until_step_name=req.until_step_name)
        return {"success": result.success, "data": result.__dict__, "error": result.error}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/read_table")
async def read_table(req: SiteRequest):
    """테이블 읽기를 실행합니다."""
    try:
        downloader = make_downloader(req.site_name, req.param_values)
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
        downloader = make_downloader(req.site_name, req.param_values)
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
        downloader = make_downloader(req.site_name, req.param_values)
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
                ]
            },
            "error": result.error
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/final")
async def final(req: SiteRequest):
    """통합 실행 (download + read_text + read_table)을 실행합니다."""
    try:
        downloader = make_downloader(req.site_name, req.param_values)
        result     = await downloader.final_result()
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
                ]
            },
            "error": result.error
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


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
    uvicorn.run(app, host='0.0.0.0', port=8000, loop='asyncio')