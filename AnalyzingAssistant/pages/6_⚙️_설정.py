"""
pages/5_⚙️_설정.py

설정 페이지.
  - LLM 프로파일 선택 / 모델 목록 조회 / 연결 확인 / 저장
  - Embedding 프로파일 선택 / 모델 목록 조회 / 연결 확인 / 저장
  - 파이프라인 임계값
  - 마스터 룰 관리
  - 노이즈 패턴 관리
  - 분석 이력
"""

from __future__ import annotations

import json
import time

import streamlit as st

st.set_page_config(
    page_title = "Kernel Log Analyzer",
    page_icon  = "🔍",
    layout     = "wide",
    initial_sidebar_state = "expanded",
)

from openai import OpenAI

from core.db import DB_PATH, get_conn
from core.config import (
    AppConfig, LLMProfile, cfg,
    save as cfg_save,
    rebuild_from_active,
    rebuild_embed_from_active,
    DEFAULT_SYSTEM_ANALYSIS_GUIDELINES,
)
from core.master_rule import load_rules as load_master_rules, MasterRuleGenerator


def _load_settings() -> AppConfig:
    return cfg


def _save_settings(new_cfg: AppConfig) -> None:
    cfg_save(new_cfg)
    import core.config as _c
    _c.cfg = new_cfg


# ── num_ctx 헬퍼 ──────────────────────────────────────────────────────────────

_KNOWN_CONTEXT_WINDOWS: dict[str, int] = {
    # OpenAI
    "gpt-4o":                  128_000,
    "gpt-4o-mini":             128_000,
    "gpt-4-turbo":             128_000,
    "gpt-4-turbo-preview":     128_000,
    "gpt-4":                     8_192,
    "gpt-4-32k":                32_768,
    "gpt-3.5-turbo":            16_385,
    "gpt-3.5-turbo-16k":        16_385,
    # Anthropic
    "claude-3-5-sonnet-20241022": 200_000,
    "claude-3-5-haiku-20241022":  200_000,
    "claude-3-opus-20240229":     200_000,
    "claude-3-sonnet-20240229":   200_000,
    "claude-3-haiku-20240307":    200_000,
}


def _query_num_ctx(base_url: str, model: str) -> tuple[int | None, str]:
    """
    모델의 num_ctx(컨텍스트 윈도우 크기)를 세 가지 전략으로 순서대로 조회한다.

    Returns
    -------
    (num_ctx, source)
      num_ctx : 조회된 토큰 수. 실패 시 None.
      source  : 값을 얻은 방법 설명 문자열.

    전략 순서:
      1. Ollama /api/show         — Ollama 전용 메타데이터 API
      2. /v1/models/{model}       — vLLM / LM Studio 등 OpenAI 호환 모델 정보
      3. 알려진 모델 하드코딩 목록 — OpenAI / Anthropic 공개 모델
    """
    import json
    import urllib.request

    # ── 전략 1: Ollama /api/show ──────────────────────────────────────────────
    ollama_base = base_url.rstrip("/")
    if ollama_base.endswith("/v1"):
        ollama_base = ollama_base[:-3]
    try:
        payload = json.dumps({"model": model}).encode()
        req = urllib.request.Request(
            f"{ollama_base}/api/show",
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())

        for key, val in data.get("model_info", {}).items():
            if "context_length" in key:
                return int(val), "Ollama /api/show"

        for line in data.get("parameters", "").splitlines():
            parts = line.strip().split()
            if len(parts) >= 2 and parts[0] == "num_ctx":
                return int(parts[1]), "Ollama /api/show (parameters)"
    except Exception:
        pass

    # ── 전략 2: OpenAI 호환 /v1/models/{model} (vLLM / LM Studio) ───────────
    v1_base = base_url.rstrip("/")
    if not v1_base.endswith("/v1"):
        v1_base = v1_base + "/v1"
    try:
        req = urllib.request.Request(
            f"{v1_base}/models/{model}",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())

        for field in ("max_model_len", "context_length", "max_context_length"):
            if field in data and data[field]:
                return int(data[field]), f"/v1/models ({field})"
    except Exception:
        pass

    # ── 전략 3: 알려진 모델 하드코딩 목록 ────────────────────────────────────
    model_lower = model.lower()
    for known, ctx in _KNOWN_CONTEXT_WINDOWS.items():
        if model_lower.startswith(known.lower()):
            return ctx, f"알려진 모델 목록 ({known})"

    return None, ""


