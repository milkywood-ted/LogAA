# Kernel Log Analyzer — Implementation Record

## 프로젝트 구조

```
LogAS_Home/
├── Architecture.md             # 시스템 설계 원본
├── implementation.md           # 이 파일
├── requirements.txt            # Python 패키지 목록
├── app.py                      # Streamlit 진입점
├── config/
│   ├── LLM/
│   │   └── config.yaml         # LLM / 임베딩 / 파이프라인 설정
│   ├── log_parsers.yaml        # 사전 정제 파서 정의 (코드 수정 없이 추가 가능)
│   └── patterns/
│       └── default_patterns.yaml  # 기본 패턴 정의 (seed 원본)
├── db/
│   └── loganalyzer.db          # SQLite (런타임 생성)
├── chroma_db/                  # ChromaDB 벡터 저장소 (런타임 생성)
├── sample_logs/
│   └── sample_kernel.log       # 테스트용 샘플 커널 로그 (cpu-id 포함, 111라인)
├── pages/
│   ├── 1_🔍_분석.py            # 로그 입력 + 분석 실행
│   ├── 2_📊_결과.py            # 판정 배너 + 패턴/로그/리포트
│   ├── 3_📚_KB관리.py          # 케이스 CRUD + 패턴 연결
│   ├── 4_🎯_패턴.py            # 패턴 CRUD + LLM 생성 + 수정/삭제
│   ├── 5_⚙️_설정.py           # 임계값 + 노이즈 + 이력
│   └── 5b_🔌_연결_확인.py      # LLM/임베딩 연결 테스트 + 모델 목록
└── core/
    ├── db.py
    ├── config.py               # config/LLM/config.yaml 로드/저장
    ├── llm.py                  # OpenAI 호환 chat/embed 헬퍼
    ├── log_loader.py
    ├── log_refiner.py          # Stage 1 + Stage 3
    ├── parser_registry.py      # log_parsers.yaml 로드 → PARSER_DEFS / PARSER_MAP
    ├── kb_search.py            # Stage 2
    ├── pattern_matcher.py      # Stage 4
    ├── report_generator.py     # Stage 5
    ├── reflection.py           # Stage 6 (Reflection — 리포트 자기 검증)
    ├── context_strategy.py     # LLM num_ctx 초과 시 컨텍스트 처리 전략
    ├── pipeline.py             # Stage 1→6 통합 실행기
    ├── master_rule.py          # 전역 로그 스트림 정규화 (Master Rule)
    ├── pattern_seeder.py
    └── pattern_generator.py
```

---

## 구현 완료

### `config/LLM/config.yaml` + `core/config.py` — 외부 설정 파일

모든 LLM 관련 설정을 소스 수정 없이 `config/LLM/config.yaml` 한 곳에서 관리한다.

```yaml
llm:
  base_url: "http://localhost:11434/v1"   # OpenAI 호환 엔드포인트
  api_key: "ollama"
  model: "qwen3:14b"
  max_tokens: null          # null = 모델 기본값. 응답이 잘리면 값 지정 (예: 8192)
  timeout: null             # null = 무제한. 느린 로컬 모델이면 초 단위 지정 (예: 120)
  report_temperature: 0.2   # 리포트 생성 temperature (0.0 = 결정론적, 1.0 = 창의적)

embedding:
  base_url: "http://localhost:11434/v1"
  api_key: "ollama"
  model: "bge-m3"

pipeline:
  kb_threshold: 0.70
  definite_threshold: 0.50
  max_log_lines: 200
  stage6_reflection_enabled: true   # false 로 설정하면 Stage 6 건너뜀
```

**`AppConfig` 주요 필드**

| 필드 | 설명 |
|---|---|
| `llm_max_tokens` | LLM 최대 출력 토큰. `None` = 모델 기본값 |
| `llm_timeout` | LLM 응답 타임아웃(초). `None` = 무제한 |
| `llm_report_temperature` | 리포트 생성 temperature (기본 0.2) |
| `stage6_reflection_enabled` | Stage 6 Reflection 활성화 여부 (기본 `True`) |
| `context_strategy` | num_ctx 초과 시 처리 전략. `"truncation"` \| `"split"` \| `"hybrid"` (기본 `"truncation"`) |
| `hybrid_overflow_ratio` | hybrid 모드에서 split 전환 임계 비율 (기본 `0.3` = 30%) |
| `num_ctx` | 저장된 모델 컨텍스트 윈도우 크기(토큰). `None` = 전략 비활성 |

