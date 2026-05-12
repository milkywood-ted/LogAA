# Kernel Log Analyzer — System Architecture

## Frontend: Streamlit Web UI

| Page | 이름 | 설명 |
|------|------|------|
| 1 | 🔍 분석 | 로그/문제설명 입력, 프로파일 선택(복수 가능), 임시 사전정제 키워드 입력, 케이스 직접 지정(선택), 설정 조정 |
| 2 | 📊 결과 | 실시간 분석 진행 상황, 판정 배너, 문제 패턴 상세, 정제 로그, 리포트 |
| 3 | 📚 케이스 업데이트 및 관리 | 케이스 + 문제 패턴 통합 입력, 분석 결과 가져오기 |
| 4 | 🎯 문제 패턴 관리 | 문제 패턴 단독 CRUD (케이스 연결 없이 독립 관리), 패턴별 분석지침 관리 |
| 5 | 📋 분석 프로파일 관리 | 분석 프로파일 CRUD (JSON 파일), 사전지식 참조 관리 |
| 6 | ⚙️ 설정 | 임계값, 모델, 컨텍스트 전략, 프롬프트 템플릿, 노이즈패턴 관리 |
| 6b | 🔌 연결 확인 | LLM / 임베딩 엔드포인트 연결 테스트, 모델 목록 조회 |

- **공유 자원**: `st.cache_resource` (KBSearch, PatternMatcher)
- **페이지간 데이터**: `st.session_state` (result, history)

### 이력 업데이트 (Page 3)

케이스(문제 설명)와 문제 패턴을 한 화면에서 통합 입력하고 한 번에 저장한다.

| 입력 경로 | 설명 |
|-----------|------|
| 수기 입력 (주요) | 문제 설명 작성 → 케이스 등록 + ChromaDB 임베딩, 문제 패턴 정의 → 패턴 등록 + 케이스 연결, 관련 로그/evidence 첨부 (선택) |
| 분석 결과 가져오기 (보조) | 기존 분석 결과를 불러와 케이스/패턴 항목을 자동 채움 → 사용자 검토/수정 후 저장 |

### 케이스 — 문제 패턴 관계

케이스(문제 상황)와 문제 패턴은 N:M 관계이며, 각각 독립적으로 관리된다.

| 엔티티 | 독립 존재 | 제약 |
|--------|-----------|------|
| 케이스 | 불가 | 최소 1개 이상의 문제 패턴 연결 필수 |
| 문제 패턴 | 가능 | 케이스 연결 없이 단독 존재 가능 |

```
cases ──┐
        ├── case_patterns (case_id, pattern_id)
patterns ──┘
```

- 하나의 케이스에 여러 문제 패턴이 연결될 수 있다
- 하나의 문제 패턴이 여러 케이스에 연결될 수 있다
- 케이스 저장 시 문제 패턴 최소 1개 연결 유효성 검증을 수행한다

### 케이스 추천 프로파일 (profile_refs)

`cases` 테이블의 `profile_refs` 컬럼에 해당 케이스에 적합한 분석 프로파일 이름 목록을 JSON 배열로 저장한다.

| 동작 시점       | 설명                                                                     |
|----------------|-------------------------------------------------------------------------|
| 케이스 등록/편집 | Page 3 편집 폼에서 추천 프로파일 multiselect로 지정                        |
| Stage 2 HIT 후  | 매칭된 케이스의 `profile_refs`를 사용자 선택 프로파일에 자동 병합 (뒤에 추가) |

Stage 2 HIT 시 케이스 추천 프로파일 자동 병합 흐름:

```
Stage 2 HIT
    │
    └─ matched_case.profile_refs 존재?
          ├─ YES → merge_profiles(profile_refs) → _combine_merged_profiles(user_merged, case_merged)
          │          ├─ 분석 지침: 사용자 프로파일 우선, 케이스 추천 후순위
          │          ├─ 사전정제 키워드: 합집합
          │          └─ ChromaDB 사전지식: 추가 enrichment
          └─ NO  → 사용자 선택 프로파일 그대로 사용
```

