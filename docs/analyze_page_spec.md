# 분석 페이지 UI / 기능 명세

> 파일: `AnlayzingAssistant/pages/1_🔍_분석.py` (Streamlit)  
> 작성일: 2026-05-13

---

## 전체 구조 (화면 순서)

```
1. 분석 프로파일 선택 (multiselect)
   └─ 케이스 직접 지정 (expander)
2. 문제 설명 (textarea)
3. 로그 입력 (radio → 3가지 모드)
   └─ 임시 키워드 필터
   └─ 고급 설정 / Stage 1 옵션 (expander)
4. 분석 실행 버튼
5. 선별 및 필터만 진행 버튼
6. 최근 분석 결과 요약 (분석 완료 후 표시)
```

---

## 1. 분석 프로파일 선택

| 항목 | 내용 |
| ---- | ---- |
| UI | `st.multiselect` |
| 데이터 소스 | `load_profiles(PROFILES_DIR)` — yaml 파일 기반 |
| 복수 선택 | 선택된 프로파일을 `merge_profiles()`로 병합 |
| 선택 시 표시 | 사전정제 키워드 목록 caption / 병합된 분석 지침 미리보기(expander, 최대 500자) |
| 미선택 시 | 기본 프로파일(`_default.json`) 자동 적용 여부 확인 → 없으면 프로파일 없이 진행 확인 |
| Pipeline 연결 | `merged_profile` → `Pipeline.run(merged_profile=...)` |

### 1-B. 케이스 직접 지정 (expander, 기본 접힘)

| 항목 | 내용 |
| ---- | ---- |
| UI | `st.selectbox` |
| 데이터 소스 | DB `cases` 테이블 (`SELECT name FROM cases`) |
| 선택 시 표시 | 케이스 description(300자), 연결 패턴 수, 키워드 목록 |
| 효과 | Stage 2 벡터 검색 + Reranker 스킵 → 지정 케이스로 바로 Stage 3~5 진행 |
| Pipeline 연결 | `pinned_case_name` → `Pipeline.run(pinned_case_name=...)` |

---

## 2. 문제 설명

| 항목 | 내용 |
| ---- | ---- |
| UI | `st.text_area`, height=100 |
| 미입력 시 | 분석 실행 시 확인 다이얼로그 표시 (진행 / 취소) |
| Pipeline 연결 | `problem_text` → `Pipeline.run(problem_text=...)` |

---

## 3. 로그 입력

`st.radio`로 3가지 모드 선택.

### 모드 ① 파일 업로드 (기본)

| 항목 | 내용 |
| ---- | ---- |
| UI | `st.file_uploader`, 다중 파일 허용 |
| 처리 | UTF-8 decode (`errors="replace"`) |
| 재사용 | 이전 업로드 파일을 `session_state["input_raw_logs"]`에 보관, 재접속 시 재사용 |

### 모드 ② 로컬 경로

| 항목 | 내용 |
| ---- | ---- |
| UI | 경로 text_input + recursive 체크박스 |
| 처리 | 파일: `load_files([path])` / 폴더: `load_folder(path, recursive=)` |
| 오류 표시 | 경로 없음 `st.error` |

### 모드 ③ 텍스트 직접 입력

| 항목 | 내용 |
| ---- | ---- |
| UI | `st.text_area`, height=300 |
| 처리 | `{"input.log": text}` 형태로 `raw_logs`에 저장 |

### 임시 키워드 필터

| 항목 | 내용 |
| ---- | ---- |
| UI | text_input, 쉼표 구분 |
| 실시간 미리보기 | `prefilter_by_keywords(raw_logs, keywords)` 호출 → 파일 수·라인 수 예상 결과 표시 |
| Pipeline 연결 | `input_keywords` → `RefineConfig(input_keywords=...)` |

---

## 4. 고급 설정 / Stage 1 옵션 (expander, 기본 접힘)

### 4-1. 사전 정제 옵션 (Stage 1-1)

| 항목 | 내용 |
| ---- | ---- |
| UI | 파서별 체크박스 (동적 생성) |
| 데이터 소스 | `PARSER_DEFS` from `core.parser_registry` |
| 효과 | 체크된 포맷의 라인만 추출 / 모두 해제 시 전체 라인 사용 |
| Pipeline 연결 | `active_parsers` → `RefineConfig(active_parsers=...)` |

### 4-2. 파일 선별 조건 (Stage 1-4)

| 항목 | 내용 |
| ---- | ---- |
| UI | textarea, 줄 단위 regex 입력 |
| 조건 | AND — 모든 패턴을 포함하는 파일만 통과 |
| Pipeline 연결 | `file_conditions` → `RefineConfig(file_conditions=...)` |