**`core/config.py` 공개 API**

```python
cfg: AppConfig          # 모듈 임포트 시 자동 로드된 전역 설정 객체
save(config: AppConfig) # config/LLM/config.yaml 에 저장
```

---

### `core/llm.py` — OpenAI 호환 LLM/임베딩 헬퍼

`ollama` 라이브러리와 `transformers`/`torch` 의존성을 제거하고 `openai` 라이브러리로 단일화.  
Ollama, OpenAI, vLLM, LM Studio 등 OpenAI 호환 엔드포인트라면 `config.yaml` 변경만으로 전환 가능하다.

```python
chat(messages, json_mode, temperature) → str
embed(texts)                           → list[list[float]]
```

- `chat()`: `response_format=json_object` 지원 (Reranker·패턴 생성에 사용)
- `embed()`: 입력 순서 보장 (`index` 기준 정렬)

---

### `core/db.py` — SQLite 스키마 & 커넥션

**테이블**

| 테이블 | 용도 |
|---|---|
| `patterns` | 패턴 정의. 5가지 타입 전용 컬럼 포함 |
| `pattern_steps` | SEQUENCE 단계 (patterns CASCADE 삭제) |
| `pattern_components` | COMPOSITE 참조 (patterns CASCADE 삭제) |
| `cases` | KB 케이스. `description` 이 임베딩 대상 |
| `case_patterns` | cases ↔ patterns 다대다 |
| `noise_patterns` | 노이즈 필터 regex |
| `master_rules` | 전역 로그 스트림 정규화 규칙 (Master Rule) |
| `history` | 분석 이력 (input_hash, result JSON) |

**공개 API**

```python
init_db(db_path)   # 스키마 생성 (IF NOT EXISTS)
get_conn(db_path)  # context manager — commit/rollback 자동 처리
```

---

### `core/log_loader.py` — 파일/폴더 로딩

파일 또는 폴더 경로를 받아 `{절대경로: 내용}` 딕셔너리를 반환한다.

**특징**
- 파일 확장자 제약 없음
- 인코딩 자동 감지 (`chardet`) — 신뢰도 미달 시 UTF-8 fallback
- 파일 크기에 따라 읽기 방식 자동 선택
  - `≤ LOG_LOADER_STREAM_THRESHOLD_MB` : `read_bytes()` 전체 읽기
  - 초과 : 앞부분 64KB 샘플로 인코딩 감지 → `open()` 라인 순회
- 환경 변수 `LOG_LOADER_STREAM_THRESHOLD_MB` 로 임계값 설정 (기본 50 MB)
- null byte 감지 → 바이너리 파일 자동 제외

**공개 API**

```python
load_inputs(inputs, recursive, encoding_confidence)  # 파일+폴더 혼합 목록
load_files(paths, encoding_confidence)               # 파일 경로 목록만
load_folder(folder, recursive, encoding_confidence)  # 폴더 단독
```

---

### `core/log_refiner.py` — Stage 1 + Stage 3

#### Stage 1 — Common Log Refinement

**전체 실행 순서 (파이프라인에서 호출)**

```
raw_logs
  │
  ▼ [0단계]  prefilter_by_keywords(raw_logs, input_keywords)
  │           OR 기반 파일 선별 + 라인 추출 (키워드 지정 시만 실행)
  │
  ▼ [1-4]    LogRefiner.select_files(raw_logs, file_conditions)
  │           AND 기반 파일 선별 (file_conditions 지정 시만 실행)
  │
  ▼ [1-1]    LogRefiner._parse_and_filter  — 파일별 실행
  │           dmesg / syslog / journalctl 포맷 파싱, 비커널 라인 제거
  │
  ▼ [1-2]    LogRefiner._collapse
  │           repeat marker 흡수 → consecutive dedup → burst collapse
  │
  ▼ [1-3]    LogRefiner._apply_window
  │           시간 구간 필터 (우선순위: ts 직접 지정 > 앵커 > 미적용)
  │
  ▼ L_common (타임스탬프 순 병합)
```