### 실시간 분석 진행 표시 (Page 1)

분석 페이지에서 실행 버튼 클릭 시 각 Stage의 진행 상태를 실시간으로 표시한다.

#### 구현 방식

`pipeline.run()`에 `on_progress` 콜백 파라미터를 추가하고, 각 Stage 진입 시 호출한다. UI는 `st.status` + `st.progress`를 조합하여 단계 진행을 실시간으로 렌더링한다.

```python
on_progress(step: int, total: int, stage_name: str, detail: str) -> None
```

#### 진행 단계 (총 7단계 / Reflection 활성화 시 8단계)

| Step | Stage | 표시 내용 | 조건 |
| ---- | ----- | --------- | ---- |
| 1/7(8) | Stage 1 | 로그 정제 — 파일 선별 및 노이즈 제거 중 | 항상 |
| 2/7(8) | 마스터 룰 | 전역 정규화 규칙 적용 중 | 항상 |
| 3/7(8) | Stage 2 | KB 벡터 검색 및 LLM Reranker 실행 중 | 항상 |
| 4/7(8) | Stage 3 | 케이스 기반 로그 재정제 중 | 항상 |
| 5/7(8) | Stage 4 | 문제 패턴 매칭 및 점수 산출 중 | 항상 |
| 6/7(8) | Stage 3/4 Fallback | 케이스 패턴 점수 낮음 — 전체 패턴으로 재시도 중 | HIT이지만 score < definite_threshold 시 |
| 7/7(8) | Stage 5 | LLM 진단 리포트 생성 중 | 항상 |
| 8/8 | Stage 6 — Reflection | LLM 자기 검증 중 | Reflection 활성화 시 |

완료 시 `st.status`를 `state="complete"`로 전환하고 판정 결과를 표시한다. 오류 발생 시 `state="error"`로 전환한다.

---

## Analysis Profile

분석 프로파일은 특정 도메인에 포커싱하여 분석 오류를 줄이고 정확도를 높이기 위한 단위이다. 분석 시 복수 프로파일을 선택할 수 있으며, 선택된 프로파일들의 구성 요소는 병합되어 파이프라인에 적용된다.

### 프로파일 구성 요소

| 항목 | 설명 |
|------|------|
| 분석 지침 | LLM에 전달할 도메인별 분석 가이드라인 (system prompt에 주입) |
| 사전정제 키워드 | Stage 1 로그 정제 시 사용할 whitelist 키워드 |
| 사전지식 참조 | 참조할 사전지식의 이름 목록 (JSON에 이름으로 저장, 내부 처리 시 ID로 변환) |

### 사전지식 저장소

| 저장소 | 적합한 지식 유형 |
|--------|-----------------|
| SQLite | 구조화된 정보 (디바이스 특성, 디바이스 간 관계, 스펙 등) |
| ChromaDB | 비구조화 문서 (기술 문서, 장애 분석 보고서, 매뉴얼 등) |

### 멀티 프로파일 병합 규칙

| 항목 | 병합 방식 |
|------|-----------|
| 분석 지침 | 프로파일 순서대로 연결 |
| 사전정제 키워드 | 합집합 (중복 제거) |
| 사전지식 참조 | 합집합 (중복 제거) |

### 파이프라인 적용 흐름

```
프로파일 선택 (복수 가능)
    │
    ├─ 병합 → 분석 지침 통합
    ├─ 병합 → 사전정제 키워드 합집합
    └─ 병합 → 사전지식 참조 합집합
          │
          ├─ SQLite 조회 → 구조화 지식
          └─ ChromaDB 검색 → 비구조화 지식
                │
                └─ 컨텍스트 조립 → Stage 2B, Stage 5에 주입
```

