"""
core/kb_search.py

Stage 2 — KB Search

Step A: embed() → ChromaDB Top-K 후보 검색
Step B: chat() Reranker → relevance_score ≥ threshold → HIT

Output: MatchedCase (HIT) | None (MISS)

MatchedCase.patterns 에 케이스에 연결된 패턴 목록이 포함되어
Stage 3, Stage 4 에서 바로 사용할 수 있다.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

import chromadb

from core.db import DB_PATH, get_conn
from core.config import cfg
from core.llm import chat, embed

# ── 기본값 ────────────────────────────────────────────────────────────────────

CHROMA_PATH   = Path(__file__).parent.parent / "chroma_db"
COLLECTION    = "cases"
DEFAULT_TOP_K = 5


def _parse_json_list(raw: str | None) -> list:
    """JSON 배열 필드를 안전하게 파싱한다. 실패·빈 값이면 [] 반환."""
    if not raw:
        return []
    try:
        return json.loads(raw)
    except Exception:
        return []


# ── 결과 타입 ─────────────────────────────────────────────────────────────────

@dataclass
class MatchedCase:
    """Stage 2 HIT 결과 — Stage 3/4 에 그대로 전달된다."""
    case_id: int
    name: str
    description: str
    keywords: list[str]
    relevance_score: float
    patterns: list[dict] = field(default_factory=list)
    """케이스에 연결된 패턴 목록 (steps / components 포함)."""
    profile_refs: list[str] = field(default_factory=list)
    """케이스에 연결된 분석 프로파일 이름 목록 — 자동 병합 대상."""
    pinned: bool = False
    """사용자가 직접 지정한 케이스 여부 (True 면 Stage 2 자동 검색을 건너뛴 결과)."""


# ── KBSearch ──────────────────────────────────────────────────────────────────

class KBSearch:
    """
    Streamlit 에서 st.cache_resource 로 싱글턴으로 사용한다.

        @st.cache_resource
        def get_kb_search():
            return KBSearch()
    """

    def __init__(
        self,
        llm_model: str | None = None,
        chroma_path: Path     = CHROMA_PATH,
        db_path: Path         = DB_PATH,
        top_k: int            = DEFAULT_TOP_K,
        threshold: float | None = None,
    ) -> None:
        # None 이면 호출 시점의 cfg 값 사용 (import 시 고정 방지)
        self._llm_model = llm_model if llm_model is not None else cfg.llm_model
        self.db_path    = db_path
        self.top_k      = top_k
        self.threshold  = threshold if threshold is not None else cfg.kb_threshold

        # ChromaDB persistent
        self._client = chromadb.PersistentClient(path=str(chroma_path))
        self._col    = self._client.get_or_create_collection(
            name     = COLLECTION,
            metadata = {"hnsw:space": "cosine"},
        )

    # ── 메인 API ──────────────────────────────────────────────────────────────

    def search(
        self,
        problem_text: str,
        knowledge_context: str = "",
        system_analysis_guidelines: str = "",
        selected_profile_names: list[str] | None = None,
    ) -> MatchedCase | None:
        """
        주어진 문제 설명으로 KB 를 검색한다.

        Parameters
        ----------
        problem_text               : 사용자가 입력한 문제 설명
        knowledge_context          : 프로파일 사전지식 컨텍스트 (Reranker 프롬프트에 주입)
        system_analysis_guidelines : 시스템 분석 지침 (Reranker 프롬프트 최상단에 주입)
        selected_profile_names     : 사용자가 선택한 프로파일 이름 목록.
                                     비어있거나 None이면 모든 케이스 검색.
                                     값이 있으면 profile_refs가 교집합인 케이스 +
                                     profile_refs가 비어있는 케이스만 검색.

        Returns
        -------
        MatchedCase  : relevance_score ≥ threshold 인 케이스가 있을 때 (HIT)
        None         : 후보 없음 또는 threshold 미달 (MISS)
        """
        allowed_ids = self._get_allowed_case_ids(selected_profile_names or [])
        if allowed_ids is not None and not allowed_ids:
            return None   # 필터링 결과 후보 없음
        candidates = self._vector_search(problem_text, allowed_ids)
        if not candidates:
            return None
        return self._rerank(problem_text, candidates, knowledge_context, system_analysis_guidelines)

    def _get_allowed_case_ids(self, selected_profile_names: list[str]) -> list[int] | None:
        """
        선택된 프로파일 이름에 해당하는 케이스 ID 목록을 반환한다.

        Returns
        -------
        None       : 필터링 없음 (selected_profile_names가 비어있음 → 모든 케이스)
        list[int]  : 허용된 case_id 목록 (빈 리스트면 후보 없음)

        필터 규칙:
          - 케이스의 profile_refs가 비어있으면 항상 포함
          - 케이스의 profile_refs와 selected_profile_names의 교집합이 있으면 포함
        """
        if not selected_profile_names:
            return None

        selected_set = set(selected_profile_names)
        with get_conn(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, profile_refs FROM cases"
            ).fetchall()

        allowed: list[int] = []
        for row in rows:
            refs = _parse_json_list(row["profile_refs"])
            if not refs or selected_set & set(refs):
                allowed.append(row["id"])
        return allowed

    # ── 케이스 직접 로드 (사용자 지정 경로 — Stage 2 우회) ────────────────────

    def load_case_by_name(self, name: str) -> MatchedCase | None:
        """
        케이스 이름으로 MatchedCase 를 직접 로드한다.

        벡터 검색·Reranker 를 거치지 않고 사용자가 지정한 케이스를 그대로
        Stage 3/4 로 전달할 때 사용한다. pinned=True 로 표시되며
        relevance_score 는 1.0 으로 고정한다.
        """
        with get_conn(self.db_path) as conn:
            row = conn.execute(
                "SELECT id, name, description, keywords FROM cases WHERE name = ?",
                (name,),
            ).fetchone()
        if row is None:
            return None

        case_id      = int(row["id"])
        patterns     = self._load_patterns(case_id)
        profile_refs = self._load_case_profile_refs(case_id)

        return MatchedCase(
            case_id         = case_id,
            name            = row["name"],
            description     = row["description"] or "",
            keywords        = _parse_json_list(row["keywords"]),
            relevance_score = 1.0,
            patterns        = patterns,
            profile_refs    = profile_refs,
            pinned          = True,
        )

    def list_case_names(self) -> list[str]:
        """등록된 케이스 이름 전체 (정렬됨). 사용자 지정 UI 용."""
        with get_conn(self.db_path) as conn:
            rows = conn.execute(
                "SELECT name FROM cases ORDER BY name"
            ).fetchall()
        return [r["name"] for r in rows]

    # ── ChromaDB CRUD (KB 관리 페이지에서 호출) ───────────────────────────────

    def add_case(
        self,
        case_id: int,
        name: str,
        description: str,
        keywords: list[str],
    ) -> None:
        """케이스를 ChromaDB 에 추가(또는 갱신)한다."""
        vec = embed([description])[0]
        self._col.upsert(
            ids        = [str(case_id)],
            embeddings = [vec],
            documents  = [description],
            metadatas  = [{
                "case_id":  case_id,
                "name":     name,
                "keywords": json.dumps(keywords, ensure_ascii=False),
            }],
        )

    def remove_case(self, case_id: int) -> None:
        """케이스를 ChromaDB 에서 제거한다."""
        self._col.delete(ids=[str(case_id)])

    def sync_from_db(self) -> int:
        """
        SQLite cases 테이블 전체를 ChromaDB 에 동기화한다.

        Returns: 동기화된 케이스 수
        """
        with get_conn(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, name, description, keywords FROM cases"
            ).fetchall()

        for row in rows:
            self.add_case(
                case_id     = row["id"],
                name        = row["name"],
                description = row["description"],
                keywords    = json.loads(row["keywords"]),
            )
        return len(rows)

    # ── Step A: 벡터 검색 ─────────────────────────────────────────────────────

    def _vector_search(
        self,
        query: str,
        allowed_case_ids: list[int] | None = None,
    ) -> list[dict]:
        """embed() → ChromaDB cosine 유사도 Top-K 검색.

        allowed_case_ids가 주어지면 해당 case_id만 검색 대상으로 제한한다.
        None이면 모든 케이스를 검색한다.
        """
        total = self._col.count()
        if total == 0:
            return []

        vec = embed([query])[0]
        query_kwargs: dict = {
            "query_embeddings": [vec],
            "n_results":        min(self.top_k, total),
            "include":          ["documents", "metadatas", "distances"],
        }
        if allowed_case_ids is not None:
            query_kwargs["where"] = {"case_id": {"$in": allowed_case_ids}}
        results = self._col.query(**query_kwargs)

        candidates: list[dict] = []
        for i, _ in enumerate(results["ids"][0]):
            meta = results["metadatas"][0][i]
            candidates.append({
                "case_id":     int(meta["case_id"]),
                "name":        meta["name"],
                "description": results["documents"][0][i],
                "keywords":    json.loads(meta["keywords"]),
                "distance":    results["distances"][0][i],
            })
        return candidates

    # ── Step B: LLM Reranker ──────────────────────────────────────────────────

    def _rerank(
        self,
        problem_text: str,
        candidates: list[dict],
        knowledge_context: str = "",
        system_analysis_guidelines: str = "",
    ) -> MatchedCase | None:
        """
        LLM 으로 후보 전체를 한 번에 평가한다.
        threshold 이상 중 최고 점수 케이스를 반환한다.
        """
        prompt = _build_rerank_prompt(
            problem_text, candidates, knowledge_context, system_analysis_guidelines
        )

        try:
            content = chat(
                messages    = [{"role": "user", "content": prompt}],
                model       = self._llm_model,
                json_mode   = True,
                temperature = 0.0,
            )
            data   = json.loads(content)
            scores = data.get("scores", [])
        except Exception:
            return None

        best_idx:   int   = -1
        best_score: float = -1.0

        for s in scores:
            idx   = int(s.get("index", 0)) - 1       # 1-based → 0-based
            score = float(s.get("relevance_score", 0.0))
            if score >= self.threshold and score > best_score:
                best_idx   = idx
                best_score = score

        if best_idx < 0:
            return None

        candidate    = candidates[best_idx]
        patterns     = self._load_patterns(candidate["case_id"])
        profile_refs = self._load_case_profile_refs(candidate["case_id"])

        return MatchedCase(
            case_id         = candidate["case_id"],
            name            = candidate["name"],
            description     = candidate["description"],
            keywords        = candidate["keywords"],
            relevance_score = best_score,
            patterns        = patterns,
            profile_refs    = profile_refs,
        )

    # ── 패턴 로드 (Stage 3/4 용) ──────────────────────────────────────────────

    def _load_patterns(self, case_id: int) -> list[dict]:
        """케이스에 연결된 패턴을 SQLite 에서 로드한다."""
        with get_conn(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT p.*
                FROM patterns p
                JOIN case_patterns cp ON cp.pattern_id = p.id
                WHERE cp.case_id = ?
                ORDER BY p.id
                """,
                (case_id,),
            ).fetchall()

            result: list[dict] = []
            for row in rows:
                d = dict(row)
                d["keywords"] = json.loads(d["keywords"])
                pid = d["id"]

                if d["type"] == "SEQUENCE":
                    d["steps"] = [
                        s["pattern"]
                        for s in conn.execute(
                            "SELECT pattern FROM pattern_steps "
                            "WHERE pattern_id = ? ORDER BY step_order",
                            (pid,),
                        ).fetchall()
                    ]

                if d["type"] == "COMPOSITE":
                    d["components"] = [
                        c["name"]
                        for c in conn.execute(
                            """
                            SELECT p2.name
                            FROM pattern_components pc
                            JOIN patterns p2 ON pc.ref_pattern_id = p2.id
                            WHERE pc.pattern_id = ?
                            ORDER BY pc.component_order
                            """,
                            (pid,),
                        ).fetchall()
                    ]

                result.append(d)

        return result

    def _load_case_profile_refs(self, case_id: int) -> list[str]:
        """케이스에 연결된 분석 프로파일 이름 목록을 SQLite 에서 로드한다."""
        with get_conn(self.db_path) as conn:
            row = conn.execute(
                "SELECT profile_refs FROM cases WHERE id=?", (case_id,)
            ).fetchone()
        return _parse_json_list(row["profile_refs"]) if row else []