`refine(raw_log, config) → list[LogLine]` — 파일 1개에 대해 1-1~1-3 실행  
`select_files(files, conditions) → dict[str, str]` — 1-4 파일 선별  
`prefilter_by_keywords(raw_logs, keywords) → dict[str, str]` — 0단계 전처리

**설정 (`RefineConfig`)**

| 필드 | 기본값 | 설명 |
|---|---|---|
| `input_keywords` | `[]` | 0단계 OR 필터. 파일 선별 + 라인 추출. 비어 있으면 미적용 |
| `file_conditions` | `[]` | 1-4 AND 파일 선별 regex 목록. 비어 있으면 전체 파일 통과 |
| `anchors` | `[]` | 1-3 관심 이벤트 regex 목록 |
| `window_before_sec` | 30.0 | 앵커 이전 포함 구간 (초) |
| `window_after_sec` | 30.0 | 앵커 이후 포함 구간 (초) |
| `ts_start` | `None` | 1-3 커널 uptime 직접 지정 구간 시작. None 이면 하한 없음 |
| `ts_end` | `None` | 1-3 커널 uptime 직접 지정 구간 종료. None 이면 상한 없음 |
| `burst_window_sec` | 1.0 | burst 판단 슬라이딩 윈도우 (초) |
| `burst_threshold` | 5 | burst collapse 최소 횟수 |

**1-3 `_apply_window` 우선순위**
1. `ts_start` 또는 `ts_end` 중 하나라도 지정 → 직접 지정 범위 사용 (anchors 무시)
2. `anchors` 지정 → 매칭 타임스탬프 기준 window_before/after 적용
3. 아무것도 없으면 전체 반환

**`prefilter_by_keywords` 동작**
1. 파일 선별: keywords 중 하나라도 파일 전체 내용에 포함된 파일만 통과 (OR, case-insensitive)
2. 라인 추출: 통과된 파일에서 keywords 중 하나라도 포함된 라인만 남김 (OR, case-insensitive)
- `input_keywords`가 비어 있으면 원본 그대로 반환

#### Stage 3 — Case-Specific Log Refinement

`L_common → keyword 필터링 → L_refined`

```python
refine_for_case(l_common, matched_case) → list[RefinedEntry]   # HIT
refine_for_patterns(l_common, patterns) → list[RefinedEntry]   # MISS
```

| 경로 | 동작 | 출력 수 |
|---|---|---|
| HIT | `matched_case.keywords` 로 필터 → 케이스의 모든 패턴이 공유 | 케이스 패턴 수만큼 |
| MISS | 패턴별 `keywords` 로 각각 필터, 빈 결과 제외 | 매칭된 패턴 수만큼 |

**`RefinedEntry`** — Stage 4 처리 단위

```python
@dataclass
class RefinedEntry:
    pattern: dict        # Stage 4 에서 매칭할 패턴
    lines: list[LogLine] # 해당 패턴 전용 L_refined
```

---

### `core/db.py` + `patterns/default_patterns.yaml` + `core/pattern_seeder.py` — 패턴 저장소

#### `default_patterns.yaml` — 기본 패턴 정의 (11개)

5가지 타입 예시 포함:

| 타입 | 예시 패턴 |
|---|---|
| PRESENCE | 커널 패닉, OOM Killer, EXT4 오류 |
| SEQUENCE | ATA 리셋 시퀀스, NVMe 초기화 실패 |
| WINDOW | 단시간 다수 ATA 오류, 단시간 다수 I/O 오류 |
| ABSENCE | ATA EH 미완료, 디스크 언마운트 미완료 |
| COMPOSITE | ATA 복합 장애 (AND), 스토리지 심각 장애 (OR) |

#### `core/pattern_seeder.py`

```python
load_yaml(yaml_path) → list[AnyPattern]   # 파싱 + pydantic 검증 (DB 미접촉)
seed(yaml_path, db_path) → int            # 테이블 비어있을 때만 삽입 (최초 1회)
reset(yaml_path, db_path) → int           # 전체 삭제 후 재삽입 (기본값 복구)
```

**검증 항목**: 타입별 필수 필드 / COMPOSITE NOT 단일 component / 미정의 참조 / 순환 참조 (DFS)

