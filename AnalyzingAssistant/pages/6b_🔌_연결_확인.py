"""
pages/5b_🔌_연결_확인.py

LLM / 임베딩 연결 확인 페이지.
  - 현재 config/LLM/config.yaml 에 설정된 엔드포인트로 연결 테스트
  - 사용 가능한 모델 목록 조회 (/v1/models)
  - LLM: 간단한 채팅 완성 요청으로 응답 확인
  - Embedding: 짧은 텍스트 임베딩 요청으로 벡터 차원 확인
"""

from __future__ import annotations

import time

import streamlit as st

st.set_page_config(
    page_title = "Kernel Log Analyzer",
    page_icon  = "🔍",
    layout     = "wide",
    initial_sidebar_state = "expanded",
)

from openai import OpenAI

from core.config import cfg

# ── 페이지 ────────────────────────────────────────────────────────────────────

st.header("🔌 연결 확인")
st.caption("⚙️ 설정 페이지에서 변경한 내용은 앱 재시작 후 반영됩니다.")

# ── 현재 설정 요약 ────────────────────────────────────────────────────────────

with st.expander("현재 설정 (config/LLM/config.yaml)", expanded=True):
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**LLM**")
        st.code(f"base_url : {cfg.llm_base_url}\nmodel    : {cfg.llm_model}", language="text")
    with col2:
        st.markdown("**Embedding**")
        st.code(f"base_url : {cfg.embed_base_url}\nmodel    : {cfg.embed_model}", language="text")

st.divider()

# ── 모델 목록 조회 ─────────────────────────────────────────────────────────────

st.subheader("사용 가능한 모델 목록")

col_llm_models, col_embed_models = st.columns(2)

same_endpoint = (cfg.embed_base_url == cfg.llm_base_url and
                 cfg.embed_api_key  == cfg.llm_api_key)

with col_llm_models:
    st.markdown("**LLM 엔드포인트**")
    if st.button("모델 목록 조회", key="fetch_llm_models"):
        with st.spinner("조회 중..."):
            try:
                client = OpenAI(base_url=cfg.llm_base_url, api_key=cfg.llm_api_key)
                models = sorted(
                    [m.id for m in client.models.list().data],
                    key=str.lower,
                )
                st.session_state["llm_models"] = models
            except Exception as e:
                st.session_state["llm_models"] = None
                st.session_state["llm_models_error"] = str(e)

    if "llm_models" in st.session_state:
        if st.session_state["llm_models"] is None:
            st.error(st.session_state.get("llm_models_error", "알 수 없는 오류"))
        else:
            models = st.session_state["llm_models"]
            st.success(f"{len(models)}개 모델")
            for m in models:
                tags = []
                if m == cfg.llm_model:
                    tags.append("LLM")
                if same_endpoint and m == cfg.embed_model:
                    tags.append("Embedding")
                tag = f"  ✅ **현재 사용 중** ({', '.join(tags)})" if tags else ""
                st.markdown(f"- `{m}`{tag}")

with col_embed_models:
    st.markdown("**Embedding 엔드포인트**")

    if same_endpoint:
        st.info(f"LLM 과 같은 엔드포인트입니다.  \n위 목록에서 **{cfg.embed_model}** 을 확인하세요.")
    else:
        if st.button("모델 목록 조회", key="fetch_embed_models"):
            with st.spinner("조회 중..."):
                try:
                    client = OpenAI(base_url=cfg.embed_base_url, api_key=cfg.embed_api_key)
                    models = sorted(
                        [m.id for m in client.models.list().data],
                        key=str.lower,
                    )
                    st.session_state["embed_models"] = models
                except Exception as e:
                    st.session_state["embed_models"] = None
                    st.session_state["embed_models_error"] = str(e)

        if "embed_models" in st.session_state:
            if st.session_state["embed_models"] is None:
                st.error(st.session_state.get("embed_models_error", "알 수 없는 오류"))
            else:
                models = st.session_state["embed_models"]
                st.success(f"{len(models)}개 모델")
                for m in models:
                    tag = " ✅ **현재 사용 중**" if m == cfg.embed_model else ""
                    st.markdown(f"- `{m}`{tag}")

st.divider()

# ── 연결 테스트 ───────────────────────────────────────────────────────────────

st.subheader("연결 테스트")

col_test_llm, col_test_embed = st.columns(2)

# ── LLM 테스트 ────────────────────────────────────────────────────────────────

with col_test_llm:
    st.markdown("**LLM 채팅 완성**")
    test_prompt = st.text_input(
        "테스트 프롬프트",
        value="'연결 테스트 성공'이라고만 답하세요.",
        key="llm_test_prompt",
    )
    if st.button("LLM 테스트", type="primary", key="test_llm"):
        with st.spinner(f"`{cfg.llm_model}` 호출 중..."):
            try:
                client = OpenAI(base_url=cfg.llm_base_url, api_key=cfg.llm_api_key)
                t0 = time.time()
                response = client.chat.completions.create(
                    model       = cfg.llm_model,
                    #messages    = [{"role": "user", "content": test_prompt}],
                    messages=[{"role": "user", "content": "'연결 Test 성공'이라고만 답하세요."}],
                    temperature = 0.0,
                    max_tokens  = 500,
                )
                elapsed = time.time() - t0
                content = response.choices[0].message.content.strip()
                st.success(f"✅ 연결 성공  ({elapsed:.2f}s)")
                st.text_area("응답", value=content, height=100, disabled=True)
            except Exception as e:
                st.error(f"❌ 연결 실패\n\n{e}")

# ── Embedding 테스트 ──────────────────────────────────────────────────────────

with col_test_embed:
    st.markdown("**임베딩**")
    test_text = st.text_input(
        "테스트 텍스트",
        value="커널 로그 분석 테스트",
        key="embed_test_text",
    )
    if st.button("임베딩 테스트", type="primary", key="test_embed"):
        with st.spinner(f"`{cfg.embed_model}` 호출 중..."):
            try:
                client = OpenAI(base_url=cfg.embed_base_url, api_key=cfg.embed_api_key)
                t0 = time.time()
                response = client.embeddings.create(
                    model = cfg.embed_model,
                    input = [test_text],
                )
                elapsed = time.time() - t0
                vec = response.data[0].embedding
                st.success(f"✅ 연결 성공  ({elapsed:.2f}s)")
                st.metric("벡터 차원", len(vec))
                st.caption(f"첫 5개 값: {[round(v, 4) for v in vec[:5]]}")
            except Exception as e:
                st.error(f"❌ 연결 실패\n\n{e}")
