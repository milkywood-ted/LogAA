# KB Reranker — 전용 rerank 엔드포인트 지원 구현 설계

| 항목 | 값 |
| --- | --- |
| 문서 유형 | 구현 설계 (Implementation Spec) — 아직 미구현, 착수 전 설계 |
| 작성일 | 2026-07-22 |
| 기준 커밋 | `e646d77` (branch `main` — PR #36 revert 반영) |
| 대상 | `AnalyzingAssistant_v2/` Stage 2 (KB Reranker) |
| 작성 기준 | [기술 문서 작성 가이드](../Technical_Design/기술%20문서%20작성%20가이드.md) 표준 목차 |
| 서빙 스택 | **vLLM** (rerank 엔드포인트 제공 확정 — 사용자 확인) |

> 본 문서는 코드를 수정하지 않고 착수 가능한 수준까지 설계만 확정한다.
> 구현 착수는 §8 Phase 순서를 따르며, 각 Phase 완료 시 사용자 확인을 받는다.

---

## 0. 목표 및 범위

### 배경 (해결하려는 문제)

현재 KB Stage 2의 Reranker는 **LLM-as-reranker** 방식이다. 후보 케이스들을 프롬프트에 넣어 생성형 LLM이 `{"scores":[...]}` JSON으로 관련성을 채점한다([kb_search.py `_rerank`](../../AnalyzingAssistant_v2/core/kb_search.py) → [`chat_with_profile`](../../AnalyzingAssistant_v2/core/llm.py)).

그런데 현재 커밋된 설정([config.yaml:3](../../AnalyzingAssistant_v2/config/LLM/config.yaml))은 `reranker_llm`을 **cross-encoder reranker 모델**(`bjson/bge-reranker-base`)로 지정하고 있다. 이 프로필에는 `provider`가 없어 기본값 `openai`로 해석되고, 결국 cross-encoder 모델에 `/v1/chat/completions`를 호출한다. cross-encoder는 chat 생성을 못 하므로 호출이 실패하고, fallback도 비어 있어 같은 프로필을 두 번 시도한 뒤 `KBRerankerError`로 Stage 2 전체가 실패한다.

즉 **"reranker"라는 이름이 두 가지(LLM-as-reranker vs cross-encoder reranker)를 가리키는데, 설정은 후자를 넣고 코드는 전자를 기대**하는 불일치다. 본 작업은 코드가 **cross-encoder rerank 전용 엔드포인트를 정식 지원**하도록 하여 이 불일치를 해소한다.

### 범위 안

- `core/llm.py`: rerank 엔드포인트 호출 함수 신설 (provider 분기).
- `core/kb_search.py` `_rerank`: 프로필 종류에 따라 **엔드포인트 rerank 경로**와 기존 **LLM 프롬프트 경로**를 분기.
- 프로필 스키마: rerank 엔드포인트 프로필을 식별할 수단 추가.
- `api/router/settings.py` + 설정 UI: rerank 엔드포인트 프로필 선택·검증.
- `config/LLM/config.yaml`: rerank 프로필 예시.

### 범위 밖 (건드리지 않음)

- `_rerank`의 **조립부**([kb_search.py:475-508](../../AnalyzingAssistant_v2/core/kb_search.py#L475-L508)) — 칩 가중치 정렬 · `max_candidates` 컷 · `MatchedCase` 조립. `(idx, score)` 튜플만 소비하므로 점수 출처와 무관하다.
- Stage 2 벡터 검색(`_vector_search` / `embed()`) — 임베딩 프로필(`active_embed`)을 쓰며 reranker와 별개다.
- `pipeline.py`의 Stage 오케스트레이션 · threshold 필터 **구조**.

### 확정된 방향

| # | 항목 | 결정 | 근거 |
| --- | --- | --- | --- |
| D1 | 교체 vs 병존 | 기존 LLM 프롬프트 reranker를 **제거하지 않고 병존**. 프로필 종류로 경로 선택 | 하위호환 + LLM reranker의 컨텍스트 신호(§C2) 보존 옵션 유지 |
| D2 | 호출 위치 | `_rerank`의 **채점부만** 분기, 조립부는 공유 | 격리도 최대화, downstream 무변경 (범위 밖) |
| D3 | 서빙 | vLLM rerank 엔드포인트 (Cohere/Jina 호환) | 사용자 확인 |

---

## 1. 요구사항·제약

| # | 요구사항 / 제약 | 확신도 |
| --- | --- | --- |
| R1 | reranker 프로필이 rerank 엔드포인트를 가리킬 때, chat completion 없이 후보를 채점할 수 있어야 한다 | high |
| R2 | 기존 LLM 프롬프트 reranker 경로는 그대로 동작해야 한다(프로필 종류로 선택) | high |
| R3 | rerank 결과는 기존 `passed: list[(idx, score)]` 구조로 매핑되어, threshold·칩 가중치·`MatchedCase` 조립이 무변경으로 재사용되어야 한다 | high |
| R4 | rerank 엔드포인트 프로필도 fallback 프로필 재시도 메커니즘([kb_search.py:429-433](../../AnalyzingAssistant_v2/core/kb_search.py#L429-L433))과 호환되어야 한다 | medium |
| C1 | 서빙은 vLLM rerank API (`POST /rerank`, Cohere/Jina 호환). 입력 (query, documents[]), 출력 문서별 relevance_score | high |
| C2 | cross-encoder는 (query, doc) 쌍만 본다 — LLM reranker가 주입하던 `knowledge_context`·`system_analysis_guidelines`([kb_search.py:415-416](../../AnalyzingAssistant_v2/core/kb_search.py#L415-L416))를 채점에 반영할 수 없다 | high |
| C3 | cross-encoder 점수 스케일은 LLM의 0~1 관련성 점수와 다르다. 현 `kb_threshold: 0.7`([config.yaml:27](../../AnalyzingAssistant_v2/config/LLM/config.yaml#L27))은 재검증·재보정이 필요하다 | high |
| C4 | vLLM은 `/rerank`·`/v1/rerank`·`/v2/rerank`를 모두 루트에 등록한다. `base_url`이 `/v1`로 끝나는 이 코드베이스 컨벤션에서는 실효 경로가 `/v1/rerank`이며, `rerank_path`에 `/v1/rerank`를 중복 지정하면 `/v1/v1/rerank`로 404가 난다 | high |

---

## 2. 아키텍처 개요

`_rerank`의 채점부를 프로필 종류에 따라 두 경로로 분기한다. **엔드포인트 경로**는 `llm.rerank(profile, query, documents)`로 문서별 점수를 받아 `(idx, score)`로 매핑하고, **LLM 경로**는 기존 프롬프트 채점을 그대로 쓴다. 두 경로 모두 같은 `passed` 리스트를 만들어 이후 조립부로 넘긴다. 새 함수 `llm.rerank`는 `chat_with_profile`이 이미 쓰는 provider 분기 패턴([llm.py:47-53](../../AnalyzingAssistant_v2/core/llm.py#L47-L53))을 그대로 따른다. 이 구조가 downstream을 건드리지 않으면서(격리도 최대, R3) 기존 동작을 보존(R2)하는 최소 변경 경로다.

---

## 3. 컴포넌트 설계

### 3.1 `core/llm.py` — rerank 호출 함수 신설

기존 `chat_with_profile` 옆에 rerank 전용 함수를 추가한다.

```python
def rerank(profile: dict, query: str, documents: list[str],
           top_n: int | None = None) -> list[tuple[int, float]]:
    """rerank 엔드포인트를 호출해 (문서 index, relevance_score) 목록을 반환한다.
    입력 documents 순서 기준 index. 점수 내림차순 정렬 여부는 호출측이 처리."""
    provider = profile.get("provider", "")
    if provider == "vllm-rerank":         # §4.3 프로필 식별
        return _rerank_vllm(profile, query, documents, top_n)
    raise RerankError(f"지원하지 않는 rerank provider: {provider!r}")
```

- `_rerank_vllm`: `httpx.post(base_url + rerank_path, json={...})` — 이미 import된 `httpx`([llm.py:17](../../AnalyzingAssistant_v2/core/llm.py#L17)) 재사용. 요청/응답 스키마는 §4.1.
- **전체 URL = `base_url` + `rerank_path` 합성**. 이 코드베이스의 모든 프로필은 `base_url`이 `/v1`로 끝나므로([config.yaml](../../AnalyzingAssistant_v2/config/LLM/config.yaml)), `rerank_path` 기본값을 `/rerank`로 두면 실제 호출은 `/v1/rerank`가 되어 vLLM에서 가장 흔한 경로와 맞는다. `rerank_path`는 프로필 필드로 노출해 §C4의 버전 차이를 흡수한다.
- **주의**: vLLM은 `/rerank`·`/v1/rerank`·`/v2/rerank`를 모두 루트에서 등록한다. `base_url`이 이미 `/v1`로 끝나는데 `rerank_path`에 `/v1/rerank`를 넣으면 `/v1/v1/rerank`로 404가 난다. 기본값은 base_url이 `/v1`을 포함한다는 전제(이 코드베이스 컨벤션)에서 `/rerank`로 둔다.
- 예외 타입 `RerankError`(신설) — `_rerank`의 재시도 루프가 잡을 수 있도록 `Exception` 하위.
- 빈 응답·형식 불일치 시 `RerankError`로 통일해 상위에서 fallback 재시도(R4)가 걸리게 한다.

### 3.2 `core/kb_search.py` `_rerank` — 채점부 분기

[kb_search.py:411-473](../../AnalyzingAssistant_v2/core/kb_search.py#L411-L473)의 채점부만 수정한다. 조립부(475-508)는 무변경.

```python
def _score_candidates(self, profile, problem_text, candidates,
                      knowledge_context, system_analysis_guidelines) -> list[tuple[int,float]]:
    if _is_rerank_endpoint(profile):
        docs = [_candidate_document(c) for c in candidates]
        pairs = llm_rerank(profile, problem_text, docs)      # [(idx, score)]
        return [(i, s) for i, s in pairs if s >= self.threshold]
    # 기존 LLM 프롬프트 경로 (현행 유지)
    ...
```

- `_candidate_document(c)`: 후보를 문서 문자열로 직렬화. 기존 프롬프트가 name/description/analysis를 넣던 것([_build_rerank_prompt](../../AnalyzingAssistant_v2/core/kb_search.py))과 정합되게 `name + description + analysis`를 결합. **결정 필요** → §6.
- 재시도 루프([kb_search.py:429-459](../../AnalyzingAssistant_v2/core/kb_search.py#L429-L459))는 프로필 목록을 순회하는 구조 그대로 두고, 각 프로필에 대해 위 `_score_candidates`를 호출하도록 감싼다. 엔드포인트 프로필과 LLM 프로필이 primary/fallback으로 **섞여도** 동작한다(R4).
- 엔드포인트 경로에서는 `knowledge_context`·`system_analysis_guidelines`를 **사용하지 않는다**(C2). 이 손실은 문서화하고 로그로 남긴다.

### 3.3 프로필 식별 — `core/config/`

rerank 엔드포인트 프로필과 chat LLM 프로필을 구분할 수단이 필요하다. **결정 필요** → §6 (D-a).

권고안: `llm_profiles` 항목에 `provider: vllm-rerank`를 쓰고, `rerank_path`(선택, 기본 `/rerank`)를 둔다. `chat_with_profile`은 이미 `provider`로 분기하므로([llm.py:47](../../AnalyzingAssistant_v2/core/llm.py#L47)) 일관된다. `_is_rerank_endpoint(profile)`은 `profile.get("provider") == "vllm-rerank"`로 판정.

`reranker_llm()` 해석([config/__init__.py:124-129](../../AnalyzingAssistant_v2/core/config/__init__.py#L124-L129))은 그대로 — 이름으로 `llm_profiles`에서 찾는다.

### 3.4 `api/router/settings.py` + 설정 UI

- reranker 설정 저장([settings.py:315-324](../../AnalyzingAssistant_v2/api/router/settings.py#L315-L324))은 이름 존재만 검증한다. 여기에 **성격 검증**을 추가할지 결정 필요(§6 D-c): rerank/chat 프로필을 잘못 지정하면 명시적 400을 낼지, 런타임 실패에 맡길지.
- 모델 목록 조회(`/reranker/models` 유무 확인 필요) — rerank 엔드포인트는 chat `/models`와 다를 수 있어 UI 모델 드롭다운은 수기 입력 허용으로 둘 수 있다.

### 3.5 `config/LLM/config.yaml` — 예시 프로필

```yaml
llm_profiles:
- name: bge-reranker-vllm
  provider: vllm-rerank
  base_url: http://<vllm-host>:<port>/v1   # 기존 프로필과 동일하게 /v1 로 끝냄
  rerank_path: /rerank                     # 합성 결과 = .../v1/rerank
  api_key: ...
  model: BAAI/bge-reranker-v2-m3
reranker_llm: bge-reranker-vllm
```

`base_url`을 `/v1`로 끝내고 `rerank_path` 기본값 `/rerank`를 붙여 실제 호출 URL이 `.../v1/rerank`가 되게 한다 — 기존 chat/embed 프로필의 base_url 컨벤션과 일치한다. 현재의 깨진 `bge-rernaker-v2` 프로필(provider 없음, base_url이 Ollama)을 이 형태로 교정한다.

---

## 4. 인터페이스 / 계약

### 4.1 vLLM rerank API (Cohere/Jina 호환)

요청 (`{base_url}{rerank_path}` = 예: `http://host:port/v1/rerank`):
```json
{
  "model": "BAAI/bge-reranker-v2-m3",
  "query": "<problem_text>",
  "documents": ["<doc0>", "<doc1>", ...]
}
```

응답:
```json
{
  "results": [
    {"index": 0, "relevance_score": 0.98},
    {"index": 2, "relevance_score": 0.71},
    ...
  ]
}
```

- `index`는 요청 `documents` 배열 기준. `_rerank`의 후보 순서와 1:1 대응한다.
- vLLM은 `--task score`(또는 rerank 지원 태스크)로 cross-encoder 모델을 서빙해야 한다 — **인프라 확인 항목**(§8 Phase 0).
- `relevance_score` 스케일은 서버 설정에 의존(보통 sigmoid 0~1). §C3 재보정 대상.

### 4.2 신규 함수 시그니처

```python
# core/llm.py
def rerank(profile: dict, query: str, documents: list[str],
           top_n: int | None = None) -> list[tuple[int, float]]

class RerankError(Exception): ...
```

### 4.3 프로필 스키마 확장 (권고안)

| 필드 | 값 | 비고 |
| --- | --- | --- |
| `provider` | `vllm-rerank` | rerank 엔드포인트 식별자 |
| `rerank_path` | `/rerank` (기본) | §C4 버전 차이 흡수 |
| `base_url`, `api_key`, `model` | 기존과 동일 | |

---

## 5. 데이터 · 제어 흐름

```
Stage 2 search()
  └─ _vector_search  → embed()[active_embed]  → 후보 candidates (Top-K)
  └─ _rerank(candidates)
       └─ _score_candidates(reranker_profile)
            ├─ [provider == vllm-rerank]  llm.rerank(query, docs) → (idx, score)[]
            │                              → threshold 필터
            └─ [그 외]                     기존 LLM 프롬프트 채점 (현행)
       └─ [조립부·무변경] 칩 가중치 정렬 → max_candidates 컷 → MatchedCase[]
```

벡터 검색(임베딩 프로필)과 rerank(reranker 프로필)는 **별개 프로필**이라는 점이 핵심 — 둘을 혼동하면 §0의 원인 불일치가 재발한다.

---

## 6. 트레이드오프 & 대안 (결정 필요 항목)

| # | 결정 항목 | 옵션 | 권고 |
| --- | --- | --- | --- |
| D-a | 프로필 식별 방식 | (1) `provider: vllm-rerank` 필드 / (2) 별도 `reranker_profiles` 목록 / (3) `kind: rerank` 플래그 | **(1)** — `chat_with_profile`의 기존 provider 분기와 일관, 추가 목록·스키마 최소 |
| D-b | 문서 직렬화 범위 | (1) description만 / (2) name+description / (3) name+description+analysis | **(3)** — 기존 LLM 프롬프트와 동일 정보량. 단 cross-encoder 입력 길이 한도 확인 필요 |
| D-c | 잘못된 프로필 방어 | (1) 설정 저장 시 성격 검증 400 / (2) 런타임 실패에 맡김(현행 KBRerankerError) | **(1)** — §0 사고의 재발 방지. 단 rerank/chat 판별 기준 필요 |
| D-d | threshold 스케일 | (1) `kb_threshold` 재사용 / (2) rerank 전용 `rerank_threshold` 신설 | **(2)** — LLM 0~1과 cross-encoder 점수 분포가 달라(§C3) 공용 컷은 오판 위험. Phase 4에서 실측 후 확정 |
| D-e | LLM reranker 유지 여부 | (1) 병존(프로필로 선택) / (2) 전면 교체 | **(1)** — 하위호환 + `knowledge_context` 신호(§C2)를 쓰고 싶을 때 선택 가능 (D1 확정) |

---

## 7. 마이그레이션 / 호환성

- **기존 LLM reranker 프로필**: `provider`가 `vllm-rerank`가 아니므로 자동으로 기존 경로. 무영향.
- **현재 깨진 설정**: `reranker_llm: bge-rernaker-v2`(provider 없음 → chat 호출 실패)는 본 작업으로 **정식 rerank 프로필로 교정**하면 동작한다. 교정 전까지는 §0의 실패가 지속되므로, 급하면 임시로 `reranker_llm`을 비워 `active_llm` 폴백([config/__init__.py:126](../../AnalyzingAssistant_v2/core/config/__init__.py#L126))으로 우회 가능(별도 조치, 본 설계와 독립).
- **이력·downstream**: rerank 점수도 `relevance_score`로 `MatchedCase`에 실려 기존 직렬화·표기와 동일. 스키마 변경 없음.

---

## 8. 구현 단계 (각 Phase 완료 후 사용자 확인)

- [ ] **Phase 0 — 인프라 확인**: vLLM에 cross-encoder(`bge-reranker`)를 rerank 태스크로 서빙 중인지, 엔드포인트 경로(`/rerank` vs `/v1/rerank` vs `/v2/rerank`)와 응답 스키마를 실호출로 확인. (코드 아님)
- [ ] **Phase 1 — `llm.rerank`**: 함수 + `RerankError` + vLLM 호출 구현. httpx 목킹 단위 테스트(요청 스키마, 응답 파싱, 오류→RerankError).
- [ ] **Phase 2 — `_rerank` 분기**: `_score_candidates` 추출 + provider 분기 + `_candidate_document`. 기존 LLM 경로 회귀 테스트 + 엔드포인트 경로 테스트(스텁).
- [ ] **Phase 3 — 설정/프로필**: 프로필 식별(D-a), settings 검증(D-c), config.yaml 예시, 설정 UI 소폭 확장.
- [ ] **Phase 4 — threshold 재보정 + E2E**: 실제 vLLM으로 대표 케이스 채점, 점수 분포 실측 후 `rerank_threshold`(D-d) 확정. Stage 2 관통 검증.

---

## 9. 확정 이력 & 미해결 질문

### 확정 이력

| 일자 | 내용 |
| --- | --- |
| 2026-07-22 | 최초 작성. 서빙 스택 vLLM 확인. 코드 격리도 조사 후 착수 전 설계 확정 |
| 2026-07-22 | PR #38(PR #36 revert) 반영 — 기준 커밋 `e646d77`로 갱신, kb_search.py 라인 인용 5건 재조정(채점부 411-473, 조립부 475-508 등). 설계 내용 자체는 PR #36 코드에 의존하지 않아 불변 |

### 미해결 질문 (착수 전 사용자 확인 필요)

1. **D-a 프로필 식별 방식** — 권고안 `provider: vllm-rerank`로 확정할지.
2. **D-b 문서 직렬화 범위** — analysis 포함 여부 + cross-encoder 입력 길이 한도.
3. **D-c 방어 검증** — 설정 저장 시 rerank/chat 성격 오지정을 막을지, 막는다면 판별 기준.
4. **D-d threshold** — 전용 `rerank_threshold` 신설 방향 동의 여부(구체 값은 Phase 4 실측).
5. **범위 질문** — 본 작업과 별개로, 지금 즉시 깨진 config(`reranker_llm`)를 임시 우회(비워서 active_llm 폴백)해 둘지, 아니면 이 구현으로 한 번에 해결할지.