---

### `core/pattern_generator.py` — 자연어 → 패턴 생성

`PatternGenerator.generate(text) → GenerationResult`

**2단계 구조**

```
1단계: _fetch_context(text)
  키워드 기반으로 관련 기존 패턴 필터 (관련 없으면 최대 20개 fallback)
  → context_patterns (상세), all_names (이름 목록)

2단계: _call_with_retry(...)  최대 3회
  llm.chat(json_mode=True) 호출
  → 패턴 구조 생성 + 기존 패턴과의 관계 분석
  → pydantic 검증 + COMPOSITE 참조 무결성 확인
  → 실패 시 오류 메시지 포함해 재시도
```

**`GenerationResult`**

```python
@dataclass
class GenerationResult:
    pattern: AnyPattern       # 생성된 패턴 (검증 완료)
    relations: list[Relation] # 기존 패턴과의 관계
    context_patterns: list[dict]
```

**관계 유형**: `references` / `similar` / `extends` / `subset` / `conflicts` / `complement`

---

### `core/kb_search.py` — Stage 2 KB Search

`KBSearch.search(problem_text) → MatchedCase | None`

**Step A — 벡터 검색**
- `llm.embed()` → ChromaDB cosine 유사도 Top-K

**Step B — LLM Reranker**
- 후보 전체를 한 번에 평가 (K번 호출 대신 1번)
- `llm.chat(json_mode=True, temperature=0)`
- `relevance_score ≥ threshold` 중 최고 점수 → `MatchedCase`
- 전부 미달 → `None` (MISS)

**`MatchedCase`**

```python
@dataclass
class MatchedCase:
    case_id: int
    name: str
    description: str
    keywords: list[str]
    relevance_score: float
    patterns: list[dict]   # Stage 3/4 에서 사용할 패턴 목록
```

**KB 관리 API**

```python
add_case(case_id, name, description, keywords)  # ChromaDB upsert
remove_case(case_id)                            # ChromaDB 삭제
sync_from_db()                                  # SQLite → ChromaDB 전체 동기화
```

---

### `core/pattern_matcher.py` — Stage 4 Pattern Matching

`PatternMatcher.match_entries(entries) → MatchResult`

| 타입 | 매칭 방식 |
|---|---|
| PRESENCE | regex + `event_dedup_window_sec` (fingerprint 기반 중복 무시) |
| SEQUENCE | 단일 상태 기계, `step_dedup`, `non_overlapping` (barrier 설정) |
| WINDOW | 슬라이딩 윈도우, `count_unique_only` |
| ABSENCE | IDLE↔WATCHING 상태 기계 |
| COMPOSITE | non-COMPOSITE 결과 수집 후 AND/OR/NOT 계산 |

- 실행 순서: non-COMPOSITE 일괄 → COMPOSITE 처리
- Score: `Σ(weight×matched) / Σ(weight)`

---

### `core/report_generator.py` — Stage 5 Report Generation

`ReportGenerator.generate(...) → ReportResult`

| 판정 | 조건 | LLM 처리 |
|---|---|---|
| 문제 | score ≥ `definite_threshold` | 케이스/패턴/evidence 기반 구조화 리포트 |
| 불확실 | 부분 매칭 (score 낮음) | 부분 매칭 정보 포함 불확실 리포트 |
| 알 수 없음 | 매칭 패턴 없음 | L_common 직접 분석 + `PatternGenerator` KB 추가 제안 |

#### 프롬프트 조립 순서

```
[1] 시스템 분석 지침         ← cfg.system_analysis_guidelines (항상 고정)
[2] 프로파일별 분석 지침     ← 선택된 프로파일에서 병합
[3] 매칭된 패턴별 분석지침   ← Stage 4 매칭 결과에서 수집
[4] 사전지식 컨텍스트        ← SQLite / ChromaDB
[5] 분석 데이터             ← 정제 로그, 매칭 결과, evidence
```

#### `ReportResult`

```python
@dataclass
class ReportResult:
    verdict: str                            # "문제" | "불확실" | "알 수 없음"
    report_md: str                          # Markdown 리포트
    kb_suggestion: GenerationResult | None  # 알 수 없음 경로만
```

---