def _suggest_max_log_lines(num_ctx: int) -> int:
    """
    num_ctx 기준 권장 최대 로그 라인 수를 계산한다.

    산정 기준:
      - 프롬프트 오버헤드(지침, 분석 데이터, 출력 버퍼): 4,000 토큰 예약
      - 로그 라인당 평균 토큰: 30
      - 결과 범위: 50 ~ 2,000
    """
    available = max(0, num_ctx - 4000)
    return max(50, min(2000, available // 30))


# ── 노이즈 패턴 헬퍼 ──────────────────────────────────────────────────────────

def _load_noise_patterns() -> list[dict]:
    with get_conn(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT id, pattern, comment FROM noise_patterns ORDER BY id"
        ).fetchall()
    return [dict(r) for r in rows]


def _add_noise_pattern(pattern: str, comment: str) -> None:
    with get_conn(DB_PATH) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO noise_patterns (pattern, comment) VALUES (?, ?)",
            (pattern, comment),
        )


def _delete_noise_pattern(nid: int) -> None:
    with get_conn(DB_PATH) as conn:
        conn.execute("DELETE FROM noise_patterns WHERE id=?", (nid,))


# ── 이력 헬퍼 ─────────────────────────────────────────────────────────────────

def _load_history(limit: int = 50) -> list[dict]:
    with get_conn(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT id, input_hash, result, created_at FROM history ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        try:
            d["result_parsed"] = json.loads(d["result"])
        except Exception:
            d["result_parsed"] = {}
        result.append(d)
    return result


def _delete_history(hid: int) -> None:
    with get_conn(DB_PATH) as conn:
        conn.execute("DELETE FROM history WHERE id=?", (hid,))


def _clear_all_history() -> int:
    with get_conn(DB_PATH) as conn:
        cur = conn.execute("DELETE FROM history")
        return cur.rowcount


# ── 공통 UI 빌더 ──────────────────────────────────────────────────────────────

def _profile_selector_ui(
    *,
    section_key: str,           # "llm" or "embed"
    profiles: list,             # list[LLMProfile]
    active_name: str,
    role_label: str,            # "LLM" or "Embedding"
    test_fn,                    # callable(profile, model) -> (ok: bool, msg: str)
) -> tuple[str, str]:
    """
    프로파일 선택 → 모델 목록 조회 → 모델 선택 + 연결 확인 UI.
    반환: (선택된 profile name, 선택된 model name)
    """
    profile_names = [p.name for p in profiles]

    # ── 프로파일 선택 ────────────────────────────────────────────────────────

    def _fmt(name: str) -> str:
        return f"{name}  ✅ (활성)" if name == active_name else name

    saved_sel = st.session_state.get(f"_{section_key}_sel_profile", active_name)
    default_idx = profile_names.index(saved_sel) if saved_sel in profile_names else (
        profile_names.index(active_name) if active_name in profile_names else 0
    )
    sel_name = st.selectbox(
        "프로파일",
        profile_names,
        index=default_idx,
        format_func=_fmt,
        key=f"_{section_key}_profile_sb",
    )
    st.session_state[f"_{section_key}_sel_profile"] = sel_name

    sel_profile = next(p for p in profiles if p.name == sel_name)
    _key = sel_profile.api_key
    _masked_key = _key if len(_key) <= 6 else _key[:4] + "••••"
    _provider = getattr(sel_profile, "provider", "openai")
    st.caption(f"`{sel_profile.base_url}`  ·  api_key: `{_masked_key}`  ·  provider: `{_provider}`")

    # 프로파일 바뀌면 모델 목록 초기화
    if st.session_state.get(f"_{section_key}_models_for") != sel_name:
        for k in [f"_{section_key}_models", f"_{section_key}_models_err",
                  f"_{section_key}_conn_result"]:
            st.session_state.pop(k, None)

    # ── 모델 목록 조회 ────────────────────────────────────────────────────────

    if st.button("모델 목록 조회", key=f"_{section_key}_fetch_btn"):
        with st.spinner("모델 목록 조회 중..."):
            try:
                if getattr(sel_profile, "provider", "openai") == "anthropic":
                    import anthropic as _ant
                    _ant_client = _ant.Anthropic(api_key=sel_profile.api_key)
                    models = sorted([m.id for m in _ant_client.models.list().data], key=str.lower)
                else:
                    client = OpenAI(base_url=sel_profile.base_url, api_key=sel_profile.api_key)
                    models = sorted([m.id for m in client.models.list().data], key=str.lower)
                st.session_state[f"_{section_key}_models"]     = models
                st.session_state[f"_{section_key}_models_for"] = sel_name
                st.session_state.pop(f"_{section_key}_models_err", None)
            except Exception as e:
                st.session_state[f"_{section_key}_models_err"] = str(e)
                st.session_state.pop(f"_{section_key}_models", None)

    if err := st.session_state.get(f"_{section_key}_models_err"):
        st.error(f"조회 실패: {err}")

    # ── 모델 선택 + 연결 확인 ────────────────────────────────────────────────

    fetched = st.session_state.get(f"_{section_key}_models")
    m_col, t_col = st.columns([4, 1])

    if fetched:
        cur = sel_profile.model
        midx = fetched.index(cur) if cur in fetched else 0
        sel_model = m_col.selectbox(
            "모델 선택",
            fetched,
            index=midx,
            key=f"_{section_key}_model_sb",
        )
    else:
        m_col.info(f"현재 모델: **`{sel_profile.model}`**  \n목록 조회 후 변경할 수 있습니다.")
        sel_model = sel_profile.model

    if t_col.button("연결 확인", key=f"_{section_key}_conn_btn", use_container_width=True):
        with st.spinner(f"`{sel_model}` 연결 확인 중..."):
            ok, msg, detail = test_fn(sel_profile, sel_model)
            st.session_state[f"_{section_key}_conn_result"] = (ok, msg, detail)
    
    if raw := st.session_state.get(f"_{section_key}_conn_result"):
        ok, msg = raw[0], raw[1]
        detail = raw[2] if len(raw) > 2 else ""
        #st.success(msg) if ok else st.error(msg)
        if ok :
            st.success(msg)
            st.text_area("응답", value=detail, height=80, disabled=True)
        else:
            st.error(msg)
    return sel_name, sel_model


# ── 연결 테스트 함수 ──────────────────────────────────────────────────────────

def _test_llm(profile: LLMProfile, model: str) -> tuple[bool, str, str]:
    """(ok, status_msg, detail) 반환. detail 은 text_area 로 별도 표시."""
    if getattr(profile, "provider", "openai") == "anthropic":
        try:
            import anthropic as _ant
            _ant_kwargs: dict = {"api_key": profile.api_key, "timeout": 60}
            if profile.base_url:
                _ant_kwargs["base_url"] = profile.base_url
            client = _ant.Anthropic(**_ant_kwargs)
            t0 = time.time()
            resp = client.messages.create(
                model=model,
                messages=[{"role": "user", "content": "'연결 Test 성공'이라고만 답하세요."}],
                temperature=0.0,
                max_tokens=100,
            )
            elapsed = time.time() - t0
            content = resp.content[0].text.strip()
            return True, f"✅ 연결 성공 ({elapsed:.2f}s)", content
        except Exception as e:
            return False, f"❌ 연결 실패: {e}", ""
    try:
        client = OpenAI(base_url=profile.base_url, api_key=profile.api_key, timeout=60)
        t0 = time.time()
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "'연결 Test 성공'이라고만 답하세요."}],
            temperature=0.0,
            max_tokens=500,
        )
        elapsed = time.time() - t0
        content = resp.choices[0].message.content.strip()
        return True, f"✅ 연결 성공 ({elapsed:.2f}s)", content
    except Exception as e:
        return False, f"❌ 연결 실패: {e}", ""