### 4-3. 시간 구간 필터 (Stage 1-3)

`st.radio`로 3가지 방식 선택.

| 방식 | UI | 연결 |
| ---- | -- | ---- |
| 필터 없음 | — | 전체 로그 사용 |
| 앵커 패턴 | textarea (regex, 줄 단위) + `window_before`(초) + `window_after`(초) number_input | `anchors`, `window_before_sec`, `window_after_sec` → `RefineConfig` |
| 타임스탬프 직접 지정 | `ts_start` / `ts_end` text_input (커널 uptime 초 단위) | `ts_start`, `ts_end` → `RefineConfig` |

### 4-4. Burst Collapse 임계값

| 항목 | 내용 |
| ---- | ---- |
| UI | `st.number_input`, min=1, default=5 |
| 효과 | 동일 fingerprint가 임계값 이상 반복 시 1개로 축약 |
| Pipeline 연결 | `burst_threshold` → `RefineConfig(burst_threshold=...)` |

---

## 5. 분석 실행

### 유효성 검사 흐름

```
로그 없음 → 에러 표시, 중단
문제 설명 없음 → 확인 다이얼로그 (진행 / 취소)
프로파일 미선택 + _default.json 있음 → 기본 프로파일 적용 확인
프로파일 미선택 + _default.json 없음 → 프로파일 없이 진행 확인
```

### Pipeline 실행

```python
RefineConfig(
    input_keywords, file_conditions,
    anchors, window_before_sec, window_after_sec,
    ts_start, ts_end,
    burst_threshold, active_parsers,
)

Pipeline.run(
    problem_text, raw_logs, config=RefineConfig,
    merged_profile, on_progress, pinned_case_name,
)
```

| 항목 | 내용 |
| ---- | ---- |
| 진행 표시 | `st.status` + `st.progress` + `st.empty` (step별 실시간 업데이트) |
| 완료 | 판정(verdict) + 아이콘(`🔴문제` / `🟡불확실` / `⚪알 수 없음`) 표시 |
| 결과 저장 | `session_state["last_result"]`, `session_state["problem_text"]` |
| 파일 선별 알림 | 원본 대비 제외된 파일 수 `st.warning` |
| 결과 확인 안내 | "📊 결과 페이지에서 확인하세요" `st.info` |

---

## 6. 선별 및 필터만 진행

LLM을 호출하지 않고 Stage 1 필터링만 수행.

| 항목 | 내용 |
| ---- | ---- |
| UI | `st.button` (secondary) |
| 처리 | `prefilter_by_keywords` → `LogRefiner.select_files(file_conditions)` |
| 출력 | 원본 파일 수 → 선별 후 파일 수, 선별된 파일 목록(파일명·라인 수) |

---

## 7. 최근 분석 결과 요약

분석 완료 후 `session_state["last_result"]`가 존재하면 페이지 하단에 표시.

| UI 항목 | 내용 |
| ------- | ---- |
| 4열 metric | 판정 / 진단 점수(`score`) / 매칭 패턴 수 / 매칭 케이스명 |
| 매칭 패턴 목록 | 패턴 이름 caption |
| 리포트 미리보기 | `report_md` → `st.markdown` (expander, 기본 접힘) |

---

## session_state 키 목록

| 키 | 타입 | 설명 |
| -- | ---- | ---- |
| `problem_text_input` | str | 문제 설명 입력값 |
| `selected_profiles` | list[str] | 선택된 프로파일 이름 목록 |
| `input_mode` | str | 로그 입력 방식 |
| `local_path_input` | str | 로컬 경로 입력값 |
| `recursive` | bool | 폴더 재귀 여부 |
| `log_text_direct` | str | 텍스트 직접 입력값 |
| `input_keywords` | str | 키워드 필터 입력값 (raw) |
| `file_conditions_input` | str | 파일 선별 조건 입력값 (raw) |
| `window_mode` | str | 시간 구간 필터 방식 |
| `anchors_raw` | str | 앵커 패턴 입력값 (raw) |
| `window_before` / `window_after` | float | 앵커 전후 구간(초) |
| `ts_start_input` / `ts_end_input` | str | 타임스탬프 입력값 |
| `burst_threshold` | int | Burst collapse 임계값 |
| `input_raw_logs` | dict[str, str] | 로드된 파일 내용 보관 |
| `pinned_case_name` | str | 직접 지정한 케이스 이름 |
| `last_result` | PipelineResult | 마지막 분석 결과 |
| `problem_text` | str | 마지막 분석 시 문제 설명 |