### `core/context_strategy.py` — Context Strategy

LLM 호출 프롬프트가 모델의 `num_ctx`(컨텍스트 윈도우)를 초과할 때 적용하는 처리 전략 모듈.  
`ReportGenerator` 내부에서 사용되며, `num_ctx` 미설정 시 전략을 적용하지 않는다.

#### 토큰 추정

```python
CHARS_PER_TOKEN = 3   # 한국어/영어 혼합 기준 보수적 추정
OVERHEAD_TOKENS = 4_000   # 프롬프트 구조 + 출력 버퍼 예약

estimate_tokens(text: str) → int   # len(text) // 3
```

#### 전략 목록

| 전략 | 방식 | 특징 |
|---|---|---|
| `truncation` | 우선순위 순으로 채우고 초과분 버림 | 빠름, 낮은 우선순위 지식 손실 가능 |
| `split` | `knowledge_context` 를 청크 분할, 여러 번 호출 후 이어받음 | 느림, 전체 지식 반영 |
| `hybrid` | 기본 truncation, overflow 비율 ≥ 임계값이면 split 전환 | 대부분 빠르게 처리, 필요 시만 분할 전송 |

#### 우선순위 (truncation 시)

```
1. system_guidelines   — 항상 최우선 포함
2. analysis_guidelines
3. knowledge_context   — 가장 먼저 잘림
```

#### 공개 API

```python
truncate_context(sg, ag, kc, fixed_tokens, num_ctx) → ContextResult
# 우선순위 순서로 context 를 채우고 초과분 버림
# ContextResult: system_guidelines, analysis_guidelines, knowledge_context, was_truncated, truncated_tokens

split_knowledge_chunks(kc, sg, ag, fixed_tokens, num_ctx) → list[str]
# knowledge_context 를 num_ctx 에 맞게 분할
# 분할 불필요하면 원본 단일 청크 반환

calc_overflow_ratio(sg, ag, kc, fixed_tokens, num_ctx) → float
# knowledge_context 에서 잘리는 비율 (0.0~1.0)
```

#### `ReportGenerator` 통합

```
generate() 호출
  │
  ▼ _apply_context_strategy(sg, ag, kc, verdict, match_result, l_common)
  │   num_ctx 미설정: 원본 반환 (전략 미적용)
  │   truncation: truncate_context() → 잘린 (sg, ag, kc) 반환
  │   hybrid(overflow < 임계값): truncation 경로
  │   split / hybrid(overflow ≥ 임계값): 원본 반환 (_generate_report 에서 split 처리)
  │
  ▼ _generate_report(verdict, ..., sg, ag, kc, ...)
  │   split 불필요(청크 1개 이하): _build_prompt() → 단일 LLM 호출
  │   split 필요:
  │     chunk_1 → _build_prompt() → 초기 리포트
  │     chunk_2..N → _prompt_refine() → 순차 보완 ("이전 리포트 + 추가 지식")
  └─ 최종 report_md 반환
```

**`_estimate_fixed_tokens`**: verdict 별 고정 데이터(evidence/로그/프롬프트 골격) 토큰 추정

- `"알 수 없음"`: `render_lines(l_common[:max_log_lines])` 토큰 + 300
- `"문제"` / `"불확실"`: evidence + 패턴별 분석지침 토큰 + 300

---

### `core/reflection.py` — Stage 6 Reflection

`Reflector.reflect(...) → ReflectionResult`

Stage 5 리포트를 LLM 으로 한 번 더 검증하는 자기 검증 단계.

#### 검증 기준 (프롬프트 내 명시)

| 항목 | 처리 |
|---|---|
| evidence 존재 여부 | 실제 매칭 로그 라인에 근거 없는 항목 → `[근거 없음]` 접두어 또는 제거 |
| 추측성 판단 | 로그에 직접 근거 없는 추론 → `[추정]` 접두어 또는 별도 섹션 이동 |
| 판정-score 일관성 | 판정 레이블은 유지하고 근거 설명만 수정 |

#### LLM 응답 파싱 구조

```
### REFLECTION_NOTES
수정하거나 제거한 항목 목록 (변경 없으면 "변경 없음")

### REPORT_FINAL
검증 완료된 최종 Markdown 리포트
```