def _test_embed(profile: LLMProfile, model: str) -> tuple[bool, str, str]:
    """(ok, status_msg, detail) 반환."""
    try:
        client = OpenAI(base_url=profile.base_url, api_key=profile.api_key, timeout=30)
        t0 = time.time()
        resp = client.embeddings.create(model=model, input=["연결 테스트"])
        elapsed = time.time() - t0
        dim = len(resp.data[0].embedding)
        return True, f"✅ 연결 성공 ({elapsed:.2f}s)", f"벡터 차원: {dim}"
    except Exception as e:
        return False, f"❌ 연결 실패: {e}", ""


# ── 페이지 ────────────────────────────────────────────────────────────────────

st.header("⚙️ 설정")

# 저장 완료 배너 (저장 직후 재렌더링 시 한 번만 표시)
if "_save_msg" in st.session_state:
    st.success(st.session_state.pop("_save_msg"))

settings = _load_settings()

tab_llm, tab_master, tab_noise, tab_history = st.tabs(
    ["LLM", "마스터 룰", "노이즈 패턴", "분석 이력"]
)

# ── 탭 1: LLM ────────────────────────────────────────────────────────────────

with tab_llm:

    # ────────────────────────────── LLM 설정 ──────────────────────────────────

    st.subheader("LLM 설정")
    st.caption(
        "리포트 생성 · Reranker · 패턴 생성에 사용하는 LLM 입니다.  \n"
        "OpenAI 호환 엔드포인트라면 Ollama, vLLM, LM Studio, OpenAI 등 모두 사용 가능합니다."
    )

    llm_sel_name, llm_sel_model = _profile_selector_ui(
        section_key = "llm",
        profiles    = settings.llm_profiles,
        active_name = settings.active_llm,
        role_label  = "LLM",
        test_fn     = _test_llm,
    )

    # LLM 전용 고급 옵션 (선택된 프로파일 기반)
    sel_llm_profile = next(p for p in settings.llm_profiles if p.name == llm_sel_name)

    with st.expander("고급 옵션 (provider / max_tokens / timeout / report_temperature)"):
        _provider_options = ["openai", "anthropic"]
        _cur_provider = getattr(sel_llm_profile, "provider", "openai")
        adv_provider = st.selectbox(
            "API 제공자",
            _provider_options,
            index=_provider_options.index(_cur_provider) if _cur_provider in _provider_options else 0,
            key="_llm_adv_provider",
            help=(
                "**openai**: Ollama·vLLM·LM Studio·OpenAI 등 OpenAI 호환 엔드포인트  \n"
                "**anthropic**: Anthropic API (`api_key` 에 Anthropic API 키 입력, `base_url` 무시)"
            ),
        )
        ac1, ac2, ac3 = st.columns(3)
        adv_max_raw = ac1.text_input(
            "max_tokens",
            value="" if sel_llm_profile.max_tokens is None else str(sel_llm_profile.max_tokens),
            placeholder="비워두면 기본값 (Anthropic: 4096)",
            key="_llm_adv_max",
        )
        adv_to_raw = ac2.text_input(
            "timeout (초)",
            value="" if sel_llm_profile.timeout is None else str(sel_llm_profile.timeout),
            placeholder="비워두면 무제한",
            key="_llm_adv_to",
        )
        adv_temp = ac3.slider(
            "report_temperature",
            0.0, 1.0,
            float(sel_llm_profile.report_temperature),
            0.05,
            key="_llm_adv_temp",
            help=(
                "Stage 5 리포트 생성 및 Stage 6 Reflection 에만 적용되는 LLM 온도.  \n"
                "0.0 = 결정론적 (매번 동일한 결과), 1.0 = 창의적 (결과 변동 큼).  \n"
                "패턴·마스터룰 생성 등 구조화 JSON 출력은 이 값과 무관하게 0.1 고정."
            ),
        )
    adv_max = int(adv_max_raw.strip()) if adv_max_raw.strip().isdigit() else None
    adv_to  = int(adv_to_raw.strip())  if adv_to_raw.strip().isdigit()  else None

    if st.button("저장", type="primary", key="_llm_save_btn"):
        import dataclasses as _dc
        new_llm_profiles = [
            _dc.replace(p,
                model              = llm_sel_model,
                max_tokens         = adv_max,
                timeout            = adv_to,
                report_temperature = adv_temp,
                provider           = adv_provider,
            ) if p.name == llm_sel_name else p
            for p in settings.llm_profiles
        ]
        new_cfg = rebuild_from_active(settings, new_llm_profiles, llm_sel_name)
        _save_settings(new_cfg)
        st.cache_resource.clear()
        st.session_state["_save_msg"] = f"✅ LLM 저장 완료: '{llm_sel_name}' / '{llm_sel_model}'"
        st.rerun()

    st.divider()

    # ──────────────────────────── Embedding 설정 ──────────────────────────────

    st.subheader("Embedding 설정")
    st.caption("벡터 검색(KB) 에 사용하는 임베딩 모델입니다. BGE-M3 권장.")

    embed_sel_name, embed_sel_model = _profile_selector_ui(
        section_key = "embed",
        profiles    = settings.embed_profiles,
        active_name = settings.active_embed,
        role_label  = "Embedding",
        test_fn     = _test_embed,
    )

    if st.button("저장", type="primary", key="_embed_save_btn"):
        import dataclasses as _dc
        new_embed_profiles = [
            _dc.replace(p, model=embed_sel_model) if p.name == embed_sel_name else p
            for p in settings.embed_profiles
        ]
        new_cfg = rebuild_embed_from_active(settings, new_embed_profiles, embed_sel_name)
        _save_settings(new_cfg)
        st.cache_resource.clear()
        st.session_state["_save_msg"] = f"✅ Embedding 저장 완료: '{embed_sel_name}' / '{embed_sel_model}'"
        st.rerun()

    st.divider()

    # ──────────────────────────── 파이프라인 임계값 ────────────────────────────

    st.subheader("파이프라인 임계값")

    # ── num_ctx 조회 (폼 외부 — 버튼 인터랙션은 form 안에 넣을 수 없음) ──────────

    _active_llm_p = next(
        (p for p in settings.llm_profiles if p.name == settings.active_llm),
        None,
    )

    _nc_col, _nc_btn_col = st.columns([5, 1])
    _nc_col.caption(
        f"활성 LLM: **{settings.active_llm}** / `{_active_llm_p.model if _active_llm_p else '?'}`  \n"
        "모델의 `num_ctx`(컨텍스트 윈도우)를 조회하여 최대 로그 라인 수의 적정값을 계산합니다."
    )
    _nc_query_btn = _nc_btn_col.button(
        "num_ctx 조회", key="_nc_query_btn", use_container_width=True,
    )

    if _nc_query_btn and _active_llm_p:
        with st.spinner("num_ctx 조회 중..."):
            _num_ctx, _nc_source = _query_num_ctx(_active_llm_p.base_url, _active_llm_p.model)
        if _num_ctx:
            _suggested = _suggest_max_log_lines(_num_ctx)
            st.session_state["_nc_result"] = {
                "num_ctx":   _num_ctx,
                "suggested": _suggested,
                "model":     _active_llm_p.model,
                "source":    _nc_source,
            }
            st.session_state["_suggested_max_log"] = _suggested
            st.session_state["_nc_manual"] = False
        else:
            st.session_state["_nc_result"] = None
            st.session_state["_nc_manual"] = True
            st.warning(
                "num_ctx 를 확인할 수 없습니다.  \n"
                "Ollama·vLLM·LM Studio 엔드포인트가 아니거나, "
                "알려진 모델 목록에 없는 경우입니다.  \n"
                "아래 `num_ctx` 입력란이 활성화되었습니다. 직접 값을 입력해 주세요.",
                icon="⚠️",
            )

    if nc := st.session_state.get("_nc_result"):
        st.info(
            f"**{nc['model']}** — num_ctx: **{nc['num_ctx']:,} 토큰** "
            f"_(출처: {nc['source']})_  \n"
            f"권장 최대 로그 라인: **{nc['suggested']:,}줄**  \n"
            f"_(프롬프트 오버헤드 4,000 토큰 제외, 로그 라인당 평균 30 토큰 기준)_",
            icon="💡",
        )

    # ── num_ctx 입력 (폼 외부 — 수동 입력 시 즉시 rerun 되어 폼 내 max_log_lines 기본값이 갱신됨)
    _nc_default_val = int(
        (st.session_state.get("_nc_result") or {}).get("num_ctx")
        or settings.num_ctx
        or 0
    )
    _nc_manual = bool(st.session_state.get("_nc_manual", False))
    num_ctx_input = st.number_input(
        "num_ctx (컨텍스트 윈도우 토큰 수)",
        min_value=0, max_value=1_000_000,
        value=_nc_default_val,
        step=1024,
        disabled=not _nc_manual,
        help=(
            "모델의 컨텍스트 윈도우 크기(토큰).  \n"
            "`num_ctx 조회` 버튼으로 자동 입력되며, 조회 실패 시에만 수동 입력이 활성화됩니다.  \n"
            "0 = 미설정 (컨텍스트 전략 비활성)."
        ),
    )

    # 수동 입력 모드에서 값이 바뀌었을 때만 권장 라인 수 갱신
    # (조회 성공 시에는 버튼 핸들러에서 이미 _suggested_max_log 을 설정하므로 여기선 건너뜀)
    if _nc_manual and int(num_ctx_input) > 0:
        if int(num_ctx_input) != st.session_state.get("_last_manual_num_ctx"):
            st.session_state["_suggested_max_log"] = _suggest_max_log_lines(int(num_ctx_input))
            st.session_state["_last_manual_num_ctx"] = int(num_ctx_input)

    if int(num_ctx_input) > 0:
        st.caption(
            f"num_ctx = **{int(num_ctx_input):,} 토큰** — 저장 시 컨텍스트 전략이 활성화됩니다."
        )
    else:
        st.caption(
            "num_ctx 미설정(0) — 컨텍스트 전략이 비활성 상태로 저장됩니다. "
            "위 `num_ctx 조회` 버튼으로 값을 얻거나, 조회 실패 시 수동 입력하세요."
        )

    with st.form("pipeline_form"):
        kb_threshold = st.slider(
            "KB 관련성 임계값 (Stage 2 HIT 기준)",
            0.0, 1.0, float(settings.kb_threshold), 0.05,
        )
        definite_threshold = st.slider(
            "확정 진단 임계값 (Stage 5 '문제' 판정 기준)",
            0.0, 1.0, float(settings.definite_threshold), 0.05,
        )
        _max_log_default = int(
            st.session_state.get("_suggested_max_log", settings.max_log_lines)
        )
        max_log_lines = st.number_input(
            "LLM 에 전달할 최대 로그 라인 수",
            min_value=10, max_value=2000,
            value=_max_log_default,
            help="'알 수 없음' 판정 경로에서 LLM에 직접 전달할 L_common 최대 라인 수입니다.",
        )
        st.divider()
        stage6_enabled = st.checkbox(
            "Stage 6 Reflection 활성화",
            value=bool(settings.stage6_reflection_enabled),
            help=(
                "Stage 5 리포트를 LLM으로 한 번 더 검증합니다.  \n"
                "검증 항목: evidence 존재 여부, 추측성 판단 식별, 판정-score 일관성.  \n"
                "활성화 시 LLM 호출이 1회 추가되어 분석 시간이 늘어납니다."
            ),
        )
        observability_enabled = st.checkbox(
            "Observability (Stage별 상세 로그) 활성화",
            value=bool(settings.observability_enabled),
            help=(
                "각 Stage의 입출력·판단 근거를 SQLite `analysis_logs` 테이블에 기록합니다.  \n"
                "활성화 시 결과 페이지에서 Stage별 상세 로그를 조회할 수 있습니다.  \n"
                "기록 항목: Stage 1 정제 통계, Stage 2 HIT/MISS 근거, Stage 4 패턴 점수, Stage 5 리포트 원문 등.  \n"
                "비활성화 시 성능 영향 없음."
            ),
        )
        st.divider()

        _STRATEGY_LABELS = {
            "truncation": "우선순위 Truncation",
            "split":      "분할 전송",
            "hybrid":     "혼합 (Hybrid)",
        }
        _strategy_options = list(_STRATEGY_LABELS.keys())
        _strategy_default = (
            _strategy_options.index(settings.context_strategy)
            if settings.context_strategy in _strategy_options else 0
        )
        context_strategy = st.selectbox(
            "컨텍스트 전략 (num_ctx 초과 시)",
            _strategy_options,
            index=_strategy_default,
            format_func=lambda s: _STRATEGY_LABELS[s],
            help=(
                "**우선순위 Truncation**: 우선순위 순으로 채우고 초과분 버림 — 빠름, 낮은 우선순위 지식 손실 가능.  \n"
                "**분할 전송**: 사전지식을 청크로 나눠 여러 번 호출 후 이어받음 — 느림, 전체 지식 반영.  \n"
                "**혼합**: 기본 Truncation 적용, 잘리는 비율이 임계값 초과 시 분할 전송으로 전환."
            ),
        )
        hybrid_overflow_ratio = st.slider(
            "혼합 전환 임계값 (hybrid 전용)",
            0.0, 1.0,
            float(settings.hybrid_overflow_ratio),
            0.05,
            help=(
                "혼합(Hybrid) 전략에서만 사용됩니다.  \n"
                "사전지식이 이 비율 이상 잘릴 때 분할 전송으로 자동 전환합니다.  \n"
                "예: 0.3 → 30% 이상 잘릴 경우 분할 전송 사용."
            ),
        )
        pipeline_submit = st.form_submit_button("저장", type="primary")

    if pipeline_submit:
        import dataclasses as _dc
        # num_ctx: 입력값 > 0 이면 저장, 0 이면 미설정(None) 으로 처리
        saved_num_ctx: int | None = int(num_ctx_input) if num_ctx_input > 0 else None

        new_cfg = _dc.replace(
            settings,
            kb_threshold              = kb_threshold,
            definite_threshold        = definite_threshold,
            max_log_lines             = int(max_log_lines),
            stage6_reflection_enabled = stage6_enabled,
            context_strategy          = context_strategy,
            hybrid_overflow_ratio     = float(hybrid_overflow_ratio),
            num_ctx                   = saved_num_ctx,
            observability_enabled     = observability_enabled,
        )
        _save_settings(new_cfg)
        st.session_state.pop("_suggested_max_log", None)   # 제안값 적용 완료 → 초기화
        st.session_state.pop("_last_manual_num_ctx", None)
        st.session_state["_save_msg"] = "✅ 파이프라인 설정 저장 완료"
        st.rerun()

    st.divider()

    # ──────────────────────────── 시스템 분석 지침 ────────────────────────────

    st.subheader("시스템 분석 지침")
    st.caption(
        "모든 LLM 프롬프트 최상단에 주입되는 전역 분석 제약 조건입니다.  \n"
        "Stage 2B(Reranker) 와 Stage 5(리포트 생성) 에 적용됩니다."
    )

    sag_value = st.text_area(
        "시스템 분석 지침",
        value=settings.system_analysis_guidelines,
        height=160,
        label_visibility="collapsed",
        key="_sag_textarea",
    )

    sag_col1, sag_col2 = st.columns([1, 1])
    sag_save    = sag_col1.button("저장", type="primary",   key="_sag_save_btn")
    sag_reset   = sag_col2.button("기본값으로 초기화",       key="_sag_reset_btn")

    if sag_save:
        import dataclasses as _dc
        new_cfg = _dc.replace(settings, system_analysis_guidelines=sag_value)
        _save_settings(new_cfg)
        st.session_state["_save_msg"] = "✅ 시스템 분석 지침 저장 완료"
        st.rerun()

    if sag_reset:
        import dataclasses as _dc
        new_cfg = _dc.replace(
            settings,
            system_analysis_guidelines=DEFAULT_SYSTEM_ANALYSIS_GUIDELINES,
        )
        _save_settings(new_cfg)
        st.session_state["_save_msg"] = "✅ 시스템 분석 지침을 기본값으로 초기화했습니다."
        st.rerun()

    st.divider()

    # ──────────────────────────── 현재 설정 요약 ──────────────────────────────

    with st.expander("현재 설정 파일 (config/LLM/config.yaml)", expanded=False):
        active_llm_p   = next((p for p in settings.llm_profiles   if p.name == settings.active_llm),   None)
        active_embed_p = next((p for p in settings.embed_profiles  if p.name == settings.active_embed), None)
        st.json({
            "system_analysis_guidelines": settings.system_analysis_guidelines,
            "active_llm": settings.active_llm,
            "active_llm_profile": {
                "base_url":           active_llm_p.base_url           if active_llm_p else None,
                "model":              active_llm_p.model               if active_llm_p else None,
                "max_tokens":         active_llm_p.max_tokens          if active_llm_p else None,
                "timeout":            active_llm_p.timeout             if active_llm_p else None,
                "report_temperature": active_llm_p.report_temperature  if active_llm_p else None,
            },
            "active_embed": settings.active_embed,
            "active_embed_profile": {
                "base_url": active_embed_p.base_url if active_embed_p else None,
                "model":    active_embed_p.model    if active_embed_p else None,
            },
            "pipeline": {
                "kb_threshold":             settings.kb_threshold,
                "definite_threshold":       settings.definite_threshold,
                "max_log_lines":            settings.max_log_lines,
                "stage6_reflection_enabled": settings.stage6_reflection_enabled,
                "context_strategy":         settings.context_strategy,
                "hybrid_overflow_ratio":    settings.hybrid_overflow_ratio,
                "num_ctx":                  settings.num_ctx,
                "observability_enabled":    settings.observability_enabled,
            },
        })

