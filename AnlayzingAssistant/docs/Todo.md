## Todo

### 파이프라인

- [x] 실시간 분석 진행 표시 (Page 1): 분석 실행 중 각 Stage 완료 시 진행 상태를 즉시 표시. `st.status` + `st.progress` 조합. 현재는 분석 완료 후 결과만 정적으로 표시.

### 분석 페이지 (Page 1)

- [x] 분석 프로파일 선택을 분석 페이지의 제일 상단에 위치하도록 할 것
- [x] 분석결과 요약을 로그 분석 페이지 하단에 표시해줄 것.
- [ ] 파일 선별에 대한 명확한 표기, 파일 선별은 독립적으로 수행가능하도록 사용자 인터페이스에 노출되어야 하지만, 실제 구현상에서는 중복되는 기능이다. 이에 대한 정리가 필요하다.
<!-- 고려해볼만한 사항: 분석 중 다른 페이지로 이동하면 pipeline.run()이 메인 스레드에서 강제 중단되어 결과가 유실된다.
     개선 방향: 백그라운드 스레드(threading.Thread)로 pipeline.run()을 분리하고, 완료 결과를 session_state에 폴링하는 방식.
     단, Streamlit의 session_state 스레드 안전성 및 st.* UI 호출 제한(메인 스레드 전용)을 감안한 설계 필요. -->

### 파이프라인 — 미구현 Stage

- [x] Stage 6 Reflection: Stage 5 리포트를 LLM으로 재검증. evidence 존재 여부, 추측성 판단 식별, 판정-score 일관성 검증. 추측성 항목 제거 또는 별도 구분하여 최종 리포트 출력. 설정 페이지에서 비활성화 가능.

### Observability

- [ ] `analysis_logs` 테이블 스키마 추가 및 파이프라인 각 Stage 입출력 로깅 구현. Stage별 로깅 항목은 Architecture.md 참조. 결과 페이지(Page 2)에서 상세 로그 조회 UI 추가.

### Prompt Template

- [x] 시스템 분석 지침(Common Constraints) 설정 페이지(Page 6)에서 관리 기능 구현. `AppConfig`에 필드 추가, Stage 2B(Reranker)와 Stage 5(Report Generation) 프롬프트에 주입.

### Context Strategy

- [x] LLM `num_ctx` 초과 시 처리 전략 구현. `client.show(model_name)`으로 런타임 `num_ctx` 조회. 우선순위 truncation / 분할 전송 / 혼합 3가지 전략, 설정 페이지(Page 6)에서 선택 가능하도록.

### 데이터 관리

- [ ] 분석 프로파일에 대해서 기본 분석 프로파일을 작성하도록 할 것.
- [ ] 분석 이력에 전체 리포트(report_md) 저장 및 이력 페이지에서 리포트 복원 조회. 현재 history 테이블에는 판정·점수·케이스·패턴만 저장되어 있어 페이지 새로고침 시 리포트 본문이 유실됨.