병합된 컨텍스트가 LLM의 `num_ctx`를 초과하는 경우, Context Strategy 설정에 따라 처리한다.

### 기본 분석 프로파일 (`_default.json`)

`config/profiles/_default.json` 파일이 존재하면 기본 분석 프로파일로 취급한다. 사용자가 분석 실행 시 프로파일을 선택하지 않은 경우에 자동으로 적용된다.

| 조건 | 동작 |
|------|------|
| `_default.json` 존재 | 기본 프로파일 적용 안내 팝업 → 확인 시 자동 적용 |
| `_default.json` 없음 | 분석 정확도 저하 경고 팝업 → 확인 시 프로파일 없이 진행 |

파일명 `_default`는 특수 예약명이며, Page 5 (분석 프로파일 관리) 일반 프로파일 목록과 동일한 JSON 스키마를 사용한다.

### 프로파일 미선택 팝업 흐름 (Page 1)

분석 실행 버튼 클릭 시 다음 순서로 검증한다.

```text
▶ 분석 실행 클릭
    │
    ├─ 로그 없음 → 오류 표시, 중단
    ├─ 문제 설명 없음 → 확인 팝업 (문제 설명 없이 진행 여부)
    │
    └─ 프로파일 미선택?
          ├─ _default.json 존재 → 기본 프로파일 적용 안내 팝업 (확인/취소)
          │       확인 → _default 프로파일 자동 적용 후 분석 실행
          └─ _default.json 없음 → 정확도 경고 팝업 (확인/취소)
                  확인 → 프로파일 없이 분석 실행
```

---

## Backend: `core/pipeline.py`

### Stage 1 — Common Log Refinement

| Step | 처리 | 설명 |
|------|------|------|
| 1-1 | 커널 로그 필터링 | 비커널 라인 제거 |
| 1-2 | 사전정제 키워드 필터링 | 프로파일 whitelist 키워드 + 임시 키워드 합집합으로 관련 라인만 보존 |
| 1-3 | 반복/버스트 collapse | 동일 라인 xN 축약 |
| 1-4 | 앵커 기반 시간 윈도우 추출 | 관심 구간만 추출 |
| 1-5 | 처리 조건 기반 파일 선별 | 조건에 부합하는 로그 파일만 선택 |

**Output** → `L_common`

### Stage 2 — KB Search

| Step | 처리 | 설명 |
|------|------|------|
| A | BGE-M3 Embedding | ChromaDB Top-K 검색 |
| B | Qwen3-14B LLM Reranker | `/no_think` + `format=json`, `relevance_score ≥ threshold` → HIT |

**Output** → `matched_case` | `MISS`

#### 케이스 직접 지정 (Pinned Case) — 선택

사용자가 Page 1 에서 특정 케이스를 지정한 경우 Stage 2 의 벡터 검색·Reranker 를 건너뛰고 지정 케이스를 그대로 `matched_case` 로 사용한다. 기존 자동 검색 동작은 유지되며, 지정은 순수 옵션이다.

| 경로 | 동작 | Observability `source` |
| ---- | ---- | ---------------------- |
| 자동 (기본) | 벡터 검색 → Reranker → HIT/MISS | `auto` |
| 지정 (이름 일치) | `load_case_by_name()` → `relevance_score=1.0`, `pinned=True` | `pinned` |
| 지정 (이름 불일치) | 자동 검색으로 Fallback | `pinned_not_found` → `auto_fallback` |

- 지정 케이스도 `profile_refs` 자동 병합 대상 (Stage 2 HIT 흐름과 동일)
- 지정 케이스도 Stage 3/4 Fallback 대상 (`score < definite_threshold` 시 전체 패턴 재시도)
- 결과 페이지(Page 2)는 `matched_case.pinned == True` 일 때 "📌 사용자 지정" 배지로 구분 표시

### Stage 3 — Case-Specific Log Refinement

`L_common` → candidate keywords로 재필터링

