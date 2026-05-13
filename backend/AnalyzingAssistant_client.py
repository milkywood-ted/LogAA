import asyncio
import httpx
import yaml
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "config.yaml"

POLL_INTERVAL = 3.0   # 초
POLL_TIMEOUT  = 600.0 # 초 (10분)


def _load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _get_assistant() -> dict:
    config = _load_config()
    aa = config.get("analyzing_assistant")
    if not aa:
        raise ValueError("config.yaml 에 'analyzing_assistant' 설정이 없습니다")
    return aa


def _headers(aa: dict) -> dict:
    return {"X-API-Key": aa["api_key"]}


async def submit_analyze_job(
    problem_text: str,
    log_paths: list[str],
    profile_names: list[str] | None = None,
    pinned_case_name: str | None = None,
    parser_names: list[str] | None = None,
    input_keywords: list[str] | None = None,
    anchors: list[str] | None = None,
    recursive: bool = True,
) -> str:
    """분석 작업을 제출하고 job_id만 즉시 반환한다."""
    aa = _get_assistant()
    payload = {
        "problem_text": problem_text,
        "log_paths": log_paths,
        "profile_names": profile_names or [],
        "pinned_case_name": pinned_case_name,
        "parser_names": parser_names or [],
        "input_keywords": input_keywords or [],
        "anchors": anchors or [],
        "recursive": recursive,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        res = await client.post(f"{aa['url']}/analyze", json=payload, headers=_headers(aa))
        res.raise_for_status()
        return res.json()["job_id"]


async def get_analyze_job(job_id: str) -> dict:
    """job_id로 분석 진행 상태 및 결과를 조회한다."""
    aa = _get_assistant()
    async with httpx.AsyncClient(timeout=10.0) as client:
        res = await client.get(f"{aa['url']}/analyze/{job_id}", headers=_headers(aa))
        res.raise_for_status()
        return res.json()


async def analyze(
    problem_text: str,
    log_paths: list[str],
    profile_names: list[str] | None = None,
    pinned_case_name: str | None = None,
    parser_names: list[str] | None = None,
    input_keywords: list[str] | None = None,
    anchors: list[str] | None = None,
    recursive: bool = True,
) -> dict:
    """
    AnalyzingAssistant 에 분석 요청을 보내고 완료된 결과를 반환한다.

    Parameters
    ----------
    problem_text     : 문제 설명
    log_paths        : 서버 로컬 로그 파일/폴더 경로 목록
    profile_names    : 적용할 분석 프로파일 이름 목록 (선택)
    pinned_case_name : 직접 지정할 KB 케이스 이름 (선택)
    parser_names     : 적용할 로그 파서 이름 목록 (선택, 미지정 시 default 파서)
    input_keywords   : 로그 사전 필터링 키워드 (선택)
    anchors          : anchor 키워드 (선택)
    recursive        : 폴더 탐색 시 하위 폴더 재귀 여부

    Returns
    -------
    result dict (verdict, report_md, matched_case, match_result, ...)
    """
    aa = _get_assistant()
    base_url = aa["url"]
    headers = _headers(aa)

    payload = {
        "problem_text": problem_text,
        "log_paths": log_paths,
        "profile_names": profile_names or [],
        "pinned_case_name": pinned_case_name,
        "parser_names": parser_names or [],
        "input_keywords": input_keywords or [],
        "anchors": anchors or [],
        "recursive": recursive,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        # 분석 요청 제출
        response = await client.post(
            f"{base_url}/analyze",
            json=payload,
            headers=headers,
        )
        response.raise_for_status()
        job_id = response.json()["job_id"]

    # 완료까지 polling
    result = await _poll(base_url, job_id, headers)
    return result


# ── Settings / Pipeline ──────────────────────────────────────────────────────

async def get_analysis_profiles() -> list:
    aa = _get_assistant()
    async with httpx.AsyncClient(timeout=10.0) as client:
        res = await client.get(f"{aa['url']}/settings/profiles", headers=_headers(aa))
        res.raise_for_status()
        return res.json()


async def get_guidelines() -> dict:
    aa = _get_assistant()
    async with httpx.AsyncClient(timeout=10.0) as client:
        res = await client.get(f"{aa['url']}/settings/guidelines", headers=_headers(aa))
        res.raise_for_status()
        return res.json()


async def save_guidelines(value: str) -> dict:
    aa = _get_assistant()
    async with httpx.AsyncClient(timeout=10.0) as client:
        res = await client.post(f"{aa['url']}/settings/guidelines", json={"value": value}, headers=_headers(aa))
        res.raise_for_status()
        return res.json()


async def query_num_ctx() -> dict:
    aa = _get_assistant()
    async with httpx.AsyncClient(timeout=15.0) as client:
        res = await client.get(f"{aa['url']}/settings/pipeline/num_ctx", headers=_headers(aa))
        res.raise_for_status()
        return res.json()


async def get_pipeline_config() -> dict:
    aa = _get_assistant()
    async with httpx.AsyncClient(timeout=10.0) as client:
        res = await client.get(f"{aa['url']}/settings/pipeline/config", headers=_headers(aa))
        res.raise_for_status()
        return res.json()


async def save_pipeline_config(data: dict) -> dict:
    aa = _get_assistant()
    async with httpx.AsyncClient(timeout=10.0) as client:
        res = await client.post(f"{aa['url']}/settings/pipeline/config", json=data, headers=_headers(aa))
        res.raise_for_status()
        return res.json()


# ── Settings / Active ────────────────────────────────────────────────────────

async def get_active_profiles() -> dict:
    aa = _get_assistant()
    async with httpx.AsyncClient(timeout=10.0) as client:
        res = await client.get(f"{aa['url']}/settings/active", headers=_headers(aa))
        res.raise_for_status()
        return res.json()


# ── Settings / LLM ───────────────────────────────────────────────────────────

async def get_llm_profiles() -> list[str]:
    aa = _get_assistant()
    async with httpx.AsyncClient(timeout=10.0) as client:
        res = await client.get(f"{aa['url']}/settings/llm/profiles", headers=_headers(aa))
        res.raise_for_status()
        return res.json()


async def get_llm_models(profile: str) -> list[str]:
    aa = _get_assistant()
    async with httpx.AsyncClient(timeout=10.0) as client:
        res = await client.get(f"{aa['url']}/settings/llm/models", params={"profile": profile}, headers=_headers(aa))
        res.raise_for_status()
        return res.json()


async def check_llm_connection(profile: str, model: str | None = None) -> dict:
    aa = _get_assistant()
    async with httpx.AsyncClient(timeout=30.0) as client:
        res = await client.post(
            f"{aa['url']}/settings/llm/check",
            json={"profile": profile, "model": model},
            headers=_headers(aa),
        )
        res.raise_for_status()
        return res.json()


async def get_llm_config(profile: str) -> dict:
    aa = _get_assistant()
    async with httpx.AsyncClient(timeout=10.0) as client:
        res = await client.get(f"{aa['url']}/settings/llm/config", params={"profile": profile}, headers=_headers(aa))
        res.raise_for_status()
        return res.json()


async def save_llm_config(profile: str, data: dict) -> dict:
    aa = _get_assistant()
    async with httpx.AsyncClient(timeout=10.0) as client:
        res = await client.post(
            f"{aa['url']}/settings/llm/config",
            json={"profile": profile, **data},
            headers=_headers(aa),
        )
        res.raise_for_status()
        return res.json()


# ── Settings / Embedding ──────────────────────────────────────────────────────

async def get_embedding_profiles() -> list[str]:
    aa = _get_assistant()
    async with httpx.AsyncClient(timeout=10.0) as client:
        res = await client.get(f"{aa['url']}/settings/embedding/profiles", headers=_headers(aa))
        res.raise_for_status()
        return res.json()


async def get_embedding_models(profile: str) -> list[str]:
    aa = _get_assistant()
    async with httpx.AsyncClient(timeout=10.0) as client:
        res = await client.get(f"{aa['url']}/settings/embedding/models", params={"profile": profile}, headers=_headers(aa))
        res.raise_for_status()
        return res.json()


async def check_embedding_connection(profile: str, model: str | None = None) -> dict:
    aa = _get_assistant()
    async with httpx.AsyncClient(timeout=30.0) as client:
        res = await client.post(
            f"{aa['url']}/settings/embedding/check",
            json={"profile": profile, "model": model},
            headers=_headers(aa),
        )
        res.raise_for_status()
        return res.json()


async def get_embedding_config(profile: str) -> dict:
    aa = _get_assistant()
    async with httpx.AsyncClient(timeout=10.0) as client:
        res = await client.get(f"{aa['url']}/settings/embedding/config", params={"profile": profile}, headers=_headers(aa))
        res.raise_for_status()
        return res.json()


async def save_embedding_config(profile: str, data: dict) -> dict:
    aa = _get_assistant()
    async with httpx.AsyncClient(timeout=10.0) as client:
        res = await client.post(
            f"{aa['url']}/settings/embedding/config",
            json={"profile": profile, **data},
            headers=_headers(aa),
        )
        res.raise_for_status()
        return res.json()


# ── 내부 헬퍼 ─────────────────────────────────────────────────────────────────

async def _poll(base_url: str, job_id: str, headers: dict) -> dict:
    """job 완료까지 polling 하고 result 를 반환한다."""
    elapsed = 0.0
    async with httpx.AsyncClient(timeout=30.0) as client:
        while elapsed < POLL_TIMEOUT:
            await asyncio.sleep(POLL_INTERVAL)
            elapsed += POLL_INTERVAL

            response = await client.get(
                f"{base_url}/analyze/{job_id}",
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()

            status = data["status"]
            if status == "done":
                return data["result"]
            if status == "error":
                raise RuntimeError(f"분석 실패 (job_id={job_id}): {data.get('error')}")
            # pending / running → 계속 대기

    raise TimeoutError(f"분석 시간 초과 (job_id={job_id}, {POLL_TIMEOUT}초)")