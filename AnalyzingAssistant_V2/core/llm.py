"""
core/llm.py

LLM API 공통 헬퍼. OpenAI 호환 및 Anthropic 인터페이스를 지원한다.

공개 API:
    chat(messages, json_mode, temperature)             → str  (active_llm 프로필 사용)
    chat_with_profile(profile, messages, ...)          → str  (임의 프로필로 독립 호출)
    embed(texts)                                       → list[list[float]]
"""

from __future__ import annotations

import threading

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


import httpx
from anthropic import AnthropicBedrock
import subprocess

_BEDROCK_CA_CERT = "/usr/local/share/ca-certificates/samsungsemi-prx.com.crt"
_BEDROCK_PROXY_URL = "http://12.26.204.100:8080"
_BEDROCK_AWS_REGION = "ap-northeast-2"


def bedrock_client_kwargs(async_client: bool = False) -> dict:
    """AnthropicBedrock / AsyncAnthropicBedrock 생성에 필요한 공용 설정(프록시, 인증서, 리전)을 반환한다."""
    http_client_cls = httpx.AsyncClient if async_client else httpx.Client
    return {
        "aws_region": _BEDROCK_AWS_REGION,  # AWS_PROFILE, AWS_REGION 환경변수로도 대체 가능
        "http_client": http_client_cls(verify=_BEDROCK_CA_CERT, proxy=_BEDROCK_PROXY_URL),
    }


def ensure_sso_login():
    result = subprocess.run(
        ["aws", "sts", "get-caller-identity"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    if result.returncode != 0:
        subprocess.run(["aws", "sso", "login"], check=True)

def _chat_anthropic_bedrock(active: dict, messages: list[dict], model: str, json_mode: bool, temperature: float) -> str:
    client = AnthropicBedrock(**bedrock_client_kwargs())

    system_parts: list[str] = []
    user_messages: list[dict] = []
    for msg in messages:
        if msg["role"] == "system":
            system_parts.append(msg["content"])
        else:
            user_messages.append(msg)
    print(messages)
    print(user_messages)
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

    print(response)

    return _extract_text_block(response.content)