| 조건 | 처리 |
|------|------|
| HIT | 케이스 keywords → `L_refined` (1개) |
| MISS | 문제 패턴별 keywords → `L_refined` (패턴마다) |

**Output** → `L_refined`

### Stage 4 — Fault Pattern Matching

#### 4-0. 이벤트 정규화 (Fingerprinting)

PID / 주소 / IRQ 번호 등 가변 값 제거 → hash

#### 4-1. 문제 패턴 타입별 매칭

| 타입 | Dedup 옵션 |
|------|------------|
| PRESENCE | `event_dedup_window_sec` |
| SEQUENCE | `step_dedup`, `non_overlapping` |
| WINDOW | `count_unique_only` |
| ABSENCE | trigger → window 내 미출현 |
| COMPOSITE | AND / OR / NOT 조합 |

#### 4-2. 필수 문제 패턴 검증

`is_required` 미매칭 → 케이스 즉시 제외

#### 4-3. 점수 계산

```
score = Σ(weight × matched) / Σ(weight)
```

**Output** → `matched[]`, `unmatched[]`, `score`, `evidence`, 매칭된 패턴별 분석지침

### Stage 3/4 Fallback

Stage 4 완료 후 HIT 케이스가 존재하지만 `score < definite_threshold`인 경우, 케이스 keywords만으로 좁힌 로그가 부족했을 가능성이 있으므로 전체 패턴 경로로 재시도한다.

```text
Stage 4 완료
    │
    └─ matched_case 존재 AND score < definite_threshold?
          │
          YES → Stage 3 재실행 (MISS 경로 — 전체 패턴 keywords로 L_normalized 재필터링)
                  │
                  └─ fallback_result.score >= 원래 score?
                        ├─ YES → refined_entries, match_result 교체 + fallback_original_score 기록
                        └─ NO  → 원래 결과 유지 (fallback_original_score = None, 표시 안 함)
```

- `matched_case`는 fallback 후에도 유지 — Stage 5 리포트에 HIT 사실 포함
- `fallback_original_score`가 기록된 경우 리포트에 "케이스 패턴 점수 낮음(X%), 전체 패턴으로 재시도" 표시

### Stage 5 — Report Generation (Qwen3-14B)

매칭된 문제 패턴의 분석지침을 참조하여 리포트를 생성한다.

생성 항목:

- 판정 (문제 / 알 수 없음 / 불확실)
- 매칭 케이스/문제 패턴 근거
- 패턴별 분석지침에 따른 상세 분석
- evidence 로그 라인
- 권장 조치

**Output** → `report_md` (Markdown)

### Stage 6 — Reflection (Qwen3-14B)

Stage 5에서 생성된 리포트를 LLM으로 한 번 더 검증하는 자기 검증 단계.

검증 항목:

- 각 분석 항목에 evidence 로그 라인이 실제로 존재하는가
- 로그에 근거 없는 추측성 판단이 포함되어 있는가
- 판정이 score 및 매칭 결과와 일관되는가

검증 결과에 따라 추측성 항목을 제거하거나 별도 구분하여 최종 리포트를 출력한다.

**Output** → `report_final` (검증 완료된 Markdown)

---

## Observability

파이프라인 각 Stage의 판단 근거와 입출력을 로깅하여 분석 품질 추적 및 디버깅에 활용한다. 초기에는 단순 로깅 수준으로 구현하며, 필요 시 확장한다.

### 로깅 항목

| Stage | 로깅 내용 |
|-------|-----------|
| Stage 1 | 원본 라인 수, 정제 후 라인 수, 적용된 키워드 |
| Stage 2A | 검색 쿼리, Top-K 결과 및 유사도 점수 |
| Stage 2B | Reranker 입력/출력, relevance_score, HIT/MISS 판정 근거 |
| Stage 3 | 적용된 keywords, 정제 전후 라인 수 |
| Stage 4 | 매칭된 문제 패턴 목록, 각 패턴별 score, evidence 라인 |
| Stage 5 | LLM에 전송된 프롬프트 (템플릿 + 데이터), LLM 응답 원문 |
| Stage 6 | 검증 결과, 제거/구분된 추측성 항목 목록 |

