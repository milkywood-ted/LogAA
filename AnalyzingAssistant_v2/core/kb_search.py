"""
core/kb_search.py

Stage 2 — KB Search

Step A: embed() → ChromaDB Top-K 후보 검색
Step B: chat_with_profile() Reranker → relevance_score ≥ threshold → HIT

Output: MatchedCase (HIT) | None (MISS)

MatchedCase.patterns 에 케이스에 연결된 패턴 목록이 포함되어
Stage 3, Stage 4 에서 바로 사용할 수 있다.
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import chromadb

from core.db import DB_PATH, get_conn
import core.config as config
from core.chip_filter import chip_matches, filter_patterns_by_chip
from core.llm import RerankError, chat_with_profile, embed
from core.llm import rerank as llm_rerank

logger = logging.getLogger(__name__)


class KBRerankerError(Exception):
    """Reranker LLM 호출이 재시도 후에도 실패했을 때 발생한다."""

# ── 기본값 ────────────────────────────────────────────────────────────────────

CHROMA_PATH          = Path(__file__).parent.parent / "chroma_db"
# 케이스 description 임베딩 컬렉션 — Stage 2A 벡터 검색의 1차 인덱스.
COLLECTION           = "cases"
# 케이스 analysis(분석/원인/해결) 본문을 별도 임베딩한 컬렉션.
# description 만으로 매칭이 약한 경우(증상은 다르지만 원인이 같은 케이스)를 보강하기 위해
# 동일 query 로 두 컬렉션을 모두 검색하고 더 가까운 distance 를 대표값으로 채택한다.
# (구현: KBSearch._vector_search 의 desc_results / analysis_results 합산 로직 참조)
COLLECTION_ANALYSIS  = "cases_analysis"
DEFAULT_TOP_K        = 5


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
    chip_tags: list[str] = field(default_factory=list)
    """케이스가 해당하는 칩 목록 — 유사 케이스 검색 시 순위 가중치에 사용."""
    references: list[dict] = field(default_factory=list)
    """외부 문제 관리 시스템 참조 목록 — [{"system": "Jira", "reference_id": "DTV-1234"}, ...]"""
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
        max_candidates: int   = 3,
    ) -> None:
        # reranker_llm 이 설정되어 있으면 active_llm 및 llm_model 오버라이드와 무관하게
        # 그 프로필(provider/base_url/api_key/model 전체)로 독립 동작한다.
        # 설정이 없으면 기존과 동일하게 active_llm(+ llm_model 오버라이드)을 사용한다.
        reranker_profile = config.reranker_llm()
        if reranker_profile is not None:
            self._llm_profile = reranker_profile
        else:
            active = config.active_llm()
            active_model = llm_model if llm_model is not None else active.get("model", "")
            self._llm_profile = {**active, "model": active_model}
        self._llm_fallback_profile = config.reranker_fallback_llm()
        self.db_path        = db_path
        self.top_k          = top_k
        self.threshold      = threshold if threshold is not None else config.get_float("pipeline.kb_threshold", 0.70)
        self.max_candidates = max_candidates
        # rerank 엔드포인트 전용 threshold — cross-encoder 점수 분포는 LLM 의
        # 0~1 관련성 점수와 스케일이 다르므로 kb_threshold 와 별도로 둔다.
        # TODO(reranker 엔드포인트 구현 설계 §6 D-d): 실제 vLLM 으로 대표 케이스를
        # 채점해 분포를 실측하기 전까지는 kb_threshold 와 동일값을 잠정 기본값으로 쓴다.
        self.rerank_threshold = config.get_float("pipeline.rerank_threshold", self.threshold)

        # ChromaDB persistent
        self._client   = chromadb.PersistentClient(path=str(chroma_path))
        self._col      = self._client.get_or_create_collection(
            name     = COLLECTION,
            metadata = {"hnsw:space": "cosine"},
        )
        self._col_analysis = self._client.get_or_create_collection(
            name     = COLLECTION_ANALYSIS,
            metadata = {"hnsw:space": "cosine"},
        )

    # ── 메인 API ──────────────────────────────────────────────────────────────

    def search(
        self,
        problem_text: str,
        knowledge_context: str = "",
        system_analysis_guidelines: str = "",
        selected_profile_names: list[str] | None = None,
        chip: list[str] | str | None = None,
        problem_vec: list[float] | None = None,
    ) -> list[MatchedCase]:
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
        chip                       : defect 의 칩 정보. 매칭된 케이스의 패턴을
                                     chip_tags 기준으로 필터링한다. 비어있으면
                                     필터링하지 않는다 (chip_filter 참조).

        Returns
        -------
        list[MatchedCase] : relevance_score ≥ threshold 인 케이스 목록 (최대 max_candidates개,
                            relevance_score 내림차순). 빈 리스트면 MISS.
        """
        allowed_ids = self._get_allowed_case_ids(selected_profile_names or [])
        if allowed_ids is not None and not allowed_ids:
            return []
        candidates = self._vector_search(problem_text, allowed_ids, problem_vec)
        if not candidates:
            return []
        # 칩 하드 필터: chip_match_mode == "filter" 이면 defect 칩과 교집합이 없는
        # 케이스를 매치 후보에서 제외한다 (chip_tags 가 비어 있으면 공통 케이스로
        # 항상 통과, defect 칩이 비어 있으면 필터하지 않음 — chip_filter 참조).
        # "weight"(기본) 에서는 필터하지 않고 기존처럼 Reranker 순위 가중치로만 반영한다.
        if config.get_str("pipeline.chip_match_mode", "weight") == "filter":
            candidates = filter_patterns_by_chip(candidates, chip)
            if not candidates:
                return []
        return self._rerank(
            problem_text, candidates, knowledge_context,
            system_analysis_guidelines, chip,
        )

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

    def load_case_by_name(
        self, name: str, chip: list[str] | str | None = None
    ) -> MatchedCase | None:
        """
        케이스 이름으로 MatchedCase 를 직접 로드한다.

        벡터 검색·Reranker 를 거치지 않고 사용자가 지정한 케이스를 그대로
        Stage 3/4 로 전달할 때 사용한다. pinned=True 로 표시되며
        relevance_score 는 1.0 으로 고정한다.

        chip 이 주어지면 케이스에 연결된 패턴을 chip_tags 기준으로 필터링한다.
        """
        with get_conn(self.db_path) as conn:
            row = conn.execute(
                "SELECT id, name, description, keywords FROM cases WHERE name = ?",
                (name,),
            ).fetchone()
        if row is None:
            return None

        case_id      = int(row["id"])
        patterns     = self._load_patterns(case_id, chip)
        profile_refs = self._load_case_profile_refs(case_id)
        chip_tags    = self._load_case_chip_tags(case_id)
        references   = self._load_case_references(case_id)

        return MatchedCase(
            case_id         = case_id,
            name            = row["name"],
            description     = row["description"] or "",
            keywords        = _parse_json_list(row["keywords"]),
            relevance_score = 1.0,
            patterns        = patterns,
            profile_refs    = profile_refs,
            chip_tags       = chip_tags,
            references      = references,
            pinned          = True,
        )

    def load_case_by_id(
        self, case_id: int, chip: list[str] | str | None = None
    ) -> MatchedCase | None:
        """
        케이스 ID로 MatchedCase 를 직접 로드한다.

        Stage 2 벡터 검색·Reranker 를 거치지 않고, 이미 case_id 를 알고 있는
        경로(예: fallback 재귀속 — 전역 재매칭에서 실제로 걸린 패턴이 어느
        케이스 것인지 역조회한 뒤 그 케이스를 로드)에서 사용한다.

        relevance_score 는 0.0 으로 둔다 — 의미상 이 케이스는 Stage 2 의미
        유사도로 찾은 게 아니라 패턴 증거로 역추적된 것이라, 관련성 점수가
        없다(패턴 커버리지는 이후 Stage 3/4 재매칭에서 별도로 계산된다).
        """
        with get_conn(self.db_path) as conn:
            row = conn.execute(
                "SELECT id, name, description, keywords FROM cases WHERE id = ?",
                (case_id,),
            ).fetchone()
        if row is None:
            return None

        patterns     = self._load_patterns(case_id, chip)
        profile_refs = self._load_case_profile_refs(case_id)
        chip_tags    = self._load_case_chip_tags(case_id)
        references   = self._load_case_references(case_id)

        return MatchedCase(
            case_id         = case_id,
            name            = row["name"],
            description     = row["description"] or "",
            keywords        = _parse_json_list(row["keywords"]),
            relevance_score = 0.0,
            patterns        = patterns,
            profile_refs    = profile_refs,
            chip_tags       = chip_tags,
            references      = references,
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
        analysis: str = "",
        chip_tags: list[str] | None = None,
    ) -> None:
        """케이스를 ChromaDB 에 추가(또는 갱신)한다."""
        vec = embed([description])[0]
        self._col.upsert(
            ids        = [str(case_id)],
            embeddings = [vec],
            documents  = [description],
            metadatas  = [{
                "case_id":   case_id,
                "name":      name,
                "keywords":  json.dumps(keywords, ensure_ascii=False),
                "chip_tags": json.dumps(chip_tags or [], ensure_ascii=False),
            }],
        )
        if analysis.strip():
            vec_a = embed([analysis])[0]
            self._col_analysis.upsert(
                ids        = [str(case_id)],
                embeddings = [vec_a],
                documents  = [analysis],
                metadatas  = [{"case_id": case_id, "name": name}],
            )
        else:
            # analysis가 비어있으면 기존 항목 제거 (수정으로 비워진 경우 대비)
            self._col_analysis.delete(ids=[str(case_id)])

    def remove_case(self, case_id: int) -> None:
        """케이스를 ChromaDB 에서 제거한다."""
        self._col.delete(ids=[str(case_id)])
        self._col_analysis.delete(ids=[str(case_id)])

    def sync_from_db(self) -> int:
        """
        SQLite cases 테이블 전체를 ChromaDB 에 동기화한다.

        Returns: 동기화된 케이스 수
        """
        with get_conn(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, name, description, keywords, analysis, chip_tags FROM cases"
            ).fetchall()

        for row in rows:
            self.add_case(
                case_id     = row["id"],
                name        = row["name"],
                description = row["description"],
                keywords    = json.loads(row["keywords"]),
                analysis    = row["analysis"] or "",
                chip_tags   = _parse_json_list(row["chip_tags"]),
            )
        return len(rows)

    # ── Step A: 벡터 검색 ─────────────────────────────────────────────────────

    def _vector_search(
        self,
        query: str,
        allowed_case_ids: list[int] | None = None,
        problem_vec: list[float] | None = None,
    ) -> list[dict]:
        """embed() → cases / cases_analysis 두 컬렉션 Top-K 검색 후 합산.

        allowed_case_ids가 주어지면 해당 case_id만 검색 대상으로 제한한다.
        None이면 모든 케이스를 검색한다.

        problem_vec가 주어지면 embed() 호출을 생략하고 재사용한다.

        반환 dict의 distance는 두 컬렉션 중 더 낮은 값(더 유사한 쪽)을 사용한다.
        distance_desc / distance_analysis는 각 컬렉션의 개별 값이며,
        검색되지 않은 경우 None으로 채워진다.
        """
        total = self._col.count()
        if total == 0:
            return []

        vec = problem_vec if problem_vec is not None else embed([query])[0]

        def _query_col(col, n: int) -> dict[int, dict]:
            """컬렉션을 검색하여 {case_id: {...}} 딕셔너리로 반환한다."""
            if col.count() == 0:
                return {}
            kwargs: dict = {
                "query_embeddings": [vec],
                "n_results":        min(n, col.count()),
                "include":          ["documents", "metadatas", "distances"],
            }
            if allowed_case_ids is not None:
                kwargs["where"] = {"case_id": {"$in": allowed_case_ids}}
            res = col.query(**kwargs)
            out: dict[int, dict] = {}
            for i, _ in enumerate(res["ids"][0]):
                meta     = res["metadatas"][0][i]
                cid      = int(meta["case_id"])
                out[cid] = {
                    "document": res["documents"][0][i],
                    "distance": res["distances"][0][i],
                    "meta":     meta,
                }
            return out

        desc_results     = _query_col(self._col, self.top_k)
        analysis_results = _query_col(self._col_analysis, self.top_k)

        # 두 결과를 case_id 기준으로 합산 (중복 제거)
        all_ids = set(desc_results) | set(analysis_results)
        if not all_ids:
            return []

        # SQLite에서 analysis 컬럼 포함 케이스 정보 일괄 로드
        with get_conn(self.db_path) as conn:
            placeholders = ",".join("?" * len(all_ids))
            rows = conn.execute(
                f"SELECT id, name, description, keywords, analysis, chip_tags FROM cases "
                f"WHERE id IN ({placeholders})",
                list(all_ids),
            ).fetchall()
        db_map = {int(r["id"]): r for r in rows}

        candidates: list[dict] = []
        for cid in all_ids:
            if cid not in db_map:
                continue
            row              = db_map[cid]
            d_desc           = desc_results.get(cid, {}).get("distance")
            d_anal           = analysis_results.get(cid, {}).get("distance")
            # 대표 distance: 두 값 중 더 낮은 쪽 (더 유사한 쪽)
            distances        = [d for d in (d_desc, d_anal) if d is not None]
            best_distance    = min(distances) if distances else 1.0
            candidates.append({
                "case_id":           cid,
                "name":              row["name"],
                "description":       row["description"],
                "analysis":          row["analysis"] or "",
                "keywords":          json.loads(row["keywords"]),
                "chip_tags":         _parse_json_list(row["chip_tags"]),
                "distance":          best_distance,
                "distance_desc":     d_desc,
                "distance_analysis": d_anal,
            })

        # 대표 distance 오름차순 정렬 후 Top-K로 제한
        candidates.sort(key=lambda c: c["distance"])
        return candidates[:self.top_k]

    # ── Step B: LLM Reranker ──────────────────────────────────────────────────

    def _rerank(
        self,
        problem_text: str,
        candidates: list[dict],
        knowledge_context: str = "",
        system_analysis_guidelines: str = "",
        chip: list[str] | str | None = None,
    ) -> list[MatchedCase]:
        """
        후보 전체를 한 번에 채점한다. threshold 이상 케이스를 relevance_score
        내림차순으로 최대 max_candidates개 반환한다.

        채점 방식은 reranker 프로필의 provider 에 따라 갈린다:
          - provider == "vllm-rerank" : rerank 엔드포인트(cross-encoder) 채점.
            knowledge_context·system_analysis_guidelines 는 (query, doc) 쌍만
            보는 cross-encoder 특성상 반영되지 않는다. threshold 는 LLM 채점과
            스케일이 달라 rerank_threshold 를 별도로 쓴다.
          - 그 외(기존 동작)    : LLM 프롬프트 채점 (현행).
        두 종류가 primary/fallback 으로 섞여도 각 프로필 시도마다 독립 분기한다.
        """
        # 시도 순서: 1차 primary(독립 프로필), 2차 fallback(설정된 경우) 또는 primary 재시도.
        # 각 프로필은 provider/base_url/api_key/model 을 전부 포함하므로 서로 완전히 독립적으로 연결된다.
        profiles_to_try: list[dict] = [self._llm_profile]
        if self._llm_fallback_profile and self._llm_fallback_profile is not self._llm_profile:
            profiles_to_try.append(self._llm_fallback_profile)
        else:
            profiles_to_try.append(self._llm_profile)

        # rerank 엔드포인트 경로에서만 쓰는 문서 직렬화 — 프로필이 전부 LLM 이면 미사용.
        endpoint_documents: list[str] | None = None

        last_exc: Exception | None = None
        content: str = ""
        scores: list[dict] = []
        effective_threshold = self.threshold
        for attempt, profile in enumerate(profiles_to_try):
            model = profile.get("model", "")
            try:
                if _is_rerank_endpoint(profile):
                    if endpoint_documents is None:
                        endpoint_documents = [_candidate_document(c) for c in candidates]
                    pairs = llm_rerank(profile, problem_text, endpoint_documents)
                    scores = [{"index": idx + 1, "relevance_score": s} for idx, s in pairs]
                    effective_threshold = self.rerank_threshold
                else:
                    prompt = _build_rerank_prompt(
                        problem_text, candidates, knowledge_context, system_analysis_guidelines
                    )
                    content = chat_with_profile(
                        profile     = profile,
                        messages    = [{"role": "user", "content": prompt}],
                        json_mode   = True,
                        temperature = 0.0,
                    )
                    scores = json.loads(content).get("scores", [])
                    effective_threshold = self.threshold
                break
            except RerankError as e:
                last_exc = e
                logger.warning(
                    "Reranker 엔드포인트 채점 실패 (시도 %d/%d, model=%s): %s",
                    attempt + 1, len(profiles_to_try), model, e,
                )
            except json.JSONDecodeError as e:
                last_exc = e
                logger.warning(
                    "Reranker JSON 파싱 실패 (시도 %d/%d, model=%s): %s | 응답 내용: %r",
                    attempt + 1, len(profiles_to_try), model, e, content[:200],
                )
            except Exception as e:
                last_exc = e
                logger.warning(
                    "Reranker 호출 실패 (시도 %d/%d, model=%s): %s",
                    attempt + 1, len(profiles_to_try), model, e,
                )
        else:
            tried = " → ".join(p.get("model", "") for p in profiles_to_try)
            raise KBRerankerError(
                f"Reranker 호출이 모두 실패했습니다 (시도 모델: {tried}): {last_exc}"
            )

        # threshold 이상인 (idx, score) 목록을 추출한다. threshold 비교는
        # 항상 원점수로 수행한다 (칩 가중치는 컷이 아니라 순위에만 반영).
        # effective_threshold 는 실제 채점에 쓰인 프로필 종류를 따른다.
        passed: list[tuple[int, float]] = []
        for s in scores:
            idx   = int(s.get("index", 0)) - 1       # 1-based → 0-based
            score = float(s.get("relevance_score", 0.0))
            if 0 <= idx < len(candidates) and score >= effective_threshold:
                passed.append((idx, score))

        # 칩 가중치: defect chip 과 케이스 chip_tags 가 일치하면 정렬에서 우대한다.
        # 검색·threshold 에는 영향을 주지 않고 (score 원점수 유지) 순위만 끌어올려
        # max_candidates 컷에 동일 칩 케이스가 우선 살아남도록 한다.
        passed.sort(
            key=lambda x: (
                chip_matches(self._load_case_chip_tags(candidates[x[0]]["case_id"]), chip),
                x[1],
            ),
            reverse=True,
        )
        passed = passed[:self.max_candidates]

        if not passed:
            return []

        results: list[MatchedCase] = []
        for idx, score in passed:
            candidate    = candidates[idx]
            patterns     = self._load_patterns(candidate["case_id"], chip)
            profile_refs = self._load_case_profile_refs(candidate["case_id"])
            chip_tags    = self._load_case_chip_tags(candidate["case_id"])
            references   = self._load_case_references(candidate["case_id"])
            results.append(MatchedCase(
                case_id         = candidate["case_id"],
                name            = candidate["name"],
                description     = candidate["description"],
                keywords        = candidate["keywords"],
                relevance_score = score,
                patterns        = patterns,
                profile_refs    = profile_refs,
                chip_tags       = chip_tags,
                references      = references,
            ))
        return results

    # ── 패턴 로드 (Stage 3/4 용) ──────────────────────────────────────────────

    def _load_patterns(
        self, case_id: int, chip: list[str] | str | None = None
    ) -> list[dict]:
        """케이스에 연결된 패턴을 SQLite 에서 로드한다.

        chip 이 주어지면 chip_tags 기준으로 필터링한다 (chip_filter 참조).
        """
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

        return filter_patterns_by_chip(result, chip)

    def _load_case_profile_refs(self, case_id: int) -> list[str]:
        """케이스에 연결된 분석 프로파일 이름 목록을 SQLite 에서 로드한다."""
        with get_conn(self.db_path) as conn:
            row = conn.execute(
                "SELECT profile_refs FROM cases WHERE id=?", (case_id,)
            ).fetchone()
        return _parse_json_list(row["profile_refs"]) if row else []

    def _load_case_chip_tags(self, case_id: int) -> list[str]:
        """케이스가 해당하는 칩 목록을 SQLite 에서 로드한다."""
        with get_conn(self.db_path) as conn:
            row = conn.execute(
                "SELECT chip_tags FROM cases WHERE id=?", (case_id,)
            ).fetchone()
        return _parse_json_list(row["chip_tags"]) if row else []

    def _load_case_references(self, case_id: int) -> list[dict]:
        """케이스에 연결된 외부 참조 ID 목록을 SQLite 에서 로드한다."""
        with get_conn(self.db_path) as conn:
            rows = conn.execute(
                "SELECT system, reference_id FROM case_references "
                "WHERE case_id=? ORDER BY id",
                (case_id,),
            ).fetchall()
        return [{"system": r["system"], "reference_id": r["reference_id"]} for r in rows]


