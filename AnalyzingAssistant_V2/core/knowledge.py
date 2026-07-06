"""
core/knowledge.py

도메인 사전지식(domain_knowledge) 로드·검색·CRUD 및 ChromaDB 동기화 로직.

DomainKnowledge:
  - SQLite 'domain_knowledge' 테이블에 저장 (id, name, store_type, content, description)
  - store_type='sqlite'   : content 를 그대로 LLM 컨텍스트에 주입
  - store_type='chromadb' : content 를 ChromaDB 에 임베딩, problem_text 로 유사도 검색

CRUD:
  - create/update/delete_knowledge 는 SQLite 변경과 ChromaDB 동기화를 함께 수행한다.
    (store_type 분기: chromadb 면 임베딩 upsert, sqlite 면 ChromaDB 항목 제거)

병합(merge_profiles)에서 사용하는 이름→ID 변환·store 분리·SQLite 컨텍스트 조합
헬퍼도 본 모듈에 둔다. core/profile.py 는 본 모듈을 import 하여 사용한다.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import chromadb

from core.db import DB_PATH, get_conn

logger = logging.getLogger(__name__)
CHROMA_PATH              = Path(__file__).parent.parent / "chroma_db"
KNOWLEDGE_COLLECTION     = "knowledge"
KNOWLEDGE_SEARCH_TOP_K   = 3


# ── 데이터 클래스 ─────────────────────────────────────────────────────────────

@dataclass
class DomainKnowledge:
    id: int
    name: str
    store_type: str          # 'sqlite' | 'chromadb'
    content: str
    description: str
    category: str = ""
    sub_category: str = ""
    chip_tags: list[str] = field(default_factory=list)


# ── 사전지식 로드 ─────────────────────────────────────────────────────────────

def load_knowledge(db_path: Path = DB_PATH) -> list[DomainKnowledge]:
    """DB에서 모든 사전지식을 로드한다."""
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT id, name, store_type, content, description, category, sub_category, chip_tags"
            " FROM domain_knowledge ORDER BY name"
        ).fetchall()
    return [
        DomainKnowledge(
            id           = r["id"],
            name         = r["name"],
            store_type   = r["store_type"],
            content      = r["content"],
            description  = r["description"],
            category     = r["category"] or "",
            sub_category = r["sub_category"] or "",
            chip_tags    = json.loads(r["chip_tags"] or "[]"),
        )
        for r in rows
    ]


def load_one_knowledge(kid: int, db_path: Path = DB_PATH) -> DomainKnowledge | None:
    """ID로 단일 사전지식을 로드한다. 없으면 None."""
    with get_conn(db_path) as conn:
        r = conn.execute(
            "SELECT id, name, store_type, content, description, category, sub_category, chip_tags"
            " FROM domain_knowledge WHERE id=?",
            (kid,),
        ).fetchone()
    if r is None:
        return None
    return DomainKnowledge(
        id           = r["id"],
        name         = r["name"],
        store_type   = r["store_type"],
        content      = r["content"],
        description  = r["description"],
        category     = r["category"] or "",
        sub_category = r["sub_category"] or "",
        chip_tags    = json.loads(r["chip_tags"] or "[]"),
    )


# ── 사전지식 CRUD (SQLite + ChromaDB 동기화) ──────────────────────────────────

def create_knowledge(
    name: str,
    store_type: str,
    content: str,
    description: str,
    category: str = "",
    sub_category: str = "",
    chip_tags: list[str] | None = None,
    db_path: Path = DB_PATH,
) -> int:
    """
    사전지식을 생성한다.

    SQLite INSERT 후, store_type='chromadb' 이고 content 가 비어있지 않으면
    ChromaDB 에 임베딩한다. 반환값: 새 항목의 id.
    """
    chip_tags_json = json.dumps(chip_tags or [])
    with get_conn(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO domain_knowledge"
            " (name, store_type, content, description, category, sub_category, chip_tags)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, store_type, content, description, category, sub_category, chip_tags_json),
        )
        kid = cur.lastrowid
    if store_type == "chromadb" and content.strip():
        add_knowledge_to_chromadb(kid, name, content.strip())
    return kid


def update_knowledge(
    kid: int,
    name: str,
    store_type: str,
    content: str,
    description: str,
    category: str = "",
    sub_category: str = "",
    chip_tags: list[str] | None = None,
    db_path: Path = DB_PATH,
) -> None:
    """
    사전지식을 수정한다.

    SQLite UPDATE 후 store_type 에 따라 ChromaDB 를 동기화한다.
    - chromadb 이고 content 가 있으면 임베딩 갱신(upsert)
    - sqlite 이면 ChromaDB 항목 제거
    """
    chip_tags_json = json.dumps(chip_tags or [])
    with get_conn(db_path) as conn:
        conn.execute(
            "UPDATE domain_knowledge"
            " SET name=?, store_type=?, content=?, description=?,"
            " category=?, sub_category=?, chip_tags=?, updated_at=datetime('now')"
            " WHERE id=?",
            (name, store_type, content, description, category, sub_category, chip_tags_json, kid),
        )
    if store_type == "chromadb" and content.strip():
        add_knowledge_to_chromadb(kid, name, content.strip())
    elif store_type == "sqlite":
        remove_knowledge_from_chromadb(kid)


def delete_knowledge(kid: int, db_path: Path = DB_PATH) -> None:
    """사전지식을 삭제한다 (SQLite DELETE + ChromaDB 항목 제거)."""
    with get_conn(db_path) as conn:
        conn.execute("DELETE FROM domain_knowledge WHERE id=?", (kid,))
    remove_knowledge_from_chromadb(kid)


# ── 이름→ID 변환 / store 분리 / SQLite 컨텍스트 (merge_profiles 용) ────────────

def resolve_knowledge_names_to_ids(names: list[str], db_path: Path = DB_PATH) -> list[int]:
    """사전지식 이름 목록을 DB에서 ID로 변환한다. 입력 `names` 순서를 유지하며, DB 에 없는 이름은 결과에서 제외된다."""
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


def get_all_chromadb_knowledge_ids(db_path: Path) -> list[int]:
    """domain_knowledge 테이블에서 store_type='chromadb'인 항목의 ID 전체를 반환한다."""
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT id FROM domain_knowledge WHERE store_type='chromadb' ORDER BY id"
        ).fetchall()
    return [r["id"] for r in rows]


def filter_knowledge_ids(
    knowledge_ids: list[int],
    categories: list[str],
    chip,
    db_path: Path = DB_PATH,
) -> list[int]:
    """category와 chip 기준으로 지식 ID를 필터링한다.

    규칙:
    - category: categories가 비면 전부 통과. 아니면 지식.category가 categories에 속하거나
      지식.category == "" (공통) 이면 통과.
    - chip: chip_filter와 동일 철학 — 지식.chip_tags가 비면 공통(통과), defect chip이
      비면 전체 통과, 아니면 교집합 있을 때만 통과.
    - 두 조건 AND.
    """
    if not knowledge_ids:
        return []

    from core.chip_filter import _as_str_list

    placeholders = ",".join("?" * len(knowledge_ids))
    with get_conn(db_path) as conn:
        rows = conn.execute(
            f"SELECT id, category, chip_tags FROM domain_knowledge WHERE id IN ({placeholders})",
            knowledge_ids,
        ).fetchall()

    chip_set = {c.lower() for c in _as_str_list(chip)}
    cat_set  = set(categories)
    filter_by_category = bool(cat_set)
    filter_by_chip     = bool(chip_set)

    result: list[int] = []
    for r in rows:
        # category 필터
        if filter_by_category:
            k_cat = r["category"] or ""
            if k_cat != "" and k_cat not in cat_set:
                continue

        # chip 필터
        if filter_by_chip:
            tags = {t.lower() for t in _as_str_list(r["chip_tags"] or "[]")}
            if tags and not (chip_set & tags):
                continue

        result.append(r["id"])
    return result


def split_knowledge_ids_by_store(
    knowledge_ids: list[int], db_path: Path = DB_PATH
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


def load_knowledge_context(knowledge_ids: list[int], db_path: Path = DB_PATH) -> str:
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


def score_profile_relevance(
    problem_vec: list[float],
    chromadb_knowledge_ids: list[int],
    chroma_path: Path = CHROMA_PATH,
) -> float | None:
    """프로파일 연결 ChromaDB 지식과 problem 의 유사도 대표값.

    chromadb_knowledge_ids 가 비면 None (sqlite-only 프로파일).
    가장 관련 높은 지식의 유사도(1 - cosine_distance)를 반환한다.
    problem_vec 는 호출부에서 1회 계산 후 전달 — 내부에서 embed 를 호출하지 않는다.
    """
    if not chromadb_knowledge_ids:
        return None
    try:
        client = chromadb.PersistentClient(path=str(chroma_path))
        col    = client.get_or_create_collection(
            name     = KNOWLEDGE_COLLECTION,
            metadata = {"hnsw:space": "cosine"},
        )
        str_ids  = [str(kid) for kid in chromadb_knowledge_ids]
        existing = col.get(ids=str_ids, include=[])
        if not existing["ids"]:
            return None
        results = col.query(
            query_embeddings = [problem_vec],
            n_results        = 1,
            where            = {"knowledge_id": {"$in": chromadb_knowledge_ids}},
            include          = ["distances"],
        )
        distances = results["distances"][0]
        if not distances:
            return None
        return 1.0 - distances[0]
    except Exception:
        return None


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
