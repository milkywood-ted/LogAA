"""
ui/pattern_form.py

패턴 입력 폼 공유 컴포넌트.
Page 3 (케이스+패턴 통합 입력)과 Page 4 (패턴 관리)에서 공통으로 사용한다.

사용 방법::

    from ui.pattern_form import render_type_selector, render_pattern_form

    # 1) 타입 selectbox — st.form 바깥에 배치해야 변경 즉시 재렌더링됨
    render_type_selector("my_type_key")

    # 2) 패턴 필드 폼
    p_dict, cancelled = render_pattern_form(
        "my_form_key", "my_type_key", all_pattern_names,
        submit_label="저장",
    )
    if p_dict is not None:
        # 저장 처리 ...
    if cancelled:
        # 취소 처리 ...
"""
from __future__ import annotations

import streamlit as st

TYPE_OPTIONS: list[str] = ["PRESENCE", "SEQUENCE", "WINDOW", "ABSENCE", "COMPOSITE"]


def render_type_selector(type_key: str, *, label: str = "타입 *") -> None:
    """타입 selectbox를 렌더링한다.

    st.form 바깥에 배치해야 타입 변경 시 하위 필드가 즉시 재렌더링된다.

    Parameters
    ----------
    type_key : session_state 키. render_pattern_form 에 동일 키를 전달한다.
    label    : selectbox 레이블.
    """
    if type_key not in st.session_state or st.session_state[type_key] not in TYPE_OPTIONS:
        st.session_state[type_key] = "PRESENCE"
    st.selectbox(label, TYPE_OPTIONS, key=type_key)


