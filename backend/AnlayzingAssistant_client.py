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