# ── 탭 2: 마스터 룰 ──────────────────────────────────────────────────────────

def _load_master_rules_ui() -> list[dict]:
    return load_master_rules(DB_PATH)


def _add_master_rule(name: str, rule_type: str, pattern: str, comment: str) -> None:
    with get_conn(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO master_rules (name, rule_type, pattern, comment) VALUES (?,?,?,?)",
            (name, rule_type, pattern, comment),
        )


def _delete_master_rule(rid: int) -> None:
    with get_conn(DB_PATH) as conn:
        conn.execute("DELETE FROM master_rules WHERE id=?", (rid,))


with tab_master:
    st.subheader("마스터 룰")
    st.caption(
        "Stage 1(L_common) 이후, Stage 3 이전에 적용되는 전역 로그 스트림 정규화 규칙입니다.  \n"
        "패턴 매칭 전에 일괄 적용되어 중복·반복 이벤트를 단일 이벤트로 축약합니다."
    )

    master_rules = _load_master_rules_ui()

    if not master_rules:
        st.info("등록된 마스터 룰이 없습니다.")
    else:
        for mr in master_rules:
            col_name, col_type, col_pat, col_del = st.columns([2, 2, 3, 1])
            col_name.markdown(f"**{mr['name']}**")
            col_type.code(mr["rule_type"], language="text")
            col_pat.code(mr["pattern"], language="text")
            if col_del.button("삭제", key=f"del_mr_{mr['id']}"):
                _delete_master_rule(mr["id"])
                st.rerun()
            if mr.get("comment"):
                st.caption(f"  ↳ {mr['comment']}")

    st.divider()
    st.subheader("자연어로 룰 추가")
    st.caption(
        "정규화하고 싶은 로그 패턴을 자유롭게 설명하면 LLM 이 마스터 룰을 생성합니다.  \n"
        "예) *'Mute 신호가 연속으로 여러 번 와도 한 번으로 봐야 해'*"
    )

    nl_input   = st.text_area("정규화 요구사항", height=100, key="mr_nl_input")
    generate_btn = st.button("✨ 룰 생성", type="primary", key="mr_generate_btn")

    if generate_btn:
        if not nl_input.strip():
            st.error("요구사항을 입력해 주세요.")
        else:
            with st.spinner("LLM 이 마스터 룰을 생성하는 중입니다..."):
                try:
                    gen = MasterRuleGenerator(DB_PATH).generate(nl_input.strip())
                    st.session_state["mr_generated"] = {
                        "name":        gen.name,
                        "rule_type":   gen.rule_type,
                        "pattern":     gen.pattern,
                        "comment":     gen.comment,
                        "explanation": gen.explanation,
                    }
                except Exception as e:
                    st.error(f"생성 실패: {e}")
                    st.session_state.pop("mr_generated", None)

    if "mr_generated" in st.session_state:
        g = st.session_state["mr_generated"]
        st.markdown("#### 생성된 룰 미리보기")
        with st.container(border=True):
            col_l, col_r = st.columns([3, 1])
            col_l.markdown(f"**{g['name']}**")
            col_r.code(g["rule_type"], language="text")
            st.code(g["pattern"], language="text")
            if g["explanation"]:
                st.info(g["explanation"])
            if g["comment"]:
                st.caption(f"설명: {g['comment']}")

        st.markdown("**저장 전 수정 (선택)**")
        with st.form("mr_save_form"):
            save_name    = st.text_input("룰 이름",      value=g["name"])
            save_pattern = st.text_input("패턴 (regex)", value=g["pattern"])
            save_comment = st.text_input("설명",         value=g["comment"])
            save_type    = st.selectbox("rule_type", ["DEDUP_CONSECUTIVE"],
                                        index=0 if g["rule_type"] not in ["DEDUP_CONSECUTIVE"] else 0)
            col_save, col_discard = st.columns(2)
            save_submit    = col_save.form_submit_button("저장", type="primary")
            discard_submit = col_discard.form_submit_button("취소")

        if save_submit:
            if not save_name.strip() or not save_pattern.strip():
                st.error("이름과 패턴은 필수입니다.")
            else:
                try:
                    _add_master_rule(save_name.strip(), save_type, save_pattern.strip(), save_comment.strip())
                    st.success(f"마스터 룰 '{save_name}' 저장 완료")
                    st.session_state.pop("mr_generated", None)
                    st.rerun()
                except Exception as e:
                    st.error(f"저장 실패: {e}")
        if discard_submit:
            st.session_state.pop("mr_generated", None)
            st.rerun()

    with st.expander("직접 입력으로 추가"):
        with st.form("add_master_rule_form"):
            mr_name    = st.text_input("룰 이름 *")
            mr_type    = st.selectbox("rule_type", ["DEDUP_CONSECUTIVE"])
            mr_pattern = st.text_input("적용 대상 pattern (regex) *")
            mr_comment = st.text_input("설명 (선택)")
            mr_submit  = st.form_submit_button("추가", type="primary")
        if mr_submit:
            if not mr_name.strip() or not mr_pattern.strip():
                st.error("이름과 패턴은 필수입니다.")
            else:
                try:
                    _add_master_rule(mr_name.strip(), mr_type, mr_pattern.strip(), mr_comment.strip())
                    st.success(f"마스터 룰 '{mr_name}' 추가 완료")
                    st.rerun()
                except Exception as e:
                    st.error(f"추가 실패: {e}")