파싱 실패 시 원본 리포트(`fallback`)를 그대로 반환하여 Stage 5 결과를 보존한다.

#### `ReflectionResult`

```python
@dataclass
class ReflectionResult:
    report_final: str   # 검증 완료된 Markdown (Stage 5 원본 또는 수정본)
    notes: str          # 수정 내역 요약 ("변경 없음" 포함)
```

#### 활성화/비활성화

- `Pipeline.__init__(reflect=None)` → `None` 이면 `cfg.stage6_reflection_enabled` 사용
- 설정 페이지 "Stage 6 Reflection 활성화" 체크박스로 제어
- 비활성화 시 Stage 5 리포트가 최종 출력, 총 진행 단계 6→7로 변경되지 않음
- 활성화 시 LLM 호출 1회 추가됨

---

### `core/master_rule.py` — 전역 로그 스트림 정규화 (Master Rule)

Stage 1(L_common) 출력에 적용되어 Stage 3 입력(L_normalized)을 만드는 전처리 레이어.

**지원 rule_type**

| rule_type | 동작 |
|---|---|
| `DEDUP_CONSECUTIVE` | 같은 pattern 에 연속으로 매칭되는 라인을 첫 번째 1개로 축약. count 누산. |

**예시**

```
rule: pattern="Mute", rule_type=DEDUP_CONSECUTIVE
입력: Mute → Mute → Mute → Unmute
출력: Mute(×3) → Unmute   ← 정상 흐름으로 인식

rule: pattern="Unmute", rule_type=DEDUP_CONSECUTIVE
입력: Mute → Unmute → Unmute
출력: Mute → Unmute(×2)   ← 정상 흐름으로 인식
```

**공개 API**

```python
apply(lines, rules) → list[LogLine]            # 룰 목록 순서대로 적용
load_rules(db_path) → list[dict]               # DB 에서 룰 목록 로드
MasterRuleGenerator(db_path).generate(text)    # 자연어 → RuleGenerationResult
```

**`MasterRuleGenerator.generate(text)`**

- 기존 등록 룰을 컨텍스트로 LLM 에 전달 (중복 생성 방지)
- LLM 이 `name`, `rule_type`, `pattern(regex)`, `comment`, `explanation` 생성
- regex 컴파일 검증 + 지원 rule_type 확인 후 반환
- 실패 시 오류 메시지 포함 재시도 (최대 3회)

**`RuleGenerationResult`**

```python
@dataclass
class RuleGenerationResult:
    name: str
    rule_type: str      # 현재 항상 "DEDUP_CONSECUTIVE"
    pattern: str        # regex
    comment: str
    explanation: str    # 생성 근거 + 예상 동작 (UI 표시용)
```

---

### `core/pipeline.py` — Stage 1→5 통합 실행기

`Pipeline.run(problem_text, raw_logs, config) → PipelineResult`

**실행 흐름**

```
raw_logs (dict[str, str])
  │
  ▼ Stage 1  [0단계] prefilter_by_keywords (input_keywords OR 필터)
  │          [1-4]   LogRefiner.select_files (file_conditions AND 필터)
  │          [1-1~3] LogRefiner.refine (파일별 실행 → 타임스탬프 순 병합)
  │          → L_common, selected_logs (UI 표시용 선별된 파일 목록)
  │
  ▼ Master Rule  apply(l_common, rules) → L_normalized
  │              (DEDUP_CONSECUTIVE 등 전역 정규화 적용)
  │
  ▼ Stage 2  KBSearch.search(problem_text)
  │
  ├─ HIT: MatchedCase  → Stage 3 refine_for_case(L_normalized)
  └─ MISS: None        → Stage 3 refine_for_patterns(L_normalized, 전체 패턴)
  │
  ▼ Stage 4  PatternMatcher.match_entries → MatchResult
  │
  ▼ Stage 5  ReportGenerator.generate → ReportResult
  │
  ▼ history 저장 (input_hash, JSON payload)
```

**`PipelineResult`**

