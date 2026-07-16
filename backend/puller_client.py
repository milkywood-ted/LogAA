import asyncio
import ssl
import httpx
from pathlib import Path
from config import config

DOWNLOAD_CHUNK_SIZE = 1024 * 1024
TIMEOUT_SHORT = 30.0
TIMEOUT_DOWNLOAD = 300.0
TIMEOUT_SYNC_FETCH = 1200.0

def _make_verify() -> ssl.SSLContext | bool:
    """puller_client 설정에서 TLS 검증 방식을 결정한다 (환경별 조정 지점).

    - verify: false   → 검증 비활성 (테스트 환경 전용)
    - ca_cert: <경로> → 해당 파일을 CA로 신뢰. 상대 경로는 backend 디렉토리 기준.
                        파일이 없으면 안내 메시지와 함께 즉시 실패한다.
    - 둘 다 미설정    → 시스템 CA 저장소 사용 (공인 인증서·http Puller 환경)
    """
    cfg = config.puller_client()
    if cfg.get("verify") is False:
        return False
    ca_cert = cfg.get("ca_cert")
    if not ca_cert:
        return True
    path = Path(ca_cert)
    if not path.is_absolute():
        path = Path(__file__).parent / path
    if not path.exists():
        raise RuntimeError(
            f"puller_client.ca_cert 파일이 없습니다: {path} — "
            "인증서를 배치하거나 config.yaml에서 ca_cert 설정을 제거하세요."
        )
    return ssl.create_default_context(cafile=str(path))

def _make_client(**kwargs) -> httpx.AsyncClient:
    """httpx는 no_proxy CIDR를 지원하지 않아 프록시 환경에서 Puller IP가
    프록시를 경유할 수 있으므로, config.yaml의 puller_client.no_proxy 설정에 따라 제어한다."""
    cfg = config.puller_client()
    if cfg.get("no_proxy", False):
        # trust_env=False 로 환경변수 프록시(https_proxy 등)를 무시
        return httpx.AsyncClient(verify=_make_verify(), trust_env=False, **kwargs)
    return httpx.AsyncClient(verify=_make_verify(), **kwargs)


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
