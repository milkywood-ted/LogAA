"""
core/llm.py

LLM API 공통 헬퍼. OpenAI 호환 및 Anthropic 인터페이스를 지원한다.

공개 API:
    chat(messages, json_mode, temperature)             → str  (active_llm 프로필 사용)
    chat_with_profile(profile, messages, ...)          → str  (임의 프로필로 독립 호출)
    embed(texts)                                       → list[list[float]]
    rerank(profile, query, documents, top_n)           → list[(index, score)]  (cross-encoder rerank 엔드포인트)
"""

from __future__ import annotations

import subprocess
import threading

import httpx
from openai import OpenAI

import core.config as config


def _extract_text_block(content: list) -> str:
    """Anthropic 응답의 content 배열에서 text 블록만 추출해 이어붙인다.

    Sonnet 5 이상 모델은 thinking 파라미터를 생략해도 adaptive thinking이 기본
    활성화되어 content[0]이 ThinkingBlock일 수 있으므로 인덱스 접근 대신 type으로 찾는다.
    """
    parts = [block.text for block in content if block.type == "text"]
    if not parts:
        raise RuntimeError(f"LLM 응답에 text 블록이 없습니다: {content!r}")
    return "".join(parts).strip()


class RerankError(Exception):
    """rerank 엔드포인트 호출이 실패했거나 응답 형식이 예상과 다를 때 발생한다."""


def rerank(
    profile: dict,
    query: str,
    documents: list[str],
    top_n: int | None = None,
) -> list[tuple[int, float]]:
    """rerank 엔드포인트를 호출해 (documents 의 0-based index, relevance_score) 목록을 반환한다.

    chat_with_profile 과 마찬가지로 지정된 프로필로 독립 호출하며, active_llm 과 무관하다.
    현재는 vLLM (Cohere/Jina 호환 /rerank) 만 지원한다.
    """
    provider = profile.get("provider", "")
    if provider == "vllm-rerank":
        return _rerank_vllm(profile, query, documents, top_n)
    raise RerankError(f"지원하지 않는 rerank provider: {provider!r}")