```python
@dataclass
class PipelineResult:
    verdict: str                     # "문제" | "불확실" | "알 수 없음"
    report_md: str
    l_common: list[LogLine]          # Stage 1 원본 출력
    l_normalized: list[LogLine]      # Master Rule 적용 후 (Stage 3 입력)
    selected_logs: dict[str, str]    # 1-4 선별 후 실제 처리된 파일 목록 (UI 표시용)
    matched_case: MatchedCase | None
    refined_entries: list[RefinedEntry]
    match_result: MatchResult | None
    kb_suggestion: GenerationResult | None   # 알 수 없음 경로만
    reflection_notes: str                    # Stage 6 수정 내역 ("" = 비활성 또는 변경 없음)
    history_id: int | None
```

---

### Streamlit UI — `app.py` + `pages/`

**진입점 `app.py`**
- `init_db()` + `seed()` (최초 1회 DB 초기화)
- `st.cache_resource` 로 부트스트랩 처리

**Page 1 — 🔍 분석 (`pages/1_🔍_분석.py`)**
- 문제 설명 텍스트 입력
- 로그 입력 3가지 방식:
  - 파일 업로드 (다중 파일)
  - 로컬 경로 (파일 또는 폴더, recursive 선택 가능)
  - 텍스트 직접 입력
- **키워드 필터** (로그 입력 섹션 하단):
  - 쉼표 구분 키워드 입력 → `prefilter_by_keywords` 0단계 실행
  - 실시간 미리보기: "N개 파일 → M개 파일 / X줄 → Y줄"
- **⚙️ 고급 설정 (Stage 1)** expander:
  - **파일 선별 조건 (1-4)**: AND 조건 regex 목록 (줄 단위 입력)
  - **시간 구간 필터 방식** 라디오: 필터 없음 / 앵커 패턴 / 타임스탬프 직접 지정
    - 앵커 패턴: regex 목록 + window_before/after (초)
    - 타임스탬프 직접 지정: 커널 uptime 범위 (ts_start, ts_end) 텍스트 입력
  - **Burst collapse 임계값**
- 분석 실행 → `PipelineResult` 를 `st.session_state["last_result"]` 에 저장
- 분석 완료 후 파일 선별 알림: "N개 중 M개 사용 (K개 조건 미충족으로 제외)"
- 간략 요약 카드 (점수, 매칭/미매칭 패턴 수)

**Page 2 — 📊 결과 (`pages/2_📊_결과.py`)**
- 판정 배너 (색상 구분: 🔴문제 / 🟡불확실 / ⚪알 수 없음)
- 4탭 구성:
  - 📝 리포트: LLM 생성 Markdown 렌더링. Stage 6 수정 내역이 있으면 "🔍 Stage 6 검증 노트" expander 추가 표시
  - 🎯 패턴 상세: 매칭/미매칭 패턴 + evidence 로그
  - 📋 정제 로그: L_common 전체 텍스트
  - 💡 KB 제안: 알 수 없음 경로의 GenerationResult (JSON + 관계 목록)

**Page 3 — 📚 KB관리 (`pages/3_📚_KB관리.py`)**
- 케이스 목록 (expander, 연결 패턴 표시)
- 케이스 추가/수정/삭제 (ChromaDB 자동 동기화)
- 패턴 연결/해제 (multiselect)
- 사이드바: ChromaDB 전체 동기화 버튼

**Page 4 — 🎯 패턴 (`pages/4_🎯_패턴.py`)**
- 타입 필터링된 패턴 목록 (타입별 추가 필드 표시)
- 패턴 추가: 자연어 → LLM 생성 (preview + 저장) 또는 직접 입력 폼
- 사이드바: default_patterns.yaml 재시드 (기본값 초기화)

---

**Page 6 — ⚙️ 설정 (`pages/6_⚙️_설정.py`)**

- 저장 완료 시 상단 `st.success` 배너 표시 (session_state 경유 — rerun 후에도 1회 유지)
- 4탭 구성:
  - **LLM**: LLM/임베딩 프로파일 선택·모델 조회·연결 확인 + 파이프라인 임계값 + Stage 6 토글 + 컨텍스트 전략 + 시스템 분석 지침
  - 마스터 룰: 등록 룰 목록 + 자연어 생성(메인) + 직접 입력(보조, expander)
  - 노이즈 패턴: regex CRUD (DB `noise_patterns` 테이블)
  - 분석 이력: 최근 50건 조회, 개별/전체 삭제

