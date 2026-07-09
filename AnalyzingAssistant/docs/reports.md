## Modification Requirements
- LLM으로 패턴 생성시, 저장 전에 사용자가 수정할 수 있어야 한다. 
- 분석 프로그레스를 단계가 지나가면 사라지지 않고, 계속 볼 수 있게 해야 한다. 요약/상세 옵션두기.
- num_ctx와 max_tokens가 같은 거 아닌가? num_ctx를 조회 못하는 경우, max_tokens를 쓰게 팝업을 띄우는 건 어떨까?
- llm interface가 openai인데, anthropic api interface도 쓸 수 있게 config에서 지정할 수 있을까?
- 분석 단계별 수행시간, 전체 분석 수행시간이 나왔으면 좋겠다.
- 분석 리포트를 따로 저장할 수 있을까?, 페이지를 리프레쉬하면 사라져버린다. 이력에서는 간단하게밖에 안나온다.
- 설정에서 api_key를 노출시키지 않도록 하자.
- 분석 진행 중에 다른 페이지 로 가면 분석 진행중이던건 어떻게 되지?


## 버그
- 공백을 포함한 패턴의 경우 제대로 매치를 못하는 것 같다.

## 에러 또는 경고
케이스 업데이트에서 패턴 같이 추가시.
	저장 실패: st.session_state.hu_case_name cannot be modified after the widget with key hu_case_name is instantiated.
