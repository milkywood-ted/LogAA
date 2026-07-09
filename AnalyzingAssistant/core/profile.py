"""
core/profile.py

분석 프로파일 로드 및 병합 로직.

Profile:
  - JSON 파일 기반 (config/profiles/*.json), 프로파일 1개 = 파일 1개
  - 분석 지침 (analysis_guidelines): LLM system prompt에 주입
  - 사전정제 키워드 (prefilter_keywords): Stage 1 whitelist
  - 사전지식 참조 (knowledge_refs): domain_knowledge 항목의 이름 목록

MergedProfile:
  복수 프로파일 선택 시 병합 규칙:
  - 분석 지침: 프로파일 순서대로 연결
  - 사전정제 키워드: 합집합 (중복 제거, 순서 유지)
  - 사전지식 컨텍스트: 참조된 SQLite 사전지식 순서대로 연결 + ChromaDB 벡터 검색 결과

사전지식 참조:
  - JSON에는 이름(name)으로 저장 — DB 구조에 독립적
  - 파이프라인 실행 시 이름 → ID 변환하여 내부 처리
  - store_type='chromadb' 항목은 problem_text로 유사도 검색하여 knowledge_context에 추가
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import chromadb

from core.db import DB_PATH, get_conn

PROFILES_DIR             = Path(__file__).parent.parent / "config" / "profiles"
CHROMA_PATH              = Path(__file__).parent.parent / "chroma_db"
KNOWLEDGE_COLLECTION     = "knowledge"
KNOWLEDGE_SEARCH_TOP_K   = 3


# ── 데이터 클래스 ─────────────────────────────────────────────────────────────

@dataclass
class Profile:
    name: str
    description: str                    = ""
    analysis_guidelines: str            = ""
    prefilter_keywords: list[str]       = field(default_factory=list)
    knowledge_refs: list[str]           = field(default_factory=list)
    """사전지식 이름 목록 — JSON에 저장되는 형식. 내부 처리 시 ID로 변환."""


@dataclass
class DomainKnowledge:
    id: int
    name: str
    store_type: str          # 'sqlite' | 'chromadb'
    content: str
    description: str


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


# ── 파일명 헬퍼 ───────────────────────────────────────────────────────────────

def _name_to_stem(name: str) -> str:
    """프로파일 이름을 파일명 스템으로 변환한다. (공백→언더스코어, 위험 문자 제거)"""
    slug = name.strip().replace(" ", "_")
    slug = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", slug)
    return slug or "profile"


def _profile_path(name: str, profiles_dir: Path = PROFILES_DIR) -> Path:
    """프로파일 이름에 대응하는 JSON 파일 경로를 반환한다."""
    return profiles_dir / f"{_name_to_stem(name)}.json"


# ── 로드 헬퍼 ─────────────────────────────────────────────────────────────────

def load_profiles(profiles_dir: Path = PROFILES_DIR) -> list[Profile]:
    """profiles_dir 내 모든 .json 파일을 로드하여 이름순으로 반환한다."""
    profiles_dir.mkdir(parents=True, exist_ok=True)
    profiles = []
    for path in sorted(profiles_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            profiles.append(_dict_to_profile(data))
        except Exception:
            pass  # 손상된 파일은 무시
    return sorted(profiles, key=lambda p: p.name)


def load_profile(name: str, profiles_dir: Path = PROFILES_DIR) -> Profile | None:
    """이름으로 단일 프로파일을 로드한다."""
    path = _profile_path(name, profiles_dir)
    if not path.exists():
        # 파일명 스템과 이름이 다를 수 있으므로 전체 스캔으로 fallback
        for p in load_profiles(profiles_dir):
            if p.name == name:
                return p
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return _dict_to_profile(data)
    except Exception:
        return None


def save_profile(profile: Profile, profiles_dir: Path = PROFILES_DIR) -> Path:
    """프로파일을 JSON 파일로 저장한다. 반환값: 저장된 파일 경로."""
    profiles_dir.mkdir(parents=True, exist_ok=True)
    path = _profile_path(profile.name, profiles_dir)
    path.write_text(
        json.dumps(_profile_to_dict(profile), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def delete_profile(name: str, profiles_dir: Path = PROFILES_DIR) -> bool:
    """프로파일 JSON 파일을 삭제한다. 반환값: 삭제 성공 여부."""
    path = _profile_path(name, profiles_dir)
    if path.exists():
        path.unlink()
        return True
    # 스캔으로 fallback
    for path in profiles_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("name") == name:
                path.unlink()
                return True
        except Exception:
            pass
    return False


def rename_profile(old_name: str, new_profile: Profile, profiles_dir: Path = PROFILES_DIR) -> None:
    """프로파일 이름 변경 시 구 파일 삭제 후 새 파일로 저장한다."""
    delete_profile(old_name, profiles_dir)
    save_profile(new_profile, profiles_dir)


def _dict_to_profile(data: dict) -> Profile:
    return Profile(
        name                = data.get("name", ""),
        description         = data.get("description", ""),
        analysis_guidelines = data.get("analysis_guidelines", ""),
        prefilter_keywords  = data.get("prefilter_keywords", []),
        knowledge_refs      = data.get("knowledge_refs", []),
    )


def _profile_to_dict(profile: Profile) -> dict:
    return {
        "name":                profile.name,
        "description":         profile.description,
        "analysis_guidelines": profile.analysis_guidelines,
        "prefilter_keywords":  profile.prefilter_keywords,
        "knowledge_refs":      profile.knowledge_refs,
    }


# ── 사전지식 로드 ─────────────────────────────────────────────────────────────

def load_knowledge(db_path: Path = DB_PATH) -> list[DomainKnowledge]:
    """DB에서 모든 사전지식을 로드한다."""
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT id, name, store_type, content, description FROM domain_knowledge ORDER BY name"
        ).fetchall()
    return [
        DomainKnowledge(
            id          = r["id"],
            name        = r["name"],
            store_type  = r["store_type"],
            content     = r["content"],
            description = r["description"],
        )
        for r in rows
    ]


# ── 병합 ─────────────────────────────────────────────────────────────────────

def merge_profiles(
    profile_names: list[str],
    db_path: Path = DB_PATH,
    profiles_dir: Path = PROFILES_DIR,
) -> MergedProfile:
    """
    선택된 프로파일 이름 목록을 순서대로 병합한다.

    병합 규칙:
    - analysis_guidelines : 프로파일 순서대로 줄바꿈으로 연결
    - prefilter_keywords  : 합집합 (순서 유지, 중복 제거)
    - knowledge_refs      : 합집합 후 이름 → ID 변환하여 SQLite/ChromaDB 분리
    """
    if not profile_names:
        return MergedProfile(
            analysis_guidelines="",
            prefilter_keywords=[],
            knowledge_context="",
            source_profile_names=[],
        )

    profiles = [load_profile(n, profiles_dir) for n in profile_names]
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

    # 사전지식 이름 합집합 → ID 변환
    all_knowledge_names: list[str] = []
    seen_names: set[str] = set()
    for p in profiles:
        for ref in p.knowledge_refs:
            if ref not in seen_names:
                seen_names.add(ref)
                all_knowledge_names.append(ref)

    all_knowledge_ids = _resolve_knowledge_names_to_ids(all_knowledge_names, db_path)

    # SQLite / ChromaDB 분리
    sqlite_ids, chromadb_ids = _split_knowledge_ids_by_store(all_knowledge_ids, db_path)
    knowledge_context = _load_knowledge_context(sqlite_ids, db_path)

    return MergedProfile(
        analysis_guidelines    = merged_guidelines,
        prefilter_keywords     = merged_keywords,
        knowledge_context      = knowledge_context,
        chromadb_knowledge_ids = chromadb_ids,
        source_profile_names   = loaded_names,
    )


# ── 내부 헬퍼 ─────────────────────────────────────────────────────────────────

def _resolve_knowledge_names_to_ids(names: list[str], db_path: Path) -> list[int]:
    """사전지식 이름 목록을 DB에서 ID로 변환한다. 순서는 유지되지 않을 수 있음."""
    if not names:
        return []
    with get_conn(db_path) as conn:
        placeholders = ",".join("?" * len(names))
        rows = conn.execute(
            f"SELECT id, name FROM domain_knowledge WHERE name IN ({placeholders})",
            names,
        ).fetchall()
    # 입력 순서 유지
    name_to_id = {r["name"]: r["id"] for r in rows}
    return [name_to_id[n] for n in names if n in name_to_id]


def _split_knowledge_ids_by_store(
    knowledge_ids: list[int], db_path: Path
) -> tuple[list[int], list[int]]:
    """knowledge_ids를 store_type별로 분리한다. (sqlite_ids, chromadb_ids)"""
    if not knowledge_ids:
        return [], []
    with get_conn(db_path) as conn:
        placeholders = ",".join("?" * len(knowledge_ids))
        rows = conn.execute(
            f"SELECT id, store_type FROM domain_knowledge WHERE id IN ({placeholders})",
            knowledge_ids,
        ).fetchall()
    sqlite_ids   = [r["id"] for r in rows if r["store_type"] == "sqlite"]
    chromadb_ids = [r["id"] for r in rows if r["store_type"] == "chromadb"]
    return sqlite_ids, chromadb_ids


def _load_knowledge_context(knowledge_ids: list[int], db_path: Path) -> str:
    """SQLite store_type 사전지식 항목들을 텍스트로 조합한다."""
    if not knowledge_ids:
        return ""
    with get_conn(db_path) as conn:
        placeholders = ",".join("?" * len(knowledge_ids))
        rows = conn.execute(
            f"SELECT name, content FROM domain_knowledge "
            f"WHERE id IN ({placeholders}) AND store_type='sqlite' "
            f"ORDER BY name",
            knowledge_ids,
        ).fetchall()
    if not rows:
        return ""
    parts = []
    for r in rows:
        if r["content"].strip():
            parts.append(f"[{r['name']}]\n{r['content']}")
    return "\n\n".join(parts)


# ── ChromaDB 사전지식 ─────────────────────────────────────────────────────────

def search_knowledge_context(
    problem_text: str,
    knowledge_ids: list[int],
    chroma_path: Path = CHROMA_PATH,
    top_k: int = KNOWLEDGE_SEARCH_TOP_K,
) -> str:
    """
    ChromaDB store_type 사전지식 항목들을 problem_text로 유사도 검색하여 텍스트로 조합한다.
    """
    if not knowledge_ids or not problem_text.strip():
        return ""

    from core.llm import embed

    try:
        client = chromadb.PersistentClient(path=str(chroma_path))
        col    = client.get_or_create_collection(
            name     = KNOWLEDGE_COLLECTION,
            metadata = {"hnsw:space": "cosine"},
        )
        if col.count() == 0:
            return ""

        str_ids = [str(kid) for kid in knowledge_ids]
        existing = col.get(ids=str_ids, include=["documents", "metadatas"])
        if not existing["ids"]:
            return ""

        vec = embed([problem_text])[0]
        n   = min(top_k, len(existing["ids"]))
        results = col.query(
            query_embeddings = [vec],
            n_results        = n,
            where            = {"knowledge_id": {"$in": knowledge_ids}},
            include          = ["documents", "metadatas"],
        )

        parts: list[str] = []
        for i, doc in enumerate(results["documents"][0]):
            if doc and doc.strip():
                name = results["metadatas"][0][i].get("name", "")
                label = f"[{name}]" if name else "[사전지식]"
                parts.append(f"{label}\n{doc}")
        return "\n\n".join(parts)

    except Exception:
        return ""


def add_knowledge_to_chromadb(
    knowledge_id: int,
    name: str,
    content: str,
    chroma_path: Path = CHROMA_PATH,
) -> None:
    """ChromaDB 사전지식 컬렉션에 항목을 추가(또는 갱신)한다."""
    if not content.strip():
        return
    from core.llm import embed
    vec = embed([content])[0]
    client = chromadb.PersistentClient(path=str(chroma_path))
    col    = client.get_or_create_collection(
        name     = KNOWLEDGE_COLLECTION,
        metadata = {"hnsw:space": "cosine"},
    )
    col.upsert(
        ids        = [str(knowledge_id)],
        embeddings = [vec],
        documents  = [content],
        metadatas  = [{"knowledge_id": knowledge_id, "name": name}],
    )


def remove_knowledge_from_chromadb(
    knowledge_id: int,
    chroma_path: Path = CHROMA_PATH,
) -> None:
    """ChromaDB 사전지식 컬렉션에서 항목을 제거한다."""
    try:
        client = chromadb.PersistentClient(path=str(chroma_path))
        col    = client.get_or_create_collection(
            name     = KNOWLEDGE_COLLECTION,
            metadata = {"hnsw:space": "cosine"},
        )
        col.delete(ids=[str(knowledge_id)])
    except Exception:
        pass