# ── 탭 3: 노이즈 패턴 ────────────────────────────────────────────────────────

with tab_noise:
    st.subheader("노이즈 패턴 목록")
    st.caption("Stage 1-1 파싱 후 제거할 로그 라인 패턴 (regex).")

    noise_patterns = _load_noise_patterns()
    if not noise_patterns:
        st.info("등록된 노이즈 패턴이 없습니다.")
    else:
        for np in noise_patterns:
            col_pat, col_comment, col_del = st.columns([3, 3, 1])
            col_pat.code(np["pattern"], language="text")
            col_comment.caption(np.get("comment") or "")
            if col_del.button("삭제", key=f"del_noise_{np['id']}"):
                _delete_noise_pattern(np["id"])
                st.rerun()

    st.divider()
    st.subheader("노이즈 패턴 추가")
    with st.form("add_noise_form"):
        np_pattern = st.text_input("regex 패턴 *", placeholder=r"audit\[")
        np_comment = st.text_input("설명 (선택)")
        np_submit  = st.form_submit_button("추가", type="primary")
    if np_submit:
        if not np_pattern.strip():
            st.error("패턴을 입력하세요.")
        else:
            _add_noise_pattern(np_pattern.strip(), np_comment.strip())
            st.success("노이즈 패턴 추가 완료")
            st.rerun()

