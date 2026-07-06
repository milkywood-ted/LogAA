"""
core/profile.py

분석 프로파일 로드 및 병합 로직.

Profile:
  - JSON 파일 기반 (config/profiles/*.json), 프로파일 1개 = 파일 1개
  - 분석 지침 (analysis_guidelines): LLM system prompt에 주입
  - 사전정제 키워드 (prefilter_keywords): Stage 1 whitelist
  - 사전지식 참조 (knowledge_refs): domain_knowledge 항목의 고유 ID 목록

MergedProfile:
  복수 프로파일 선택 시 병합 규칙:
  - 분석 지침: 프로파일 순서대로 연결
  - 사전정제 키워드: 합집합 (중복 제거, 순서 유지)
  - 사전지식 컨텍스트: 참조된 SQLite 사전지식 순서대로 연결 + ChromaDB 벡터 검색 결과

사전지식 참조:
  - JSON에는 고유 ID(int)로 저장 — 이름 변경 시에도 참조 유지
  - store_type='chromadb' 항목은 problem_text로 유사도 검색하여 knowledge_context에 추가
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from core.config import analysis_profile_config
from core.db import DB_PATH
from core.knowledge import (
    filter_knowledge_ids,
    load_knowledge_context,
    split_knowledge_ids_by_store,
)

logger = logging.getLogger(__name__)


# ── 데이터 클래스 ─────────────────────────────────────────────────────────────

@dataclass
class Profile:
    name: str
    description: str                    = ""
    analysis_guidelines: str            = ""
    prefilter_keywords: list[str]       = field(default_factory=list)
    knowledge_refs: list[int]           = field(default_factory=list)
    """사전지식 고유 ID 목록 — JSON에 저장되는 형식."""
    category: str                       = ""
    """이 프로파일이 다루는 도메인 카테고리. 기본값 ""(무분류)."""


@dataclass
class MergedProfile:
    """복수 프로파일 병합 결과 — 파이프라인에 직접 전달된다."""
    analysis_guidelines: str        # 프로파일 순서대로 연결
    prefilter_keywords: list[str]   # 합집합 (중복 제거, 순서 유지)
    knowledge_context: str          # SQLite 사전지식 조합 텍스트
    chromadb_knowledge_ids: list[int] = field(default_factory=list)
    """ChromaDB 타입 사전지식 ID 목록 — 파이프라인에서 problem_text로 검색하여 enrichment."""
    source_profile_names: list[str] = field(default_factory=list)
    """병합에 사용된 원본 프로파일 이름 목록 — Stage 2 케이스 필터링에 사용."""


# ── CRUD (profiles_config 에 위임) ────────────────────────────────────────────

def load_profiles() -> list[Profile]:
    """모든 프로파일을 로드하여 이름순으로 반환한다."""
    result: list[Profile] = []
    for data in analysis_profile_config.load_all():
        try:
            result.append(_dict_to_profile(data))
        except (ValueError, KeyError) as e:
            logger.warning("프로파일 변환 실패: %s", e)
    return sorted(result, key=lambda p: p.name)


def load_profile(name: str) -> Profile | None:
    """이름으로 단일 프로파일을 로드한다."""
    data = analysis_profile_config.load_one(name)
    if data is None:
        return None
    try:
        return _dict_to_profile(data)
    except (ValueError, KeyError) as e:
        logger.warning("프로파일 변환 실패 (%s): %s", name, e)
        return None


def save_profile(profile: Profile) -> Path:
    """프로파일을 JSON 파일로 저장한다. 반환값: 저장된 파일 경로."""
    return analysis_profile_config.save(profile.name, _profile_to_dict(profile))


def delete_profile(name: str) -> bool:
    """프로파일 JSON 파일을 삭제한다. 반환값: 삭제 성공 여부."""
    return analysis_profile_config.delete(name)


def rename_profile(old_name: str, new_profile: Profile) -> None:
    """프로파일 이름 변경 시 구 파일 삭제 후 새 파일로 저장한다."""
    analysis_profile_config.delete(old_name)
    analysis_profile_config.save(new_profile.name, _profile_to_dict(new_profile))


def _dict_to_profile(data: dict) -> Profile:
    return Profile(
        name                = data.get("name", ""),
        description         = data.get("description", ""),
        analysis_guidelines = data.get("analysis_guidelines", ""),
        prefilter_keywords  = data.get("prefilter_keywords", []),
        knowledge_refs      = [int(r) for r in data.get("knowledge_refs", [])],
        category            = data.get("category", ""),
    )


def _profile_to_dict(profile: Profile) -> dict:
    return {
        "name":                profile.name,
        "description":         profile.description,
        "analysis_guidelines": profile.analysis_guidelines,
        "prefilter_keywords":  profile.prefilter_keywords,
        "knowledge_refs":      profile.knowledge_refs,
        "category":            profile.category,
    }


# ── 병합 ─────────────────────────────────────────────────────────────────────

def merge_profiles(
    profile_names: list[str],
    db_path: Path = DB_PATH,
    chip=None,
) -> MergedProfile:
    """
    선택된 프로파일 이름 목록을 순서대로 병합한다.

    병합 규칙:
    - analysis_guidelines : 프로파일 순서대로 줄바꿈으로 연결
    - prefilter_keywords  : 합집합 (순서 유지, 중복 제거)
    - knowledge_refs      : ID 합집합 → category/chip으로 필터 → SQLite/ChromaDB 분리

    Parameters
    ----------
    chip : defect의 칩 정보 (list[str] / str / None). 전달 시 chip_tags 기준으로
           지식을 필터링한다. None이면 필터 없음.
    """
    if not profile_names:
        return MergedProfile(
            analysis_guidelines="",
            prefilter_keywords=[],
            knowledge_context="",
            source_profile_names=[],
        )

    profiles = [load_profile(n) for n in profile_names]
    profiles = [p for p in profiles if p is not None]
    loaded_names = [p.name for p in profiles]

    # 분석 지침 연결
    guidelines_parts = [p.analysis_guidelines for p in profiles if p.analysis_guidelines.strip()]
    merged_guidelines = "\n\n".join(guidelines_parts)

    # 사전정제 키워드 합집합 (순서 유지, 중복 제거)
    seen_kw: set[str] = set()
    merged_keywords: list[str] = []
    for p in profiles:
        for kw in p.prefilter_keywords:
            if kw not in seen_kw:
                seen_kw.add(kw)
                merged_keywords.append(kw)

    # 사전지식 ID 합집합 (순서 유지, 중복 제거)
    seen_ids: set[int] = set()
    all_knowledge_ids: list[int] = []
    for p in profiles:
        for ref_id in p.knowledge_refs:
            if ref_id not in seen_ids:
                seen_ids.add(ref_id)
                all_knowledge_ids.append(ref_id)

    # category 합집합 (빈 문자열 제외)
    categories = list({p.category for p in profiles if p.category})

    # category / chip 기준 ID 필터링
    filtered_ids = filter_knowledge_ids(all_knowledge_ids, categories, chip, db_path)

    # SQLite / ChromaDB 분리
    sqlite_ids, chromadb_ids = split_knowledge_ids_by_store(filtered_ids, db_path)
    knowledge_context = load_knowledge_context(sqlite_ids, db_path)

    return MergedProfile(
        analysis_guidelines    = merged_guidelines,
        prefilter_keywords     = merged_keywords,
        knowledge_context      = knowledge_context,
        chromadb_knowledge_ids = chromadb_ids,
        source_profile_names   = loaded_names,
    )
