"""
core/context_strategy.py

LLM num_ctx 초과 시 컨텍스트 처리 전략.

전략:
  truncation — 우선순위 순으로 컨텍스트를 채우고, 초과분 버림 (빠름, 단순)
  split      — 지식 컨텍스트를 분할, 여러 번 호출하여 이어받음 (느림, 손실 없음)
  hybrid     — truncation 먼저 시도, 초과 비율이 임계값 이상이면 split 전환

우선순위 (높음 → 낮음):
  1. system_guidelines   — 항상 최우선 포함
  2. analysis_guidelines
  3. knowledge_context   — 가장 먼저 잘리거나 분할됨

사용 예시:
    from core.context_strategy import truncate_context, split_knowledge_chunks

    ctx = truncate_context("지침", "프로파일 지침", "사전지식...", 500, 8192)
    print(ctx.knowledge_context)   # 잘린 결과
    print(ctx.was_truncated)       # True/False
"""

from __future__ import annotations

from dataclasses import dataclass

# 토큰 추정: 한국어/영어 혼합 기준 보수적 추정값 (3 chars ≈ 1 token)
CHARS_PER_TOKEN: int = 3
OVERHEAD_TOKENS: int = 4_000   # 프롬프트 구조 + 출력 버퍼 예약


def estimate_tokens(text: str) -> int:
    """간단한 토큰 수 추정. 3 chars ≈ 1 token (한국어/영어 혼합 기준)."""
    return max(0, len(text) // CHARS_PER_TOKEN)


@dataclass
class ContextResult:
    """truncate_context() 반환값."""
    system_guidelines: str
    analysis_guidelines: str
    knowledge_context: str
    was_truncated: bool = False
    truncated_tokens: int = 0


_TRUNCATE_SUFFIX_ANALYSIS  = "\n... (분석 지침 일부 생략됨)"
_TRUNCATE_SUFFIX_KNOWLEDGE = "\n... (사전지식 일부 생략됨 — 컨텍스트 한도 초과)"


def truncate_context(
    system_guidelines: str,
    analysis_guidelines: str,
    knowledge_context: str,
    fixed_data_tokens: int,
    num_ctx: int,
) -> ContextResult:
    """
    우선순위 순으로 컨텍스트를 채우고, 예산 초과 시 낮은 우선순위부터 버린다.

    우선순위 (높음 → 낮음):
      1. system_guidelines   — 항상 최우선 포함
      2. analysis_guidelines
      3. knowledge_context

    Parameters
    ----------
    fixed_data_tokens : evidence / 로그 / 프롬프트 골격 등 변경 불가 데이터 토큰 수
    num_ctx           : 모델 컨텍스트 윈도우 크기 (토큰)

    Returns
    -------
    ContextResult — 처리된 (system_guidelines, analysis_guidelines, knowledge_context)
    """
    budget = max(0, num_ctx - OVERHEAD_TOKENS - fixed_data_tokens)

    # (원본 텍스트, 잘릴 때 뒤에 붙일 suffix) — 우선순위 높음 → 낮음 순
    segments: list[tuple[str, str]] = [
        (system_guidelines,   ""),
        (analysis_guidelines, _TRUNCATE_SUFFIX_ANALYSIS),
        (knowledge_context,   _TRUNCATE_SUFFIX_KNOWLEDGE),
    ]
    results = [text for text, _ in segments]
    was_truncated = False
    truncated_tokens = 0
    used = 0

    for i, (text, suffix) in enumerate(segments):
        tokens = estimate_tokens(text)
        remaining = budget - used
        if tokens > remaining:
            chars = max(0, remaining) * CHARS_PER_TOKEN
            truncated_tokens += tokens - max(0, remaining)
            results[i] = text[:chars] + suffix if suffix else text[:chars]
            # 낮은 우선순위 항목은 전부 버림
            for j in range(i + 1, len(results)):
                results[j] = ""
            was_truncated = True
            break
        used += tokens

    return ContextResult(results[0], results[1], results[2], was_truncated, truncated_tokens)


def split_knowledge_chunks(
    knowledge_context: str,
    system_guidelines: str,
    analysis_guidelines: str,
    fixed_data_tokens: int,
    num_ctx: int,
) -> list[str]:
    """
    knowledge_context 를 num_ctx 에 맞게 분할하여 청크 목록을 반환한다.
    분할이 불필요하면 원본 단일 청크를 반환한다.

    Parameters
    ----------
    fixed_data_tokens : evidence / 로그 등 고정 데이터 토큰 수

    Returns
    -------
    청크 문자열 목록. 빈 컨텍스트면 [""] 반환.
    """
    if not knowledge_context.strip():
        return [""]

    budget = max(0, num_ctx - OVERHEAD_TOKENS - fixed_data_tokens)
    sg_tokens = estimate_tokens(system_guidelines)
    ag_tokens = estimate_tokens(analysis_guidelines)
    kc_budget = max(0, budget - sg_tokens - ag_tokens)

    kc_tokens = estimate_tokens(knowledge_context)
    if kc_tokens <= kc_budget or kc_budget <= 0:
        return [knowledge_context]

    chunk_chars = max(1, kc_budget * CHARS_PER_TOKEN)
    chunks: list[str] = []
    start = 0
    while start < len(knowledge_context):
        chunks.append(knowledge_context[start: start + chunk_chars])
        start += chunk_chars
    return chunks


def calc_overflow_ratio(
    system_guidelines: str,
    analysis_guidelines: str,
    knowledge_context: str,
    fixed_data_tokens: int,
    num_ctx: int,
) -> float:
    """
    knowledge_context 에서 잘리는 비율을 계산한다.

    Returns
    -------
    0.0 = 잘림 없음, 1.0 = 전부 잘림
    """
    kc_tokens = estimate_tokens(knowledge_context)
    if kc_tokens == 0:
        return 0.0

    budget = max(0, num_ctx - OVERHEAD_TOKENS - fixed_data_tokens)
    sg_tokens = estimate_tokens(system_guidelines)
    ag_tokens = estimate_tokens(analysis_guidelines)
    kc_available = max(0, budget - sg_tokens - ag_tokens)

    if kc_available >= kc_tokens:
        return 0.0
    return (kc_tokens - kc_available) / kc_tokens
