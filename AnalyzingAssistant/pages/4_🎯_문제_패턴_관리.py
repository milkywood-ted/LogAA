"""
pages/4_🎯_패턴.py

문제 패턴 관리 페이지.
  - 문제 패턴 목록 조회 (타입별 필터)
  - 문제 패턴 추가 (자연어 → LLM 생성 또는 직접 입력)
  - 문제 패턴 수정 / 삭제 (분석지침 포함)
  - 기본값 초기화 (default_patterns.yaml 재시드)
"""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

st.set_page_config(
    page_title = "Kernel Log Analyzer",
    page_icon  = "🔍",
    layout     = "wide",
    initial_sidebar_state = "expanded",
)

from core.db import DB_PATH, get_conn
from core.pattern_db import insert_pattern
from core.pattern_seeder import reset as seed_reset
from core.pattern_generator import PatternGenerator
from ui.pattern_form import render_type_selector, render_pattern_form

# ── 공유 자원 ─────────────────────────────────────────────────────────────────

@st.cache_resource
def get_generator() -> PatternGenerator:
    cfg = st.session_state.get("settings", {})
    return PatternGenerator(
        model   = cfg.get("llm_model", "qwen3:14b"),
        db_path = DB_PATH,
    )


# ── 헬퍼 ──────────────────────────────────────────────────────────────────────

