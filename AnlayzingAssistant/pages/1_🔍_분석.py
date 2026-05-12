"""
pages/1_🔍_분석.py

로그 분석 페이지.
  - 문제 설명 입력
  - 로그 입력: 파일 업로드 / 로컬 경로 (파일·폴더) / 텍스트 직접 입력
  - Stage 1 설정 (앵커, 윈도우)
  - 분석 실행 → 결과를 session_state 에 저장 → 결과 페이지로 이동 안내
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

st.set_page_config(
    page_title = "Kernel Log Analyzer",
    page_icon  = "🔍",
    layout     = "wide",
    initial_sidebar_state = "expanded",
)

from core.db import DB_PATH, get_conn, init_db
from core.config import cfg
from core.log_loader import load_files, load_folder
from core.log_refiner import RefineConfig
from core.pattern_seeder import seed
from core.pipeline import Pipeline, PipelineResult
from core.profile import PROFILES_DIR, load_profiles, merge_profiles, MergedProfile

# ── 공유 자원 ─────────────────────────────────────────────────────────────────

@st.cache_resource
def _bootstrap() -> bool:
    init_db(DB_PATH)
    yaml_path = Path(__file__).parent.parent / "config" / "patterns" / "default_patterns.yaml"
    if yaml_path.exists():
        seed(yaml_path, DB_PATH)
    return True


@st.cache_resource
def get_pipeline() -> Pipeline:
    import core.config as _c
    _cfg = _c.cfg
    return Pipeline(
        llm_model          = _cfg.llm_model,
        kb_threshold       = _cfg.kb_threshold,
        definite_threshold = _cfg.definite_threshold,
        max_log_lines      = _cfg.max_log_lines,
        observability      = _cfg.observability_enabled,
        reflect            = _cfg.stage6_reflection_enabled,
    )


_bootstrap()

# ── session_state 기본값 초기화 (페이지 이동 후 복귀 시 값 유지) ─────────────

_DEFAULTS: dict = {
    "problem_text_input":   "",
    "selected_profiles":    [],
    "input_mode":           "파일 업로드",
    "local_path_input":     "",
    "recursive":            True,
    "log_text_direct":      "",
    "input_keywords":       "",
    "file_conditions_input": "",
    "window_mode":          "필터 없음",
    "anchors_raw":          "",
    "window_before":        30.0,
    "window_after":         30.0,
    "ts_start_input":       "",
    "ts_end_input":         "",
    "burst_threshold":      5,
    "input_raw_logs":       {},   # 업로드/로드된 파일 보관
    "confirm_default_profile": False,
    "confirm_no_profile":      False,
    "pinned_case_name":        "",   # 사용자가 지정한 케이스 이름 (빈 문자열 = 자동 검색)
}
for _k, _v in _DEFAULTS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

# ── 페이지 ────────────────────────────────────────────────────────────────────

st.header("🔍 로그 분석")

# ── 1. 분석 프로파일 선택 ────────────────────────────────────────────────────

st.subheader("1. 분석 프로파일 (선택)")
st.caption(
    "프로파일을 선택하면 분석 지침이 LLM에 주입되고, 사전정제 키워드가 Stage 1에 적용됩니다.  \n"
    "복수 선택 시 구성 요소가 병합됩니다. **📋 프로파일** 페이지에서 관리할 수 있습니다."
)

_all_profiles = load_profiles(PROFILES_DIR)
if _all_profiles:
    _all_profile_names = [p.name for p in _all_profiles]
    # selected_profiles 에 저장된 이름 중 현재 파일에 없는 것 제거
    _valid_default = [n for n in st.session_state["selected_profiles"] if n in _all_profile_names]
    if _valid_default != st.session_state["selected_profiles"]:
        st.session_state["selected_profiles"] = _valid_default

    selected_profile_names: list[str] = st.multiselect(
        "적용할 프로파일",
        options=_all_profile_names,
        key="selected_profiles",
    )

    if selected_profile_names:
        merged_profile: MergedProfile = merge_profiles(selected_profile_names, DB_PATH)
        col1, col2 = st.columns(2)
        if merged_profile.prefilter_keywords:
            col1.caption(
                "사전정제 키워드: " + ", ".join(f"`{k}`" for k in merged_profile.prefilter_keywords)
            )
        if merged_profile.analysis_guidelines.strip():
            with col2.expander("병합된 분석 지침 미리보기"):
                st.text(merged_profile.analysis_guidelines[:500] +
                        ("..." if len(merged_profile.analysis_guidelines) > 500 else ""))
    else:
        merged_profile = None
else:
    st.caption("등록된 프로파일이 없습니다.")
    selected_profile_names = []
    merged_profile = None

# ── 1-B. 케이스 직접 지정 (선택) ─────────────────────────────────────────────

def _load_case_names() -> list[str]:
    """등록된 케이스 이름 목록을 DB 에서 직접 읽는다 (UI 용, 경량 쿼리)."""
    with get_conn(DB_PATH) as conn:
        rows = conn.execute("SELECT name FROM cases ORDER BY name").fetchall()
    return [r["name"] for r in rows]


def _load_case_preview(name: str) -> dict | None:
    """지정한 케이스의 간단한 요약(설명·키워드·연결 패턴 수)을 반환."""
    with get_conn(DB_PATH) as conn:
        row = conn.execute(
            "SELECT id, description, keywords FROM cases WHERE name = ?",
            (name,),
        ).fetchone()
        if row is None:
            return None
        pat_cnt = conn.execute(
            "SELECT COUNT(*) AS c FROM case_patterns WHERE case_id = ?",
            (row["id"],),
        ).fetchone()["c"]
    import json as _json
    try:
        kws = _json.loads(row["keywords"]) if row["keywords"] else []
    except Exception:
        kws = []
    return {
        "description":  row["description"] or "",
        "keywords":     kws,
        "pattern_count": int(pat_cnt or 0),
    }


_AUTO_LABEL = "(자동 검색)"

with st.expander("🎯 케이스 직접 지정 (선택)", expanded=False):
    st.caption(
        "특정 케이스를 지정하면 Stage 2 자동 검색(벡터+Reranker)을 건너뛰고 "
        "해당 케이스로 바로 Stage 3~5 를 진행합니다.  \n"
        "비워두면 기존 자동 검색이 그대로 동작합니다."
    )

    _all_case_names = _load_case_names()
    if _all_case_names:
        _options = [_AUTO_LABEL] + _all_case_names
        _stored = st.session_state["pinned_case_name"]
        _default_idx = _options.index(_stored) if _stored in _options else 0

        _picked = st.selectbox(
            "사용할 케이스",
            options=_options,
            index=_default_idx,
            help="자동 검색으로 돌아가려면 (자동 검색) 을 선택하세요.",
        )
        pinned_case_name: str | None = None if _picked == _AUTO_LABEL else _picked
        st.session_state["pinned_case_name"] = pinned_case_name or ""

        if pinned_case_name:
            _preview = _load_case_preview(pinned_case_name)
            if _preview:
                desc = _preview["description"]
                desc_short = desc[:300] + ("..." if len(desc) > 300 else "")
                st.info(
                    f"📌 지정됨: **{pinned_case_name}**  ·  연결 패턴 {_preview['pattern_count']}개  \n"
                    f"{desc_short if desc_short else '(설명 없음)'}",
                    icon="📌",
                )
                if _preview["keywords"]:
                    st.caption(
                        "키워드: " + ", ".join(f"`{k}`" for k in _preview["keywords"])
                    )
    else:
        st.caption("등록된 케이스가 없습니다. **📚 케이스** 페이지에서 먼저 등록하세요.")
        pinned_case_name = None

# ── 2. 문제 설명 ──────────────────────────────────────────────────────────────

st.subheader("2. 문제 설명")
problem_text = st.text_area(
    "어떤 문제가 발생했나요?",
    value=st.session_state["problem_text_input"],
    placeholder="예) ATA 드라이브 타임아웃이 반복되고 시스템이 느려졌습니다.",
    height=100,
)
st.session_state["problem_text_input"] = problem_text

# ── 3. 로그 입력 ──────────────────────────────────────────────────────────────

st.subheader("3. 로그 입력")
input_mode = st.radio(
    "입력 방식",
    ["파일 업로드", "로컬 경로", "텍스트 직접 입력"],
    horizontal=True,
    key="input_mode",
)

raw_logs: dict[str, str] = {}

if input_mode == "파일 업로드":
    uploaded = st.file_uploader(
        "로그 파일을 업로드하세요 (여러 파일 가능)",
        accept_multiple_files=True,
    )
    if uploaded:
        new_logs: dict[str, str] = {}
        for f in uploaded:
            try:
                content = f.read().decode("utf-8", errors="replace")
                new_logs[f.name] = content
            except Exception as e:
                st.warning(f"{f.name} 읽기 실패: {e}")
        if new_logs:
            st.session_state["input_raw_logs"] = new_logs
            st.success(f"{len(new_logs)}개 파일 로드됨")

    # 이전에 업로드했던 파일이 남아있으면 재사용
    if st.session_state["input_raw_logs"] and not uploaded:
        prev = st.session_state["input_raw_logs"]
        st.info(f"이전에 로드한 파일 {len(prev)}개를 사용합니다.")
        with st.expander("파일 목록", expanded=False):
            for name in prev:
                st.caption(name)

    raw_logs = st.session_state["input_raw_logs"]

elif input_mode == "로컬 경로":
    local_path_input = st.text_input(
        "파일 또는 폴더 경로",
        placeholder="/var/log/dmesg  또는  /var/log/",
        value=st.session_state["local_path_input"],
    )
    st.session_state["local_path_input"] = local_path_input
    recursive = st.checkbox(
        "폴더 하위 디렉터리 포함 (recursive)",
        value=st.session_state["recursive"],
    )
    st.session_state["recursive"] = recursive

    if local_path_input.strip():
        target = Path(local_path_input.strip())
        if not target.exists():
            st.error(f"경로를 찾을 수 없습니다: `{target}`")
        else:
            try:
                if target.is_file():
                    loaded = load_files([target])
                else:
                    loaded = load_folder(target, recursive=recursive)

                raw_logs = {str(k): v for k, v in loaded.items()}
                st.session_state["input_raw_logs"] = raw_logs

                if raw_logs:
                    st.success(f"{len(raw_logs)}개 파일 로드됨")
                    with st.expander("로드된 파일 목록", expanded=False):
                        for path in raw_logs:
                            st.caption(path)
                else:
                    st.warning("해당 경로에서 읽을 수 있는 파일을 찾지 못했습니다.")
            except Exception as e:
                st.error(f"파일 로드 실패: {e}")

else:
    log_text = st.text_area(
        "커널 로그를 붙여 넣으세요",
        height=300,
        placeholder="[  1234.567890] ata1: exception Emask 0x0 SAct 0x0 ...",
        value=st.session_state["log_text_direct"],
    )
    st.session_state["log_text_direct"] = log_text
    if log_text.strip():
        raw_logs["input.log"] = log_text
        st.session_state["input_raw_logs"] = raw_logs

# ── 키워드 필터 ───────────────────────────────────────────────────────────────

st.markdown("**임시 키워드 필터**")
st.caption(
    "입력한 키워드 중 하나라도 포함된 파일만 선별하고, "
    "해당 파일에서 키워드가 포함된 라인만 추출합니다.  \n"
    "비워두면 전체 파일·전체 라인을 사용합니다."
)

kw_col, _ = st.columns([2, 1])
keywords_raw = kw_col.text_input(
    "키워드 (쉼표로 구분)",
    placeholder="Mute, Unmute, ata, error",
    key="input_keywords",
)
input_keywords = [k.strip() for k in keywords_raw.split(",") if k.strip()]

if input_keywords and raw_logs:
    from core.log_refiner import prefilter_by_keywords as _prefilter
    preview = _prefilter(raw_logs, input_keywords)
    total_lines   = sum(len(c.splitlines()) for c in raw_logs.values())
    preview_lines = sum(len(c.splitlines()) for c in preview.values())
    st.caption(
        f"예상 결과: {len(raw_logs)}개 파일 → **{len(preview)}개 파일** / "
        f"{total_lines:,}줄 → **{preview_lines:,}줄**"
    )

# ── Stage 1 설정 ──────────────────────────────────────────────────────────────

with st.expander("⚙️ 고급 설정 (Stage 1, 옵션)", expanded=False):

    # ── 사전 정제 옵션 (1-1) ──────────────────────────────────────────────────
    from core.parser_registry import PARSER_DEFS as _PARSER_DEFS
    st.markdown("**사전 정제 옵션 (1-1)**")
    st.caption(
        "커널 로그 포맷을 인식할 파서를 선택합니다.  \n"
        "체크된 포맷의 라인만 추출하고 나머지는 버립니다.  \n"
        "모두 해제하면 포맷 필터링 없이 전체 라인을 그대로 사용합니다."
    )
    pcols = st.columns(max(len(_PARSER_DEFS), 1))
    active_parsers: list[str] = []
    for col, pdef in zip(pcols, _PARSER_DEFS):
        if col.checkbox(
            pdef["label"],
            value=pdef["default"],
            help=pdef.get("description", ""),
            key=f"parser_{pdef['name']}",
        ):
            active_parsers.append(pdef["name"])

    st.divider()

    # ── 파일 선별 조건 (1-4) ──────────────────────────────────────────────────
    st.markdown("**파일 선별 조건 (1-4)**")
    st.caption(
        "아래 패턴을 모두 포함하는 파일만 정제에 사용합니다 (AND 조건).  \n"
        "비워두면 로드된 파일 전체를 사용합니다."
    )
    file_conditions_raw = st.text_area(
        "파일 선별 regex (줄 단위)",
        placeholder="ata\nkernel panic",
        height=80,
        key="file_conditions_input",
    )
    file_conditions = [c.strip() for c in file_conditions_raw.splitlines() if c.strip()]

    st.divider()

    # ── 시간 구간 필터 (1-3) ──────────────────────────────────────────────────
    st.markdown("**시간 구간 필터 (1-3)**")
    window_mode = st.radio(
        "시간 구간 필터 방식",
        ["필터 없음", "앵커 패턴", "타임스탬프 직접 지정"],
        horizontal=True,
        key="window_mode",
        help=(
            "**필터 없음**: 전체 로그 사용  \n"
            "**앵커 패턴**: 지정한 regex 가 등장하는 구간 전후를 추출  \n"
            "**타임스탬프 직접 지정**: 커널 uptime(초) 범위를 직접 입력"
        ),
    )

    anchors: list[str] = []
    window_before = 30.0
    window_after  = 30.0
    ts_start_val: float | None = None
    ts_end_val:   float | None = None

    if window_mode == "앵커 패턴":
        col1, col2 = st.columns(2)
        with col1:
            anchors_raw = st.text_area(
                "앵커 패턴 (줄 단위, regex)",
                placeholder="Mute\nUnmute",
                height=100,
                key="anchors_raw",
            )
            window_before = st.number_input(
                "앵커 이전 구간 (초)", min_value=0.0,
                value=st.session_state["window_before"], step=5.0,
            )
            st.session_state["window_before"] = window_before
        with col2:
            window_after = st.number_input(
                "앵커 이후 구간 (초)", min_value=0.0,
                value=st.session_state["window_after"], step=5.0,
            )
            st.session_state["window_after"] = window_after
        anchors = [a.strip() for a in anchors_raw.splitlines() if a.strip()]

    elif window_mode == "타임스탬프 직접 지정":
        st.caption(
            "커널 로그 타임스탬프는 부팅 후 경과 시간(uptime, 초)입니다.  \n"
            "예) `[12345.678901]` → 12345.678"
        )
        col1, col2 = st.columns(2)
        ts_start_input = col1.text_input(
            "시작 타임스탬프 (초)", placeholder="12300.0  (비워두면 처음부터)",
            key="ts_start_input",
        )
        ts_end_input = col2.text_input(
            "종료 타임스탬프 (초)", placeholder="12600.0  (비워두면 끝까지)",
            key="ts_end_input",
        )

        if ts_start_input.strip():
            try:
                ts_start_val = float(ts_start_input.strip())
            except ValueError:
                st.error("시작 타임스탬프에 유효한 숫자를 입력하세요.")
        if ts_end_input.strip():
            try:
                ts_end_val = float(ts_end_input.strip())
            except ValueError:
                st.error("종료 타임스탬프에 유효한 숫자를 입력하세요.")
        if ts_start_val is not None and ts_end_val is not None and ts_start_val >= ts_end_val:
            st.warning("시작 타임스탬프가 종료 타임스탬프보다 크거나 같습니다.")

    st.divider()
    burst_threshold = st.number_input(
        "Burst collapse 임계값",
        min_value=1,
        value=st.session_state["burst_threshold"],
        step=1,
        help="같은 fingerprint 가 이 횟수 이상 짧은 시간 내 반복되면 1개로 축약합니다.",
    )
    st.session_state["burst_threshold"] = burst_threshold

# ── 4. 분석 실행 ──────────────────────────────────────────────────────────────

st.divider()
run_col, _ = st.columns([1, 4])

with run_col:
    run_btn = st.button("▶ 분석 실행", type="primary", use_container_width=True)

if run_btn:
    if not raw_logs:
        st.error("로그 파일을 업로드하거나 텍스트를 입력해 주세요.")
        st.stop()

    if not problem_text.strip():
        st.session_state["confirm_no_problem"] = True

if st.session_state.get("confirm_no_problem"):
    st.warning("문제 설명이 입력되지 않았습니다. 문제 설명 없이 분석을 진행하시겠습니까?")
    confirm_col, cancel_col, _ = st.columns([1, 1, 3])
    if confirm_col.button("✅ 진행", key="confirm_run"):
        st.session_state["confirm_no_problem"] = False
        st.session_state["run_without_problem"] = True
        st.rerun()
    if cancel_col.button("❌ 취소", key="cancel_run"):
        st.session_state["confirm_no_problem"] = False
        st.stop()
    st.stop()

_past_problem = (run_btn and problem_text.strip()) or st.session_state.pop("run_without_problem", False)

if _past_problem and not selected_profile_names:
    _default_path = PROFILES_DIR / "_default.json"
    if _default_path.exists():
        st.session_state["confirm_default_profile"] = True
    else:
        st.session_state["confirm_no_profile"] = True

if st.session_state.get("confirm_default_profile"):
    st.info("선택된 분석 프로파일이 없습니다. **기본 분석 프로파일**을 적용하여 진행하시겠습니까?")
    col_ok, col_cancel, _ = st.columns([1, 1, 3])
    if col_ok.button("✅ 진행", key="confirm_default_ok"):
        st.session_state["confirm_default_profile"] = False
        st.session_state["run_with_default"] = True
        st.rerun()
    if col_cancel.button("❌ 취소", key="confirm_default_cancel"):
        st.session_state["confirm_default_profile"] = False
    st.stop()

if st.session_state.get("confirm_no_profile"):
    st.warning(
        "선택된 분석 프로파일이 없고 기본 프로파일도 설정되어 있지 않습니다.  \n"
        "프로파일 없이 진행하면 분석 정확도가 낮아질 수 있습니다. 계속하시겠습니까?"
    )
    col_ok, col_cancel, _ = st.columns([1, 1, 3])
    if col_ok.button("✅ 진행", key="confirm_no_prof_ok"):
        st.session_state["confirm_no_profile"] = False
        st.session_state["run_no_profile"] = True
        st.rerun()
    if col_cancel.button("❌ 취소", key="confirm_no_prof_cancel"):
        st.session_state["confirm_no_profile"] = False
    st.stop()

_do_run = (
    (_past_problem and bool(selected_profile_names))
    or st.session_state.pop("run_with_default", False)
    or st.session_state.pop("run_no_profile", False)
)

if _do_run:
    if not raw_logs:
        st.error("로그 파일을 업로드하거나 텍스트를 입력해 주세요.")
        st.stop()

    # 선택된 프로파일 없을 때 기본 프로파일 자동 적용
    _run_merged = merged_profile
    if not selected_profile_names:
        from core.profile import load_profile as _lp
        _def = _lp("_default")
        if _def:
            _run_merged = merge_profiles([_def.name], DB_PATH)

    config = RefineConfig(
        input_keywords    = input_keywords,
        file_conditions   = file_conditions,
        anchors           = anchors,
        window_before_sec = window_before,
        window_after_sec  = window_after,
        ts_start          = ts_start_val,
        ts_end            = ts_end_val,
        burst_threshold   = int(burst_threshold),
        active_parsers    = active_parsers,
    )

    with st.status("분석 중...", expanded=True) as _status:
        _prog  = st.progress(0.0)
        _log   = st.empty()
        _steps: list[str] = []

        def _on_progress(step: int, total: int, name: str, detail: str) -> None:
            _prog.progress((step - 1) / total, text=f"[{step}/{total}] {name}")
            _steps.append(f"✅ **[{step}/{total}] {name}**  \n{detail}")
            _log.markdown("\n\n".join(_steps))

        try:
            pipeline = get_pipeline()
            result: PipelineResult = pipeline.run(
                problem_text     = problem_text,
                raw_logs         = raw_logs,
                config           = config,
                merged_profile   = _run_merged,
                on_progress      = _on_progress,
                pinned_case_name = (st.session_state["pinned_case_name"] or None),
            )
        except Exception as e:
            _status.update(label="분석 실패", state="error")
            st.exception(e)
            st.stop()

        _prog.progress(1.0, text="완료")

    VERDICT_ICON = {"문제": "🔴", "불확실": "🟡", "알 수 없음": "⚪"}
    _icon = VERDICT_ICON.get(result.verdict, "❓")
    _status.update(
        label=f"{_icon} 분석 완료 — 판정: {result.verdict}",
        state="complete",
        expanded=False,
    )

    # 결과를 session_state 에 저장
    st.session_state["last_result"]  = result
    st.session_state["problem_text"] = problem_text

    # 파일 선별 결과 알림
    total = len(raw_logs)
    selected = len(result.selected_logs)
    if total != selected:
        st.warning(
            f"파일 선별: {total}개 중 {selected}개 사용 "
            f"({total - selected}개 조건 미충족으로 제외)"
        )

    st.info("📊 **결과** 페이지에서 상세 내용을 확인하세요.")

# ── 5. 선별 및 필터만 진행 ────────────────────────────────────────────────────

st.divider()
filter_col, _ = st.columns([1, 4])

with filter_col:
    filter_btn = st.button("🔍 선별 및 필터만 진행", use_container_width=True)

if filter_btn:
    if not raw_logs:
        st.error("로그 파일을 업로드하거나 텍스트를 입력해 주세요.")
        st.stop()

    from core.log_refiner import LogRefiner, prefilter_by_keywords as _prefilter2

    filtered = raw_logs.copy()
    if input_keywords:
        filtered = _prefilter2(filtered, input_keywords)

    _refiner = LogRefiner()
    selected_only = _refiner.select_files(filtered, file_conditions)

    st.subheader("선별 결과")

    kw_msg = f"키워드 필터 후 {len(filtered)}개 → " if input_keywords else ""
    st.info(
        f"원본 {len(raw_logs)}개 파일 → {kw_msg}"
        f"파일 선별 후 **{len(selected_only)}개**"
    )

    if selected_only:
        with st.expander("선별된 파일 목록", expanded=True):
            for fname, content in selected_only.items():
                lines = len(content.splitlines())
                st.caption(f"📄 {fname}  ({lines:,}줄)")
    else:
        st.warning("선별 조건을 만족하는 파일이 없습니다.")

# ── 분석 결과 요약 ────────────────────────────────────────────────────────────

_last_result: PipelineResult | None = st.session_state.get("last_result")
_last_problem: str = st.session_state.get("problem_text", "")

if _last_result is not None:
    st.divider()
    VERDICT_ICON = {"문제": "🔴", "불확실": "🟡", "알 수 없음": "⚪"}
    icon = VERDICT_ICON.get(_last_result.verdict, "❓")

    st.subheader(f"{icon} 최근 분석 결과 요약")

    if _last_problem:
        st.caption(f"문제 설명: {_last_problem[:120]}{'...' if len(_last_problem) > 120 else ''}")

    # 핵심 지표
    mc = _last_result.match_result
    case = _last_result.matched_case
    m_col1, m_col2, m_col3, m_col4 = st.columns(4)
    m_col1.metric("판정",     _last_result.verdict)
    m_col2.metric("진단 점수", f"{mc.score:.0%}" if mc else "—")
    m_col3.metric("매칭 패턴", len(mc.matched)   if mc else 0)
    m_col4.metric("매칭 케이스", case.name if case else "—")

    # 매칭된 패턴 목록
    if mc and mc.matched:
        st.caption(
            "매칭 패턴: " + ", ".join(
                f"`{p.name}`" for p in mc.matched
            )
        )

    # 리포트 미리보기
    if _last_result.report_md:
        with st.expander("리포트 미리보기", expanded=False):
            st.markdown(_last_result.report_md)
            st.info("📊 전체 내용은 **결과** 페이지에서 확인하세요.")
