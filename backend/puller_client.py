import httpx
import yaml
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "config.yaml"

def load_pullers() -> list:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        print(f)
        config = yaml.safe_load(f)
    return config.get("pullers", [])

def get_puller(name: str) -> dict:
    pullers = load_pullers()
    for p in pullers:
        if p["name"] == name:
            return p
    raise ValueError(f"Puller '{name}' 를 찾을 수 없습니다")

async def fetch_defect(puller_name: str, defect_id: str) -> dict:
    puller = get_puller(puller_name)
    payload = {
        "site_name": puller["site_name"],
        "param_values": {"Defect ID": defect_id}
    }
    async with httpx.AsyncClient(timeout=None) as client:
        response = await client.post(
            f"{puller['url']}/api/final",
            json=payload
        )
        response.raise_for_status()
        return response.json()

async def list_defect_files(puller_name: str, defect_id: str) -> list:
    puller = get_puller(puller_name)
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{puller['url']}/api/files/{defect_id}"
        )
        response.raise_for_status()
        return response.json()["files"]

async def download_defect_file(puller_name: str, defect_id: str, filename: str, save_dir: Path) -> Path:
    puller = get_puller(puller_name)
    save_dir.mkdir(parents=True, exist_ok=True)
    file_path = save_dir / filename
    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream(
            "GET",
            f"{puller['url']}/api/files/{defect_id}/{filename}"
        ) as response:
            response.raise_for_status()
            with open(file_path, "wb") as f:
                async for chunk in response.aiter_bytes(chunk_size=1024 * 1024):
                    f.write(chunk)
    return file_path