# ── 프롬프트 ──────────────────────────────────────────────────────────────────

def _build_rerank_prompt(
    problem_text: str,
    candidates: list[dict],
    knowledge_context: str = "",
    system_analysis_guidelines: str = "",
) -> str:
    candidate_block = "\n\n".join(
        f"[{i + 1}]\n이름: {c['name']}\n설명: {c['description']}"
        for i, c in enumerate(candidates)
    )
    system_section   = f"\n━━━ 시스템 분석 지침 ━━━\n{system_analysis_guidelines}\n" if system_analysis_guidelines.strip() else ""
    knowledge_section = f"\n━━━ 사전지식 ━━━\n{knowledge_context}\n" if knowledge_context.strip() else ""

    return f"""/no_think
주어진 문제 상황과 각 KB 케이스가 동일하거나 유사한 문제인지 평가하세요.
{system_section}{knowledge_section}
━━━ 문제 상황 ━━━
{problem_text}

━━━ KB 후보 케이스 ━━━
{candidate_block}

━━━ 출력 형식 (JSON 만 출력) ━━━
{{
  "scores": [
    {{
      "index": 1,
      "relevance_score": 0.0~1.0,
      "reason": "판단 근거"
    }}
  ]
}}

평가 기준:
- 1.0: 동일한 문제
- 0.7~0.9: 유사한 문제 (같은 컴포넌트, 유사한 증상)
- 0.4~0.6: 부분적으로 관련
- 0.0~0.3: 관련 없음
"""
