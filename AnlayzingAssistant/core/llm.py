"""
core/llm.py

LLM API 공통 헬퍼. OpenAI 호환 및 Anthropic 인터페이스를 지원한다.

공개 API:
    chat(messages, json_mode, temperature) → str
    embed(texts)                           → list[list[float]]
"""

from __future__ import annotations

from openai import OpenAI

from core.config import cfg


def chat(
    messages: list[dict],
    model: str | None  = None,
    json_mode: bool    = False,
    temperature: float = 0.0,
) -> str:
    import core.config as _c
    active_cfg = _c.cfg
    active_model = model if model is not None else active_cfg.llm_model
    if active_cfg.llm_provider == "anthropic":
        return _chat_anthropic(active_cfg, messages, active_model, json_mode, temperature)
    return _chat_openai(active_cfg, messages, active_model, json_mode, temperature)


def _chat_openai(active_cfg, messages: list[dict], model: str, json_mode: bool, temperature: float) -> str:
    client = OpenAI(
        base_url = active_cfg.llm_base_url,
        api_key  = active_cfg.llm_api_key,
        timeout  = active_cfg.llm_timeout,
    )
    kwargs: dict = {
        "model":       model,
        "messages":    messages,
        "temperature": temperature,
    }
    if active_cfg.llm_max_tokens is not None:
        kwargs["max_tokens"] = active_cfg.llm_max_tokens
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    response = client.chat.completions.create(**kwargs)
    return response.choices[0].message.content.strip()


def _chat_anthropic(active_cfg, messages: list[dict], model: str, json_mode: bool, temperature: float) -> str:
    try:
        import anthropic
    except ImportError as exc:
        raise RuntimeError(
            "anthropic 패키지가 설치되어 있지 않습니다. `pip install anthropic` 를 실행하세요."
        ) from exc

    client_kwargs: dict = {"api_key": active_cfg.llm_api_key}
    if active_cfg.llm_timeout is not None:
        client_kwargs["timeout"] = active_cfg.llm_timeout
    if active_cfg.llm_base_url:
        client_kwargs["base_url"] = active_cfg.llm_base_url
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
        "max_tokens":  active_cfg.llm_max_tokens or 4096,
    }
    if system:
        kwargs["system"] = system

    response = client.messages.create(**kwargs)
    return response.content[0].text.strip()


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
    client = OpenAI(base_url=cfg.embed_base_url, api_key=cfg.embed_api_key)

    response = client.embeddings.create(
        model = cfg.embed_model,
        input = texts,
    )
    # 입력 순서 보장
    items = sorted(response.data, key=lambda x: x.index)
    return [item.embedding for item in items]