# ── rerank 엔드포인트 ─────────────────────────────────────────────────────────

def _is_rerank_endpoint(profile: dict) -> bool:
    """reranker 프로필이 cross-encoder rerank 엔드포인트를 가리키는지 판정한다."""
    return profile.get("provider") == "vllm-rerank"


def _candidate_document(c: dict) -> str:
    """후보 케이스를 rerank 엔드포인트의 document 문자열로 직렬화한다.

    LLM 프롬프트 채점(_build_rerank_prompt)과 동일한 정보량(name+description+
    analysis)을 담아, 두 채점 방식의 입력 격차를 최소화한다.
    """
    parts = [c["name"], c["description"]]
    if c.get("analysis", "").strip():
        parts.append(c["analysis"])
    return "\n\n".join(p for p in parts if p)


# ── 프롬프트 ──────────────────────────────────────────────────────────────────

def _build_rerank_prompt(
    problem_text: str,
    candidates: list[dict],
    knowledge_context: str = "",
    system_analysis_guidelines: str = "",
) -> str:
    def _fmt_sim(d: float | None) -> str:
        return f"{1 - d:.3f}" if d is not None else "N/A"

    blocks = []
    for i, c in enumerate(candidates):
        lines = [
            f"[{i + 1}]",
            f"이름: {c['name']}",
            f"설명: {c['description']}",
        ]
        if c.get("analysis", "").strip():
            lines.append(f"분석: {c['analysis']}")
        sim_desc = _fmt_sim(c.get("distance_desc"))
        sim_anal = _fmt_sim(c.get("distance_analysis"))
        if c.get("analysis", "").strip():
            lines.append(f"벡터 유사도 (설명): {sim_desc}  벡터 유사도 (분석): {sim_anal}")
        else:
            lines.append(f"벡터 유사도: {sim_desc}")
        blocks.append("\n".join(lines))

    candidate_block = "\n\n".join(blocks)
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