def _load_patterns(type_filter: str | None = None) -> list[dict]:
    with get_conn(DB_PATH) as conn:
        if type_filter and type_filter != "전체":
            rows = conn.execute(
                "SELECT * FROM patterns WHERE type=? ORDER BY id",
                (type_filter,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM patterns ORDER BY id").fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["keywords"] = json.loads(d["keywords"]) if d["keywords"] else []
        result.append(d)
    return result


def _load_steps(pattern_id: int) -> list[str]:
    with get_conn(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT pattern FROM pattern_steps WHERE pattern_id=? ORDER BY step_order",
            (pattern_id,),
        ).fetchall()
    return [r["pattern"] for r in rows]


def _load_components(pattern_id: int) -> list[str]:
    with get_conn(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT p2.name
            FROM pattern_components pc
            JOIN patterns p2 ON pc.ref_pattern_id = p2.id
            WHERE pc.pattern_id = ?
            ORDER BY pc.component_order
            """,
            (pattern_id,),
        ).fetchall()
    return [r["name"] for r in rows]


def _delete_pattern(pattern_id: int) -> None:
    with get_conn(DB_PATH) as conn:
        # ref_pattern_id 에 ON DELETE CASCADE 가 없으므로 참조를 먼저 제거
        conn.execute("DELETE FROM pattern_components WHERE ref_pattern_id=?", (pattern_id,))
        conn.execute("DELETE FROM patterns WHERE id=?", (pattern_id,))


def _find_dependencies(pattern_id: int) -> dict:
    """패턴 삭제 시 영향받는 의존성을 반환한다.

    Returns:
        {
          "composites": [{"id": ..., "name": ...}, ...],  # 이 패턴을 구성요소로 쓰는 COMPOSITE 패턴
          "cases":      [{"id": ..., "name": ...}, ...],  # 이 패턴을 사용하는 케이스 (삭제 시 연결 자동 해제)
        }
    """
    with get_conn(DB_PATH) as conn:
        composites = conn.execute(
            """
            SELECT DISTINCT p.id, p.name
            FROM pattern_components pc
            JOIN patterns p ON pc.pattern_id = p.id
            WHERE pc.ref_pattern_id = ?
            """,
            (pattern_id,),
        ).fetchall()

        cases = conn.execute(
            """
            SELECT c.id, c.name
            FROM case_patterns cp
            JOIN cases c ON cp.case_id = c.id
            WHERE cp.pattern_id = ?
            """,
            (pattern_id,),
        ).fetchall()

    return {
        "composites": [dict(r) for r in composites],
        "cases":      [dict(r) for r in cases],
    }


def _delete_pattern_cascade(pattern_id: int) -> list[str]:
    """패턴과 이를 구성요소로 참조하는 COMPOSITE 패턴을 모두 삭제한다.

    ref_pattern_id 에 ON DELETE CASCADE 가 없으므로:
      1. 삭제 대상 패턴 전체(target + composite들)의 ref_pattern_id 참조를 먼저 제거
      2. 그 후 실제 DELETE 수행

    이 순서를 지키지 않으면 composite 자신이 또 다른 composite 에 참조될 때 FK 오류 발생.

    Returns:
        함께 삭제된 COMPOSITE 패턴 이름 목록
    """
    with get_conn(DB_PATH) as conn:
        composites = conn.execute(
            """
            SELECT DISTINCT p.id, p.name
            FROM pattern_components pc
            JOIN patterns p ON pc.pattern_id = p.id
            WHERE pc.ref_pattern_id = ?
            """,
            (pattern_id,),
        ).fetchall()

        composite_ids   = [c["id"]   for c in composites]
        deleted_names   = [c["name"] for c in composites]

        # 삭제할 패턴 전체에 대해 ref_pattern_id 참조를 일괄 제거 (FK 제약 해제)
        for pid in composite_ids + [pattern_id]:
            conn.execute("DELETE FROM pattern_components WHERE ref_pattern_id=?", (pid,))

        # 이제 안전하게 삭제 (pattern_steps / case_patterns 은 ON DELETE CASCADE 로 자동 처리)
        for cid in composite_ids:
            conn.execute("DELETE FROM patterns WHERE id=?", (cid,))

        conn.execute("DELETE FROM patterns WHERE id=?", (pattern_id,))

    return deleted_names


def _update_pattern_from_dict(pattern_id: int, p: dict) -> None:
    """기존 문제 패턴을 p 의 내용으로 덮어쓴다. steps / components 는 재삽입."""
    with get_conn(DB_PATH) as conn:
        conn.execute(
            """
            UPDATE patterns SET
              name=?, type=?, description=?, keywords=?,
              pattern=?, event_dedup_window_sec=?,
              step_dedup=?, non_overlapping=?,
              window_sec=?, count_threshold=?, count_unique_only=?,
              trigger_pattern=?, absent_pattern=?,
              operator=?, weight=?, is_required=?,
              analysis_guidelines=?
            WHERE id=?
            """,
            (
                p["name"], p["type"], p.get("description", ""),
                json.dumps(p.get("keywords", []), ensure_ascii=False),
                p.get("pattern"),      p.get("event_dedup_window_sec"),
                int(bool(p.get("step_dedup", False))),
                int(bool(p.get("non_overlapping", False))),
                p.get("window_sec"),   p.get("count_threshold"),
                int(bool(p.get("count_unique_only", False))),
                p.get("trigger_pattern"), p.get("absent_pattern"),
                p.get("operator"),
                float(p.get("weight", 1.0)),
                int(bool(p.get("is_required", False))),
                p.get("analysis_guidelines", ""),
                pattern_id,
            ),
        )

        # steps 재삽입
        conn.execute("DELETE FROM pattern_steps WHERE pattern_id=?", (pattern_id,))
        for i, step in enumerate(p.get("steps") or []):
            conn.execute(
                "INSERT INTO pattern_steps (pattern_id, step_order, pattern) VALUES (?,?,?)",
                (pattern_id, i, step),
            )

        # components 재삽입
        conn.execute("DELETE FROM pattern_components WHERE pattern_id=?", (pattern_id,))
        for i, comp_name in enumerate(p.get("components") or []):
            ref_row = conn.execute(
                "SELECT id FROM patterns WHERE name=?", (comp_name,)
            ).fetchone()
            if ref_row:
                conn.execute(
                    "INSERT INTO pattern_components (pattern_id, component_order, ref_pattern_id) VALUES (?,?,?)",
                    (pattern_id, i, ref_row["id"]),
                )


# insert_pattern 은 core.pattern_db 에서 import


# ── 페이지 ────────────────────────────────────────────────────────────────────

st.header("🎯 문제 패턴 관리")

# ── 사이드바 ──────────────────────────────────────────────────────────────────

with st.sidebar:
    st.subheader("🔧 도구")
    if st.button("기본값 초기화 (YAML 재시드)", use_container_width=True):
        yaml_path = Path(__file__).parent.parent / "config" / "patterns" / "default_patterns.yaml"
        count = seed_reset(yaml_path, DB_PATH)
        st.success(f"{count}개 문제 패턴으로 초기화 완료")
        st.rerun()

# ── 문제 패턴 목록 ────────────────────────────────────────────────────────────

TYPE_OPTIONS = ["전체", "PRESENCE", "SEQUENCE", "WINDOW", "ABSENCE", "COMPOSITE"]
type_filter = st.selectbox("타입 필터", TYPE_OPTIONS)

patterns = _load_patterns(type_filter)
st.subheader(f"문제 패턴 목록 ({len(patterns)}개)")

if not patterns:
    st.info("등록된 문제 패턴이 없습니다.")
else:
    for p in patterns:
        req_tag = " ⭐필수" if p.get("is_required") else ""
        label = f"**[{p['id']}] {p['name']}** — {p['type']}  |  weight={p['weight']}{req_tag}"
        with st.expander(label, expanded=False):
            st.markdown(f"**설명**: {p.get('description') or '(없음)'}")
            kws = p.get("keywords") or []
            st.markdown(f"**키워드**: `{'`, `'.join(kws)}`" if kws else "**키워드**: (없음)")

            # 타입별 추가 정보
            ptype = p["type"]
            if ptype == "PRESENCE":
                st.code(f"regex: {p.get('pattern')}", language="text")
                if p.get("event_dedup_window_sec") is not None:
                    st.caption(f"dedup 윈도우: {p['event_dedup_window_sec']}초")

            elif ptype == "SEQUENCE":
                steps = _load_steps(p["id"])
                st.markdown("**Steps**:")
                for i, s in enumerate(steps, 1):
                    st.code(f"{i}. {s}", language="text")

            elif ptype == "WINDOW":
                st.code(f"regex: {p.get('pattern')}", language="text")
                st.caption(
                    f"window={p.get('window_sec')}초  |  threshold={p.get('count_threshold')}회"
                    f"  |  unique_only={bool(p.get('count_unique_only'))}"
                )

            elif ptype == "ABSENCE":
                st.code(f"trigger : {p.get('trigger_pattern')}", language="text")
                st.code(f"absent  : {p.get('absent_pattern')}", language="text")
                st.caption(f"window: {p.get('window_sec')}초")

            elif ptype == "COMPOSITE":
                comps = _load_components(p["id"])
                st.markdown(f"**연산자**: `{p.get('operator')}`")
                st.markdown(f"**구성 문제 패턴**: {', '.join(comps)}")

            # 분석지침 표시
            guidelines = p.get("analysis_guidelines") or ""
            if guidelines.strip():
                with st.expander("📌 분석지침", expanded=False):
                    st.text(guidelines)
            else:
                st.caption("분석지침: (미등록)")

            # 수정 / 삭제 버튼
            btn_col1, btn_col2, _ = st.columns([1, 1, 4])
            if btn_col1.button("수정", key=f"edit_btn_{p['id']}"):
                st.session_state["editing_pattern_id"] = p["id"]
                st.session_state[f"edit_type_{p['id']}"] = p["type"]
                st.session_state.pop("delete_confirm_id", None)
                st.rerun()
            if btn_col2.button("삭제", key=f"del_btn_{p['id']}", type="secondary"):
                st.session_state["delete_confirm_id"] = p["id"]
                st.rerun()

            # ── 삭제 확인 UI ──────────────────────────────────────────────
            if st.session_state.get("delete_confirm_id") == p["id"]:
                deps = _find_dependencies(p["id"])
                composites = deps["composites"]
                cases      = deps["cases"]

                st.warning(f"**'{p['name']}'** 을(를) 삭제하시겠습니까?")

                if composites:
                    names = ", ".join(f"`{c['name']}`" for c in composites)
                    st.error(
                        f"⚠️ 이 패턴을 구성요소로 참조하는 COMPOSITE 패턴이 있습니다: {names}\n\n"
                        "이 패턴만 삭제하면 FK 오류가 발생합니다. **의존성 포함 삭제**를 사용하세요."
                    )
                if cases:
                    names = ", ".join(f"`{c['name']}`" for c in cases)
                    st.info(f"ℹ️ 연결된 케이스: {names} (삭제 시 연결만 해제, 케이스 자체는 유지됩니다)")

                dc_col1, dc_col2, dc_col3 = st.columns([1, 2, 3])

                # 이 패턴만 삭제 (의존성 없을 때만 활성화)
                solo_disabled = bool(composites)
                if dc_col1.button(
                    "삭제",
                    key=f"del_solo_{p['id']}",
                    type="primary",
                    disabled=solo_disabled,
                ):
                    _delete_pattern(p["id"])
                    st.session_state.pop("delete_confirm_id", None)
                    if st.session_state.get("editing_pattern_id") == p["id"]:
                        st.session_state.pop("editing_pattern_id", None)
                    st.rerun()

                # 의존성 포함 삭제 (참조 COMPOSITE 패턴도 함께 삭제)
                if composites:
                    if dc_col2.button(
                        f"의존성 포함 삭제 ({len(composites)}개)",
                        key=f"del_cascade_{p['id']}",
                        type="primary",
                    ):
                        deleted = _delete_pattern_cascade(p["id"])
                        st.session_state.pop("delete_confirm_id", None)
                        if st.session_state.get("editing_pattern_id") == p["id"]:
                            st.session_state.pop("editing_pattern_id", None)
                        msg = f"'{p['name']}' 삭제 완료"
                        if deleted:
                            msg += f" (함께 삭제된 패턴: {', '.join(deleted)})"
                        st.toast(msg)
                        st.rerun()

                if dc_col3.button("취소", key=f"del_cancel_{p['id']}"):
                    st.session_state.pop("delete_confirm_id", None)
                    st.rerun()

# ── 수정 폼 ───────────────────────────────────────────────────────────────────

editing_id = st.session_state.get("editing_pattern_id")
if editing_id is not None:
    with get_conn(DB_PATH) as _conn:
        _row = _conn.execute("SELECT * FROM patterns WHERE id=?", (editing_id,)).fetchone()
    if _row is None:
        st.session_state.pop("editing_pattern_id", None)
    else:
        ep = dict(_row)
        ep["keywords"] = json.loads(ep["keywords"]) if ep["keywords"] else []
        ep_steps = _load_steps(editing_id)
        ep_comps = _load_components(editing_id)

        st.divider()
        st.subheader(f"✏️ 문제 패턴 수정 — [{ep['id']}] {ep['name']}")

        _edit_type_key = f"edit_type_{editing_id}"
        if _edit_type_key not in st.session_state:
            st.session_state[_edit_type_key] = ep["type"]

        render_type_selector(_edit_type_key)

        _edit_all_names = [p["name"] for p in _load_patterns() if p["id"] != editing_id]
        ep_dict, edit_cancelled = render_pattern_form(
            f"edit_pattern_form_{editing_id}",
            _edit_type_key,
            _edit_all_names,
            initial_values={**ep, "steps": ep_steps, "components": ep_comps},
            submit_label="저장",
            show_cancel=True,
            clear_on_submit=False,
        )

        if ep_dict is not None:
            try:
                _update_pattern_from_dict(editing_id, ep_dict)
                st.success(f"문제 패턴 '{ep_dict['name']}' 수정 완료")
                st.session_state.pop("editing_pattern_id", None)
                st.session_state.pop(_edit_type_key, None)
                st.rerun()
            except Exception as _e:
                st.exception(_e)

        if edit_cancelled:
            st.session_state.pop("editing_pattern_id", None)
            st.session_state.pop(_edit_type_key, None)
            st.rerun()

st.divider()

# ── 문제 패턴 추가 ────────────────────────────────────────────────────────────

st.subheader("➕ 문제 패턴 추가")

add_mode = st.radio("추가 방식", ["자연어로 생성 (LLM)", "직접 입력"], horizontal=True)

# ── 자연어 생성 ────────────────────────────────────────────────────────────────

if add_mode == "자연어로 생성 (LLM)":
    nl_input = st.text_area(
        "문제 상황을 자연어로 설명하세요",
        placeholder="NVMe 드라이브 초기화 중 타임아웃이 발생하고 이후 I/O 오류가 연속으로 발생한다.",
        height=120,
    )

    if st.button("LLM으로 문제 패턴 생성", type="primary"):
        if not nl_input.strip():
            st.error("설명을 입력해 주세요.")
        else:
            with st.spinner("LLM이 문제 패턴을 생성 중..."):
                try:
                    result = get_generator().generate(nl_input)
                except Exception as e:
                    st.exception(e)
                    st.stop()

            st.session_state["generated_pattern"] = result
            st.success("문제 패턴 생성 완료! 아래에서 확인 후 저장하세요.")

    gen = st.session_state.get("generated_pattern")
    if gen is not None:
        import json as _json
        pat = gen.pattern
        gp  = _json.loads(pat.model_dump_json())

        st.subheader("✏️ 생성된 문제 패턴 — 수정 후 저장")

        with st.expander("LLM 원본 JSON 보기", expanded=False):
            st.json(gp)

        if gen.relations:
            with st.expander("기존 문제 패턴과의 관계", expanded=True):
                for rel in gen.relations:
                    st.markdown(f"- `{rel.target_name}` — **{rel.type}**: {rel.reason}")

        _gen_type = gp.get("type", "PRESENCE")
        if _gen_type not in ["PRESENCE", "SEQUENCE", "WINDOW", "ABSENCE", "COMPOSITE"]:
            _gen_type = "PRESENCE"
        if "gen_pattern_type" not in st.session_state:
            st.session_state["gen_pattern_type"] = _gen_type

        _gen_all_names = [p["name"] for p in _load_patterns()]
        render_type_selector("gen_pattern_type")
        gp_result, gen_cancelled = render_pattern_form(
            "gen_edit_form",
            "gen_pattern_type",
            _gen_all_names,
            initial_values={
                **gp,
                "keywords": gp.get("keywords") or [],
                "steps":    gp.get("steps") or [],
                "components": gp.get("components") or [],
            },
            submit_label="DB에 저장",
            show_cancel=True,
        )

        if gp_result is not None:
            try:
                pid = insert_pattern(gp_result)
                st.success(f"문제 패턴 '{gp_result['name']}' 저장 완료 (id={pid})")
                del st.session_state["generated_pattern"]
                st.session_state.pop("gen_pattern_type", None)
                st.rerun()
            except Exception as _e:
                st.exception(_e)

        if gen_cancelled:
            del st.session_state["generated_pattern"]
            st.session_state.pop("gen_pattern_type", None)
            st.rerun()

# ── 직접 입력 ─────────────────────────────────────────────────────────────────

else:
    _direct_all_names = [p["name"] for p in _load_patterns()]
    render_type_selector("direct_input_type")
    m_result, _ = render_pattern_form(
        "manual_pattern_form",
        "direct_input_type",
        _direct_all_names,
        submit_label="패턴 추가",
    )
    if m_result is not None:
        try:
            pid = insert_pattern(m_result)
            st.success(f"문제 패턴 '{m_result['name']}' 추가 완료 (id={pid})")
            st.rerun()
        except Exception as _e:
            st.exception(_e)
