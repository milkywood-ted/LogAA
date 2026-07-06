import asyncio
import httpx
from config import config

POLL_INTERVAL = 3.0   # 초
POLL_TIMEOUT  = 600.0 # 초 (10분)


class AnalyzingAssistantClient:

    def __init__(self):
        aa = config.get_active_analyzing_assistant()
        self._base_url: str = aa["url"]
        self._headers: dict = {"X-API-Key": aa["api_key"]}

    async def _get(self, path: str, timeout: float = 10.0, params: dict | None = None) -> dict:
        async with httpx.AsyncClient(timeout=timeout) as client:
            res = await client.get(f"{self._base_url}{path}", headers=self._headers, params=params)
            res.raise_for_status()
            return res.json()

    async def _post(self, path: str, body: dict, timeout: float = 10.0) -> dict:
        async with httpx.AsyncClient(timeout=timeout) as client:
            res = await client.post(f"{self._base_url}{path}", json=body, headers=self._headers)
            res.raise_for_status()
            return res.json()

    async def _put(self, path: str, body: dict, timeout: float = 10.0) -> dict:
        async with httpx.AsyncClient(timeout=timeout) as client:
            res = await client.put(f"{self._base_url}{path}", json=body, headers=self._headers)
            res.raise_for_status()
            return res.json()

    async def _delete(self, path: str, timeout: float = 10.0) -> dict:
        async with httpx.AsyncClient(timeout=timeout) as client:
            res = await client.delete(f"{self._base_url}{path}", headers=self._headers)
            res.raise_for_status()
            return res.json()

    def stream_url(self, job_id: str) -> tuple[str, dict]:
        """SSE 스트림 프록시에 필요한 (url, headers) 반환."""
        return f"{self._base_url}/analyze/{job_id}/stream", self._headers

    def _build_analyze_payload(
        self,
        problem_text: str,
        log_paths: list[str],
        profile_names: list[str] | None,
        pinned_case_name: str | None,
        parser_names: list[str] | None,
        input_keywords: list[str] | None,
        anchors: list[str] | None,
        recursive: bool,
        log_path_base: str | None = None,
        chip: list[str] | None = None,
        defect_id: str | None = None,
    ) -> dict:
        return {
            "problem_text": problem_text,
            "log_paths": log_paths,
            "log_path_base": log_path_base,
            "profile_names": profile_names or [],
            "pinned_case_name": pinned_case_name,
            "parser_names": parser_names or [],
            "input_keywords": input_keywords or [],
            "anchors": anchors or [],
            "recursive": recursive,
            "chip": chip or [],
            "defect_id": defect_id,
        }

    async def submit_analyze_job(
        self,
        problem_text: str,
        log_paths: list[str],
        profile_names: list[str] | None = None,
        pinned_case_name: str | None = None,
        parser_names: list[str] | None = None,
        input_keywords: list[str] | None = None,
        anchors: list[str] | None = None,
        recursive: bool = True,
        log_path_base: str | None = None,
        chip: list[str] | None = None,
        defect_id: str | None = None,
    ) -> str:
        payload = self._build_analyze_payload(
            problem_text, log_paths, profile_names, pinned_case_name,
            parser_names, input_keywords, anchors, recursive, log_path_base,
            chip, defect_id,
        )
        data = await self._post("/analyze", payload, timeout=30.0)
        return data["job_id"]

    async def analyze(
        self,
        problem_text: str,
        log_paths: list[str],
        profile_names: list[str] | None = None,
        pinned_case_name: str | None = None,
        parser_names: list[str] | None = None,
        input_keywords: list[str] | None = None,
        anchors: list[str] | None = None,
        recursive: bool = True,
        log_path_base: str | None = None,
        chip: list[str] | None = None,
        defect_id: str | None = None,
    ) -> dict:
        payload = self._build_analyze_payload(
            problem_text, log_paths, profile_names, pinned_case_name,
            parser_names, input_keywords, anchors, recursive, log_path_base,
            chip, defect_id,
        )
        data = await self._post("/analyze", payload, timeout=30.0)
        job_id = data["job_id"]
        return await self._poll(job_id)

    async def cancel_analyze_job(self, job_id: str) -> dict:
        return await self._delete(f"/analyze/{job_id}", timeout=10.0)

    async def get_analyze_job(self, job_id: str) -> dict:
        return await self._get(f"/analyze/{job_id}", timeout=60.0)

    # ── 분석 프로파일 CRUD ──────────────────────────────────────────────────
    async def get_analysis_profiles(self) -> list:
        return await self._get("/profiles")

    async def get_analysis_profile(self, name: str) -> dict:
        return await self._get(f"/profiles/{name}")

    async def create_analysis_profile(self, data: dict) -> dict:
        return await self._post("/profiles", data)

    async def update_analysis_profile(self, name: str, data: dict) -> dict:
        return await self._put(f"/profiles/{name}", data)

    async def delete_analysis_profile(self, name: str) -> dict:
        return await self._delete(f"/profiles/{name}")

    # ── 도메인 지식 CRUD ────────────────────────────────────────────────────
    async def get_knowledge_list(self) -> list:
        return await self._get("/knowledge")

    async def get_knowledge(self, kid: int) -> dict:
        return await self._get(f"/knowledge/{kid}")

    async def create_knowledge(self, data: dict) -> dict:
        # chromadb 임베딩이 포함될 수 있어 타임아웃을 넉넉히 둔다
        return await self._post("/knowledge", data, timeout=60.0)

    async def update_knowledge(self, kid: int, data: dict) -> dict:
        return await self._put(f"/knowledge/{kid}", data, timeout=60.0)

    async def delete_knowledge(self, kid: int) -> dict:
        return await self._delete(f"/knowledge/{kid}")

    # ── 케이스 CRUD ─────────────────────────────────────────────────────────
    async def get_cases(self) -> list:
        return await self._get("/cases")

    async def get_case(self, cid: int) -> dict:
        return await self._get(f"/cases/{cid}")

    async def create_case(self, data: dict) -> dict:
        return await self._post("/cases", data, timeout=60.0)

    async def update_case(self, cid: int, data: dict) -> dict:
        return await self._put(f"/cases/{cid}", data, timeout=60.0)

    async def delete_case(self, cid: int) -> dict:
        return await self._delete(f"/cases/{cid}")

    async def sync_cases(self) -> dict:
        return await self._post("/cases/sync", {}, timeout=120.0)

    async def get_case_patterns(self, cid: int) -> list:
        return await self._get(f"/cases/{cid}/patterns")

    async def link_pattern(self, cid: int, pid: int) -> dict:
        return await self._post(f"/cases/{cid}/patterns/{pid}", {})

    async def unlink_pattern(self, cid: int, pid: int) -> dict:
        return await self._delete(f"/cases/{cid}/patterns/{pid}")

    async def get_case_references(self, cid: int) -> list:
        return await self._get(f"/cases/{cid}/references")

    async def add_case_reference(self, cid: int, data: dict) -> dict:
        return await self._post(f"/cases/{cid}/references", data)

    async def delete_case_reference(self, cid: int, rid: int) -> dict:
        return await self._delete(f"/cases/{cid}/references/{rid}")

    # ── 패턴 CRUD ───────────────────────────────────────────────────────────
    async def get_patterns(self, pattern_type: str | None = None) -> list:
        params = {"type": pattern_type} if pattern_type else None
        return await self._get("/patterns", params=params)

    async def get_pattern(self, pid: int) -> dict:
        return await self._get(f"/patterns/{pid}")

    async def create_pattern(self, data: dict) -> dict:
        return await self._post("/patterns", data)

    async def update_pattern(self, pid: int, data: dict) -> dict:
        return await self._put(f"/patterns/{pid}", data)

    async def delete_pattern(self, pid: int) -> dict:
        return await self._delete(f"/patterns/{pid}")

    # ── 분석 이력 ───────────────────────────────────────────────────────────
    async def get_history_list(self, limit: int = 50, defect_id: str | None = None) -> list:
        params: dict = {"limit": limit}
        if defect_id is not None:
            params["defect_id"] = defect_id
        return await self._get("/history/", params=params)

    async def get_history(self, hid: int) -> dict:
        return await self._get(f"/history/{hid}")

    async def delete_history(self, hid: int) -> None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.delete(f"{self._base_url}/history/{hid}", headers=self._headers)
            res.raise_for_status()

    async def clear_history(self) -> dict:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.delete(f"{self._base_url}/history/", headers=self._headers)
            res.raise_for_status()
            return res.json()

    async def get_guidelines(self) -> dict:
        return await self._get("/settings/guidelines")

    async def save_guidelines(self, value: str) -> dict:
        return await self._post("/settings/guidelines", {"value": value})

    async def query_num_ctx(self) -> dict:
        return await self._get("/settings/pipeline/num_ctx", timeout=15.0)

    async def get_pipeline_config(self) -> dict:
        return await self._get("/settings/pipeline/config")

    async def save_pipeline_config(self, data: dict) -> dict:
        return await self._post("/settings/pipeline/config", data)

    async def get_server_config(self) -> dict:
        return await self._get("/settings/server/config")

    async def save_server_config(self, data: dict) -> dict:
        return await self._post("/settings/server/config", data)

    async def get_active_profiles(self) -> dict:
        return await self._get("/settings/active")

    async def get_llm_profiles(self) -> list[str]:
        return await self._get("/settings/llm/profiles")

    async def get_llm_models(self, profile: str) -> list[str]:
        return await self._get("/settings/llm/models", params={"profile": profile})

    async def check_llm_connection(self, profile: str, model: str | None = None) -> dict:
        return await self._post("/settings/llm/check", {"profile": profile, "model": model}, timeout=30.0)

    async def get_llm_config(self, profile: str) -> dict:
        return await self._get("/settings/llm/config", params={"profile": profile})

    async def save_llm_config(self, profile: str, data: dict) -> dict:
        return await self._post("/settings/llm/config", {"profile": profile, **data})

    async def get_embedding_profiles(self) -> list[str]:
        return await self._get("/settings/embedding/profiles")

    async def get_embedding_models(self, profile: str) -> list[str]:
        return await self._get("/settings/embedding/models", params={"profile": profile})

    async def check_embedding_connection(self, profile: str, model: str | None = None) -> dict:
        return await self._post("/settings/embedding/check", {"profile": profile, "model": model}, timeout=30.0)

    async def get_embedding_config(self, profile: str) -> dict:
        return await self._get("/settings/embedding/config", params={"profile": profile})

    async def save_embedding_config(self, profile: str, data: dict) -> dict:
        return await self._post("/settings/embedding/config", {"profile": profile, **data})

    async def _poll(self, job_id: str) -> dict:
        elapsed = 0.0
        async with httpx.AsyncClient(timeout=30.0) as client:
            while elapsed < POLL_TIMEOUT:
                await asyncio.sleep(POLL_INTERVAL)
                elapsed += POLL_INTERVAL
                res = await client.get(f"{self._base_url}/analyze/{job_id}", headers=self._headers)
                res.raise_for_status()
                data = res.json()
                if data["status"] == "done":
                    return data["result"]
                if data["status"] == "error":
                    raise RuntimeError(f"분석 실패 (job_id={job_id}): {data.get('error')}")
        raise TimeoutError(f"분석 시간 초과 (job_id={job_id}, {POLL_TIMEOUT}초)")


aa_client = AnalyzingAssistantClient()