def _rerank_vllm(
    profile: dict,
    query: str,
    documents: list[str],
    top_n: int | None,
) -> list[tuple[int, float]]:
    """vLLM rerank 엔드포인트(Cohere/Jina 호환) 호출.

    URL = base_url + rerank_path. 이 코드베이스의 base_url 컨벤션은 '/v1' 로
    끝나므로(chat/embed 프로필과 동일), rerank_path 기본값 '/rerank' 를 붙이면
    실효 경로는 '/v1/rerank' 가 된다. vLLM 은 /rerank·/v1/rerank·/v2/rerank 를
    모두 루트에 등록하므로, base_url 이 이미 '/v1' 을 포함하는데 rerank_path 에
    다시 '/v1/rerank' 를 지정하면 '/v1/v1/rerank' 로 404 가 난다 — 설정 시 주의.
    """
    base_url    = profile.get("base_url", "").rstrip("/")
    rerank_path = profile.get("rerank_path", "/rerank")
    url         = f"{base_url}{rerank_path}"

    headers: dict = {}
    api_key = profile.get("api_key", "")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload: dict = {
        "model":     profile.get("model", ""),
        "query":     query,
        "documents": documents,
    }
    if top_n is not None:
        payload["top_n"] = top_n

    try:
        response = httpx.post(
            url, json=payload, headers=headers, timeout=profile.get("timeout"),
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise RerankError(
            f"rerank 엔드포인트 호출 실패 (HTTP {e.response.status_code}): {url}"
        ) from e
    except httpx.HTTPError as e:
        raise RerankError(f"rerank 엔드포인트 연결 실패: {url} ({e})") from e

    try:
        data = response.json()
        results = data["results"]
        parsed = [(int(r["index"]), float(r["relevance_score"])) for r in results]
    except (KeyError, TypeError, ValueError) as e:
        raise RerankError(
            f"rerank 응답 형식이 예상과 다릅니다 (model={profile.get('model', '')}): {e} | "
            f"응답: {response.text[:300]!r}"
        ) from e

    if not parsed:
        raise RerankError(
            f"rerank 엔드포인트가 빈 결과를 반환했습니다 (model={profile.get('model', '')})."
        )
    return parsed


def chat_with_profile(
    profile: dict,
    messages: list[dict],
    model: str | None  = None,
    json_mode: bool    = False,
    temperature: float = 0.0,
) -> str:
    """지정된 LLM 프로필(provider/base_url/api_key 등)로 독립적으로 chat 을 호출한다.

    active_llm 과 무관하게 동작해야 하는 용도(예: reranker 전용 LLM)에 사용한다.
    """
    resolved_model = model if model is not None else profile.get("model", "")
    provider = profile.get("provider", "openai")

    if provider == "anthropic":
        return _chat_anthropic(profile, messages, resolved_model, json_mode, temperature)
    elif provider == "anthropic-bedrock":
        return _chat_anthropic_bedrock(profile, messages, resolved_model, json_mode, temperature)
    return _chat_openai(profile, messages, resolved_model, json_mode, temperature)


def chat(
    messages: list[dict],
    model: str | None  = None,
    json_mode: bool    = False,
    temperature: float = 0.0,
) -> str:
    return chat_with_profile(config.active_llm(), messages, model, json_mode, temperature)


def _chat_openai(active: dict, messages: list[dict], model: str, json_mode: bool, temperature: float) -> str:
    client = OpenAI(
        base_url = active.get("base_url", ""),
        api_key  = active.get("api_key", ""),
        timeout  = active.get("timeout"),
    )
    kwargs: dict = {
        "model":       model,
        "messages":    messages,
        "temperature": temperature,
    }
    max_tokens = active.get("max_tokens")
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    response = client.chat.completions.create(**kwargs)
    content = response.choices[0].message.content
    if not content or not content.strip():
        finish_reason = response.choices[0].finish_reason
        raise RuntimeError(
            f"LLM이 빈 응답을 반환했습니다 (model={model}, finish_reason={finish_reason!r}). "
            "서버 과부하 또는 모델 오류일 수 있습니다."
        )
    return content.strip()


def _chat_anthropic(active: dict, messages: list[dict], model: str, json_mode: bool, temperature: float) -> str:
    try:
        import anthropic
    except ImportError as exc:
        raise RuntimeError(
            "anthropic 패키지가 설치되어 있지 않습니다. `pip install anthropic` 를 실행하세요."
        ) from exc

    client_kwargs: dict = {"api_key": active.get("api_key", "")}
    timeout = active.get("timeout")
    if timeout is not None:
        client_kwargs["timeout"] = timeout
    base_url = active.get("base_url", "")
    if base_url:
        client_kwargs["base_url"] = base_url
    client = anthropic.Anthropic(**client_kwargs)

    system_parts: list[str] = []
    user_messages: list[dict] = []
    for msg in messages:
        if msg["role"] == "system":
            system_parts.append(msg["content"])
        else:
            user_messages.append(msg)

    system = "\n\n".join(system_parts)
    if json_mode:
        system = (system + "\n\nRespond only with valid JSON.").strip()

    kwargs: dict = {
        "model":       model,
        "messages":    user_messages,
        "temperature": temperature,
        "max_tokens":  active.get("max_tokens") or 4096,
    }
    if system:
        kwargs["system"] = system

    response = client.messages.create(**kwargs)
    return _extract_text_block(response.content)


def chat_stream(
    messages: list[dict],
    model: str | None       = None,
    temperature: float      = 0.0,
    cancel_event: threading.Event | None = None,
) -> str:
    """
    스트리밍 모드로 LLM을 호출하고 전체 응답 문자열을 반환한다.

    청크 수신 간격마다 cancel_event를 확인하여 취소 시 InterruptedError를 raise한다.
    Anthropic은 스트리밍 미지원으로 기존 chat()으로 fallback한다.
    """
    active = config.active_llm()
    active_model = model if model is not None else active.get("model", "")

    if active.get("provider", "openai") == "anthropic":
        # Anthropic 스트리밍은 별도 처리가 필요하므로 단순 fallback
        return _chat_anthropic(active, messages, active_model, False, temperature)
    elif active.get("provider", "openai") == "anthropic-bedrock":
        return _chat_anthropic_bedrock(active, messages, active_model, False, temperature)

    client = OpenAI(
        base_url = active.get("base_url", ""),
        api_key  = active.get("api_key", ""),
        timeout  = active.get("timeout"),
    )
    kwargs: dict = {
        "model":       active_model,
        "messages":    messages,
        "temperature": temperature,
        "stream":      True,
    }
    max_tokens = active.get("max_tokens")
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens

    result = []
    for chunk in client.chat.completions.create(**kwargs):
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("LLM 스트리밍 중 취소 요청")
        delta = chunk.choices[0].delta.content
        if delta:
            result.append(delta)
    return "".join(result).strip()


def embed(texts: list[str]) -> list[list[float]]:
    """
    텍스트 목록을 임베딩 벡터로 변환한다.

    Parameters
    ----------
    texts : 임베딩할 문자열 목록

    Returns
    -------
    각 텍스트에 대응하는 float 벡터 목록
    """
    active = config.active_embed()
    client = OpenAI(
        base_url = active.get("base_url", ""),
        api_key  = active.get("api_key", ""),
    )

    response = client.embeddings.create(
        model = active.get("model", ""),
        input = texts,
    )
    # 입력 순서 보장
    items = sorted(response.data, key=lambda x: x.index)
    return [item.embedding for item in items]


def bedrock_client_kwargs(async_client: bool = False) -> dict:
    """AnthropicBedrock / AsyncAnthropicBedrock 생성에 필요한 공용 설정(프록시, 인증서, 리전)을 반환한다.

    값은 config/LLM/config.yaml 의 bedrock 섹션에서 읽는다:

        bedrock:
          aws_region: ...   # 비우면 AWS_PROFILE / AWS_REGION 환경변수를 따른다
          ca_cert: ...      # 프록시 CA 인증서 경로 (비우면 시스템 기본 인증서)
          proxy_url: ...    # 프록시 URL (비우면 프록시 미사용)

    비어 있는 항목은 kwargs 에서 생략되어 SDK/환경 기본값이 적용된다.
    """
    kwargs: dict = {}

    aws_region = config.get_str("bedrock.aws_region", "")
    if aws_region:
        kwargs["aws_region"] = aws_region

    ca_cert   = config.get_str("bedrock.ca_cert", "")
    proxy_url = config.get_str("bedrock.proxy_url", "")
    if ca_cert or proxy_url:
        http_client_kwargs: dict = {}
        if ca_cert:
            http_client_kwargs["verify"] = ca_cert
        if proxy_url:
            http_client_kwargs["proxy"] = proxy_url
        http_client_cls = httpx.AsyncClient if async_client else httpx.Client
        kwargs["http_client"] = http_client_cls(**http_client_kwargs)

    return kwargs


def ensure_sso_login():
    result = subprocess.run(
        ["aws", "sts", "get-caller-identity"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    if result.returncode != 0:
        subprocess.run(["aws", "sso", "login"], check=True)

def _chat_anthropic_bedrock(active: dict, messages: list[dict], model: str, json_mode: bool, temperature: float) -> str:
    try:
        from anthropic import AnthropicBedrock
    except ImportError as exc:
        raise RuntimeError(
            "anthropic 패키지가 설치되어 있지 않습니다. `pip install anthropic` 를 실행하세요."
        ) from exc

    client = AnthropicBedrock(**bedrock_client_kwargs())

    system_parts: list[str] = []
    user_messages: list[dict] = []
    for msg in messages:
        if msg["role"] == "system":
            system_parts.append(msg["content"])
        else:
            user_messages.append(msg)

    system = "\n\n".join(system_parts)
    if json_mode:
        system = (system + "\n\nRespond only with valid JSON.").strip()

    kwargs: dict = {
        "model":       model,
        "messages":    user_messages,
        "max_tokens":  active.get("max_tokens") or 4096,
    }
    if system:
        kwargs["system"] = system
    response = client.messages.create(**kwargs)

    return _extract_text_block(response.content)