#### num_ctx 조회 및 최대 로그 라인 수 권장값 계산

파이프라인 임계값 섹션에 **num_ctx 조회** 버튼을 제공한다.  
OpenAI API 스펙에 컨텍스트 윈도우 크기 필드가 없으므로, 아래 세 전략을 순서대로 시도한다.

| 전략 | 대상 | 방법 |
|---|---|---|
| 1 | Ollama | `POST {base}/api/show` → `model_info.*.context_length` 또는 `parameters.num_ctx` |
| 2 | vLLM / LM Studio 등 | `GET /v1/models/{model}` → `max_model_len` / `context_length` / `max_context_length` |
| 3 | OpenAI / Anthropic 공개 모델 | `_KNOWN_CONTEXT_WINDOWS` 하드코딩 룩업 |

결과 info 박스에 출처(`Ollama /api/show`, `/v1/models (max_model_len)`, `알려진 모델 목록 (gpt-4o)` 등)를 함께 표시한다.

**권장값 계산 (`_suggest_max_log_lines`)**

```
available = num_ctx - 4000   # 프롬프트 오버헤드·출력 버퍼 예약
suggested = clamp(available // 30, 50, 2000)   # 로그 라인당 평균 30 토큰
```

조회 성공 시 `st.session_state["_suggested_max_log"]`에 저장 → 폼의 `max_log_lines` 입력 필드에 자동 반영.  
저장 버튼 클릭 후 session_state 제안값 초기화.

**마스터 룰 자연어 추가 흐름**

```
1. 텍스트 영역에 정규화 요구사항을 자유롭게 입력
   예) "Mute 커맨드가 연속으로 중복 수신되는 경우 하나로 취급해야 해"

2. [✨ 룰 생성] 버튼 클릭
   → MasterRuleGenerator.generate() 호출 (spinner 표시)
   → 생성된 룰 미리보기 (이름, rule_type, regex, 설명, 근거)

3. 미리보기에서 이름/패턴/설명 수정 후 [저장] 또는 [취소]
   → DB 저장 후 룰 목록 갱신
```

**Page 5b — 🔌 연결 확인 (`pages/5b_🔌_연결_확인.py`)**
- 현재 config.yaml 설정 요약 표시
- LLM / 임베딩 엔드포인트 모델 목록 조회 (`/v1/models`), 현재 사용 중 모델 ✅ 표시
- LLM 테스트: 커스텀 프롬프트로 채팅 완성 요청, 응답 + 소요 시간 표시
- 임베딩 테스트: 짧은 텍스트로 임베딩 요청, 벡터 차원 + 첫 5개 값 표시
- LLM/임베딩이 같은 엔드포인트면 모델 목록 중복 조회 생략

---

## 주요 설계 결정 사항

| 항목 | 결정 | 근거 |
|---|---|---|
| LLM 클라이언트 | `openai` 라이브러리 (OpenAI 호환 API) | Ollama·OpenAI·vLLM 등 엔드포인트 무관하게 동일 코드 사용 가능 |
| 임베딩 방식 | OpenAI 호환 임베딩 API | `transformers`/`torch` 제거 → `_lzma` 빌드 문제 및 느린 초기 로딩 해소 |
| LLM 설정 관리 | `config.yaml` 외부 파일 | 소스 수정 없이 모델·엔드포인트 교체 가능 |
| 패턴 저장 구조 | YAML seed + SQLite 운영 | 버전 관리 + 런타임 CRUD 분리 |
| Stage 4 확장성 | A (if/elif, POC 우선) | POC 검증 후 레지스트리 패턴(B)으로 전환 예정 |
| LLM 호출 방식 | Reranker 후보 일괄 평가 | K번 개별 호출 대신 1번 일괄로 효율화 |
| 파일 읽기 전략 | 크기 기반 자동 선택 | 소형 bulk / 대형 streaming |
| Master Rule 레이어 | Stage 1 → Master Rule → Stage 3 독립 삽입 | 중복 커맨드(Mute→Mute)를 단일 이벤트로 정규화, 패턴 매칭 품질 개선 |
| Master Rule 저장 | DB `master_rules` 테이블 + UI CRUD | 소스 수정 없이 정규화 규칙 추가/삭제 가능 |