로그는 SQLite `analysis_logs` 테이블에 분석 ID 기준으로 저장하며, 결과 페이지(Page 2)에서 상세 로그를 조회할 수 있다.

---

## Prompt Template

LLM 호출 시 사용하는 프롬프트 템플릿 구조. 시스템 분석 지침은 항상 적용되며, 프로파일별 지침과 분석 데이터는 파이프라인에서 동적으로 조립된다.

### 템플릿 구성

| 구간 | 내용 | 소스 |
|------|------|------|
| 시스템 분석 지침 | 모든 분석에 항상 적용되는 기본 규칙 | 설정 페이지(Page 6)에서 관리 |
| 프로파일별 분석 지침 | 선택된 프로파일의 도메인 특화 가이드라인 | 프로파일 JSON 파일 |
| 사전지식 컨텍스트 | 프로파일에서 참조한 구조화/비구조화 지식 | SQLite / ChromaDB |
| 분석 데이터 | 정제 로그, 매칭 결과, evidence 로그 라인, 매칭된 패턴별 분석지침 | 파이프라인 Stage 1~4 결과 |

### 시스템 분석 지침 (기본 규칙 예시)

- 로그에 직접적 근거가 없는 내용은 분석에 포함하지 않는다
- 추측성 판단은 별도 구분하여 표시한다
- 각 분석 항목에 반드시 evidence 로그 라인을 명시한다
- 판정은 제공된 score와 매칭 결과에 기반한다

### 조립 순서

```
[1] 시스템 분석 지침              ← 항상 고정
[2] 프로파일별 분석 지침     ← 선택된 프로파일에서 병합
[3] 매칭된 패턴별 분석지침   ← Stage 4 매칭 결과에서 수집
[4] 사전지식 컨텍스트        ← Context Strategy에 따라 처리
[5] 분석 데이터             ← 파이프라인 결과
```

시스템 분석 지침은 설정 페이지에서 수정할 수 있으며, Stage 2B (Reranker)와 Stage 5 (Report Generation)에서 각각 용도에 맞는 템플릿을 사용한다.

---

## Context Strategy

LLM 호출 시 컨텍스트 윈도우(`num_ctx`)를 초과하는 경우의 처리 전략.
`client.show(model_name)`으로 런타임에 `num_ctx`를 자동 조회하여 적용한다.

| 전략 | 방식 | 특징 |
|------|------|------|
| 우선순위 truncation | 우선순위 순으로 컨텍스트를 채우고, 한계 도달 시 나머지 버림 | 빠름, 구현 단순, 낮은 우선순위 지식 손실 가능 |
| 분할 전송 | 토큰 한계만큼 나눠서 여러 번 호출, 중간 결과를 이어받음 | 전체 지식 반영, 응답 느림, 중간 요약 시 미세 손실 |
| 혼합 | 기본 우선순위 truncation 적용, 잘리는 양이 임계 비율 초과 시 분할 전송 전환 | 대부분 빠르게 처리, 필요 시에만 분할 전송 |

설정 페이지(Page 6)에서 전략을 선택할 수 있으며, 혼합 모드의 전환 임계 비율도 조정 가능하다.

---

## Storage

| 컴포넌트 | 용도 |
|----------|------|
| ChromaDB | 케이스 description 벡터 (BGE-M3 Dense), 비구조화 사전지식 벡터 |
| SQLite | cases, patterns, case_patterns, domain_knowledge, noise_patterns, analysis_logs, history |
| JSON 파일 | 분석 프로파일 (`config/profiles/*.json`, 프로파일 1개 = 파일 1개) |
| Ollama | Qwen3-14B (Reranker + Report Generator) |