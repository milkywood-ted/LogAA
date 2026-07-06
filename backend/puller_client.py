import asyncio
import ssl
import httpx
from pathlib import Path
from config import config

CERT_PATH = Path(__file__).parent / "certs" / "server.crt"
DOWNLOAD_CHUNK_SIZE = 1024 * 1024
TIMEOUT_SHORT = 30.0
TIMEOUT_DOWNLOAD = 300.0
TIMEOUT_SYNC_FETCH = 1200.0

def _make_ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context(cafile=str(CERT_PATH))
    return ctx

def _make_client(**kwargs) -> httpx.AsyncClient:
    """httpx는 no_proxy CIDR를 지원하지 않아 프록시 환경에서 Puller IP가
    프록시를 경유할 수 있으므로, config.yaml의 puller_client.no_proxy 설정에 따라 제어한다."""
    cfg = config.puller_client()
    if cfg.get("no_proxy", False):
        # trust_env=False 로 환경변수 프록시(https_proxy 등)를 무시
        return httpx.AsyncClient(verify=_make_ssl_context(), trust_env=False, **kwargs)
    return httpx.AsyncClient(verify=_make_ssl_context(), **kwargs)


async def fetch_defect(puller_name: str, defect_id: str, credentials: dict | None = None) -> dict:
    puller = config.get_puller(puller_name)
    payload = {
        "site_name": puller["site_name"],
        "param_values": {"Defect ID": defect_id}
    }
    if credentials:
        payload["credentials"] = credentials

    if puller.get("async_fetch"):
        return await _fetch_defect_async(puller, payload)
    else:
        return await _fetch_defect_sync(puller, payload)

async def _fetch_defect_sync(puller: dict, payload: dict) -> dict:
    async with _make_client(timeout=TIMEOUT_SYNC_FETCH) as client:
        response = await client.post(
            f"{puller['url']}/api/final",
            json=payload
        )
        response.raise_for_status()
        return response.json()

async def _fetch_defect_async(puller: dict, payload: dict) -> dict:
    async with _make_client(timeout=TIMEOUT_SHORT) as client:
        response = await client.post(
            f"{puller['url']}/api/final/start",
            json=payload
        )
        response.raise_for_status()
        job_id = response.json()["job_id"]

    async with _make_client(timeout=TIMEOUT_SHORT) as client:
        while True:
            response = await client.get(f"{puller['url']}/api/job/{job_id}")
            response.raise_for_status()
            job = response.json()

            if job["status"] == "done":
                return {"success": job.get("success", True), "data": job.get("data"), "error": job.get("error")}
            elif job["status"] == "error":
                return {"success": False, "data": None, "error": job.get("error", "Puller 오류")}

            await asyncio.sleep(2)

async def list_defect_files(puller_name: str, defect_id: str) -> list:
    puller = config.get_puller(puller_name)
    async with _make_client(timeout=TIMEOUT_SHORT) as client:
        response = await client.get(
            f"{puller['url']}/api/files/{defect_id}"
        )
        response.raise_for_status()
        return response.json()["files"]

async def list_comment_attachments(puller_name: str, defect_id: str) -> list:
    puller = config.get_puller(puller_name)
    async with _make_client(timeout=TIMEOUT_SHORT) as client:
        response = await client.get(
            f"{puller['url']}/api/comment_attachments/{defect_id}"
        )
        response.raise_for_status()
        return response.json()["items"]


async def _stream_to_file(client: httpx.AsyncClient, url: str, file_path: Path) -> None:
    async with client.stream("GET", url) as response:
        response.raise_for_status()
        with open(file_path, "wb") as f:
            async for chunk in response.aiter_bytes(chunk_size=DOWNLOAD_CHUNK_SIZE):
                f.write(chunk)


async def download_comment_attachment_file(
    puller_name: str, defect_id: str, index: int, filename: str, save_dir: Path
) -> Path:
    puller = config.get_puller(puller_name)
    save_dir.mkdir(parents=True, exist_ok=True)
    file_path = save_dir / filename
    async with _make_client(timeout=TIMEOUT_DOWNLOAD) as client:
        await _stream_to_file(client, f"{puller['url']}/api/comment_attachments/{defect_id}/{index}/{filename}", file_path)
    return file_path


async def download_defect_file(puller_name: str, defect_id: str, filename: str, save_dir: Path) -> Path:
    puller = config.get_puller(puller_name)
    save_dir.mkdir(parents=True, exist_ok=True)
    file_path = save_dir / filename
    async with _make_client(timeout=TIMEOUT_DOWNLOAD) as client:
        await _stream_to_file(client, f"{puller['url']}/api/files/{defect_id}/{filename}", file_path)
    return file_path