# ── 탭 4: 분석 이력 ──────────────────────────────────────────────────────────

with tab_history:
    st.subheader("분석 이력 (최근 50건)")

    history = _load_history()
    if not history:
        st.info("이력이 없습니다.")
    else:
        for h in history:
            parsed    = h.get("result_parsed", {})
            verdict   = parsed.get("verdict", "?")
            problem   = parsed.get("problem_text", "")[:80]
            score     = parsed.get("score", 0.0)
            case_name = parsed.get("matched_case") or "—"
            ICONS = {"문제": "🔴", "불확실": "🟡", "알 수 없음": "⚪"}
            icon  = ICONS.get(verdict, "❓")

            with st.expander(
                f"{icon} [{h['id']}] {verdict}  |  {h['created_at']}  |  {problem}",
                expanded=False,
            ):
                c1, c2, c3 = st.columns(3)
                c1.metric("점수",     f"{score:.0%}")
                c2.metric("케이스",    case_name)
                c3.metric("입력 해시", h["input_hash"][:12] + "…")
                matched = parsed.get("matched_patterns", [])
                if matched:
                    st.markdown("**매칭 패턴**: " + ", ".join(p["name"] for p in matched))
                report_md = parsed.get("report_md", "")
                if report_md:
                    with st.expander("리포트 전문 보기"):
                        st.markdown(report_md)
                if st.button("이력 삭제", key=f"del_hist_{h['id']}"):
                    _delete_history(h["id"])
                    st.rerun()

    st.divider()
    if history:
        if st.button("전체 이력 삭제", type="secondary"):
            count = _clear_all_history()
            st.success(f"{count}건 삭제 완료")
            st.rerun()