def render_pattern_form(
    form_key: str,
    type_key: str,
    all_pattern_names: list[str],
    *,
    initial_values: dict | None = None,
    exclude_names: list[str] | None = None,
    submit_label: str = "저장",
    show_cancel: bool = False,
    clear_on_submit: bool = True,
) -> tuple[dict | None, bool]:
    """패턴 입력 폼을 렌더링한다.

    render_type_selector 를 이 함수 호출 전 form 바깥에서 먼저 호출해야 한다.
    타입별 필드는 type_key 의 session_state 값에 따라 자동 분기된다.

    Parameters
    ----------
    form_key          : st.form 키 (페이지 내 유일해야 함)
    type_key          : render_type_selector 에 전달한 것과 동일한 session_state 키
    all_pattern_names : COMPOSITE 타입 구성 패턴 선택 목록
    initial_values    : 수정 모드에서 기존 값을 미리 채울 dict.
                        keywords 는 list[str] 또는 쉼표 구분 str 모두 허용.
                        SEQUENCE 는 steps key (list[str]),
                        COMPOSITE 는 components key (list[str]).
    exclude_names     : 중복 검사 대상 이름 목록 (DB 기존 이름 + 임시 목록)
    submit_label      : 제출 버튼 레이블
    show_cancel       : True 이면 취소 버튼도 표시
    clear_on_submit   : 제출 후 폼 초기화 여부

    Returns
    -------
    (p_dict, cancelled)
        p_dict    : 유효성 통과 후 제출 시 패턴 dict, 그 외 None
        cancelled : 취소 버튼 클릭 시 True
    """
    iv = initial_values or {}
    cur_type = st.session_state.get(type_key, "PRESENCE")
    if cur_type not in TYPE_OPTIONS:
        cur_type = "PRESENCE"

    kws_raw = iv.get("keywords", "")
    if isinstance(kws_raw, list):
        kws_raw = ", ".join(kws_raw)

    with st.form(form_key, clear_on_submit=clear_on_submit):
        np_name = st.text_input(
            "패턴 이름 *",
            value=iv.get("name", ""),
            placeholder="ata_timeout_presence",
        )
        np_desc = st.text_input(
            "설명",
            value=iv.get("description", ""),
            placeholder="ATA 타임아웃 메시지 존재 여부",
        )
        np_kws = st.text_input(
            "키워드 (쉼표 구분)",
            value=kws_raw,
            placeholder="ata, timeout",
            help="패턴 매칭 전 로그 라인을 사전 필터링하는 키워드입니다. 하나라도 포함된 라인만 이 패턴의 매칭 대상이 됩니다. 비워 두면 전체 라인을 대상으로 매칭합니다.",
        )
        np_weight = st.number_input(
            "weight",
            min_value=0.1,
            max_value=10.0,
            value=float(iv.get("weight", 1.0)),
            step=0.1,
        )
        np_guide = st.text_area(
            "분석지침 (선택)",
            value=iv.get("analysis_guidelines", ""),
            height=80,
            placeholder="이 패턴이 매칭되었을 때 LLM이 참조할 분석 지침을 작성하세요.",
        )

        st.markdown("---")

        np_pattern = np_window_sec = np_threshold = np_unique = None
        np_dedup_win = np_step_dedup = np_non_overlap = None
        np_steps_raw = np_trigger = np_absent = np_operator = np_comps_raw = None

        if cur_type == "PRESENCE":
            np_pattern = st.text_input(
                "regex 패턴 *",
                value=iv.get("pattern", ""),
                placeholder=r"ata\d+: exception Emask",
            )
            np_dedup_win = st.number_input(
                "event_dedup_window_sec (0=미사용)",
                min_value=0.0,
                value=float(iv.get("event_dedup_window_sec") or 0.0),
            )

        elif cur_type == "SEQUENCE":
            np_steps_raw = st.text_area(
                "Steps (줄 단위 regex)",
                value="\n".join(iv.get("steps") or []),
                height=100,
            )
            np_step_dedup  = st.checkbox("step_dedup",      value=bool(iv.get("step_dedup",      False)))
            np_non_overlap = st.checkbox("non_overlapping", value=bool(iv.get("non_overlapping", False)))

        elif cur_type == "WINDOW":
            np_pattern = st.text_input("regex 패턴 *", value=iv.get("pattern", ""))
            np_window_sec = st.number_input(
                "window_sec (초)", min_value=1.0, value=float(iv.get("window_sec") or 60.0),
            )
            np_threshold = st.number_input(
                "count_threshold", min_value=1, value=int(iv.get("count_threshold") or 3),
            )
            np_unique = st.checkbox(
                "count_unique_only", value=bool(iv.get("count_unique_only", False)),
            )

        elif cur_type == "ABSENCE":
            np_trigger = st.text_input(
                "trigger_pattern (regex) *", value=iv.get("trigger_pattern", ""),
            )
            np_absent = st.text_input(
                "absent_pattern (regex) *", value=iv.get("absent_pattern", ""),
            )
            np_window_sec = st.number_input(
                "window_sec (초)", min_value=1.0, value=float(iv.get("window_sec") or 30.0),
            )

        elif cur_type == "COMPOSITE":
            op_options = ["AND", "OR", "NOT"]
            op_default = iv.get("operator") or "AND"
            if op_default not in op_options:
                op_default = "AND"
            np_operator = st.selectbox("operator", op_options, index=op_options.index(op_default))
            comp_default = [c for c in (iv.get("components") or []) if c in all_pattern_names]
            np_comps_raw = st.multiselect("구성 패턴", options=all_pattern_names, default=comp_default)

        if show_cancel:
            btn_c1, btn_c2 = st.columns([1, 1])
            submitted = btn_c1.form_submit_button(submit_label, type="primary")
            cancelled = btn_c2.form_submit_button("취소")
        else:
            submitted = st.form_submit_button(submit_label, type="primary")
            cancelled = False

    if cancelled:
        return None, True

    if not submitted:
        return None, False

    # 유효성 검사
    name_stripped = np_name.strip()
    if not name_stripped:
        st.error("패턴 이름은 필수입니다.")
        return None, False
    if exclude_names and name_stripped in exclude_names:
        st.error(f"'{name_stripped}' 패턴 이름이 이미 존재합니다. 다른 이름을 사용하세요.")
        return None, False
    if cur_type == "PRESENCE" and not (np_pattern or "").strip():
        st.error("PRESENCE 타입은 regex 패턴이 필수입니다.")
        return None, False
    if cur_type == "SEQUENCE" and not (np_steps_raw or "").strip():
        st.error("SEQUENCE 타입은 Steps가 필수입니다.")
        return None, False
    if cur_type == "WINDOW" and not (np_pattern or "").strip():
        st.error("WINDOW 타입은 regex 패턴이 필수입니다.")
        return None, False
    if cur_type == "ABSENCE" and (
        not (np_trigger or "").strip() or not (np_absent or "").strip()
    ):
        st.error("ABSENCE 타입은 trigger_pattern과 absent_pattern이 필수입니다.")
        return None, False

    p_dict: dict = {
        "name":                name_stripped,
        "type":                cur_type,
        "description":         np_desc.strip(),
        "keywords":            [k.strip() for k in (np_kws or "").split(",") if k.strip()],
        "weight":              float(np_weight),
        "analysis_guidelines": np_guide.strip(),
    }

    if cur_type == "PRESENCE":
        p_dict["pattern"] = np_pattern
        if np_dedup_win and np_dedup_win > 0:
            p_dict["event_dedup_window_sec"] = np_dedup_win
    elif cur_type == "SEQUENCE":
        p_dict["steps"]           = [s.strip() for s in (np_steps_raw or "").splitlines() if s.strip()]
        p_dict["step_dedup"]      = np_step_dedup
        p_dict["non_overlapping"] = np_non_overlap
    elif cur_type == "WINDOW":
        p_dict["pattern"]          = np_pattern
        p_dict["window_sec"]        = np_window_sec
        p_dict["count_threshold"]   = int(np_threshold)
        p_dict["count_unique_only"] = np_unique
    elif cur_type == "ABSENCE":
        p_dict["trigger_pattern"] = np_trigger
        p_dict["absent_pattern"]  = np_absent
        p_dict["window_sec"]      = np_window_sec
    elif cur_type == "COMPOSITE":
        p_dict["operator"]   = np_operator
        p_dict["components"] = np_comps_raw or []

    return p_dict, False
