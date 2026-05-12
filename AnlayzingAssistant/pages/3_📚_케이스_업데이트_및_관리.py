"""
pages/3_📚_케이스_업데이트_및_관리.py

이력 업데이트 페이지.

케이스(문제 설명)와 문제 패턴을 한 화면에서 통합 입력하고 한 번에 저장한다.

입력 경로:
  - 수기 입력 (주요): 문제 설명 + 패턴 연결 → 케이스 등록 + ChromaDB 임베딩
  - 분석 결과 가져오기 (보조): 기존 분석 결과 자동 채움 → 검토 후 저장
"""

from __future__ import annotations

import json

import streamlit as st

st.set_page_config(
    page_title = "Kernel Log Analyzer",
    page_icon  = "🔍",
    layout     = "wide",
    initial_sidebar_state = "expanded",
)

from core.db import DB_PATH, get_conn
from core.kb_search import KBSearch
from core.pattern_db import insert_pattern
from core.profile import load_profiles
from ui.pattern_form import render_type_selector, render_pattern_form

# ── 공유 자원 ─────────────────────────────────────────────────────────────────

@st.cache_resource
def get_kb_search() -> KBSearch:
    return KBSearch(db_path=DB_PATH)


# ── DB 헬퍼 ───────────────────────────────────────────────────────────────────

def _load_all_patterns() -> list[dict]:
    with get_conn(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT id, name, type, description FROM patterns ORDER BY name"
        ).fetchall()
    return [dict(r) for r in rows]


def _load_cases() -> list[dict]:
    with get_conn(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT id, name, description, keywords, profile_refs, created_at FROM cases ORDER BY id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def _load_patterns_for_case(case_id: int) -> list[dict]:
    with get_conn(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT p.id, p.name, p.type
            FROM patterns p
            JOIN case_patterns cp ON cp.pattern_id = p.id
            WHERE cp.case_id = ?
            ORDER BY p.name
            """,
            (case_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def _save_case_with_patterns(
    name: str,
    description: str,
    keywords: list[str],
    existing_pattern_ids: list[int],
    new_patterns: list[dict],
    evidence_log: str,
    profile_refs: list[str] | None = None,
) -> int:
    """케이스 + 패턴을 한 트랜잭션으로 저장한다.

    새 패턴은 INSERT 후 케이스에 연결한다.
    Returns: 생성된 case_id
    """
    with get_conn(DB_PATH) as conn:
        # 케이스 저장
        cur = conn.execute(
            "INSERT INTO cases (name, description, keywords, profile_refs) VALUES (?, ?, ?, ?)",
            (name, description,
             json.dumps(keywords, ensure_ascii=False),
             json.dumps(profile_refs or [], ensure_ascii=False)),
        )
        case_id = cur.lastrowid

        # 새 패턴 저장 + case_patterns 연결 (같은 트랜잭션)
        all_pattern_ids = list(existing_pattern_ids)
        for np in new_patterns:
            # keywords 가 문자열로 저장된 경우 리스트로 변환
            if isinstance(np.get("keywords"), str):
                np = {**np, "keywords": [k.strip() for k in np["keywords"].split(",") if k.strip()]}
            pid = insert_pattern(np, conn=conn)
            all_pattern_ids.append(pid)

        # case_patterns 연결
        for pid in all_pattern_ids:
            conn.execute(
                "INSERT OR IGNORE INTO case_patterns (case_id, pattern_id) VALUES (?, ?)",
                (case_id, pid),
            )

    # ChromaDB 임베딩
    get_kb_search().add_case(case_id, name, description, keywords)

    return case_id


def _update_case(
    case_id: int,
    name: str,
    description: str,
    keywords: list[str],
    profile_refs: list[str] | None = None,
) -> None:
    with get_conn(DB_PATH) as conn:
        conn.execute(
            "UPDATE cases SET name=?, description=?, keywords=?, profile_refs=?, "
            "updated_at=datetime('now') WHERE id=?",
            (name, description,
             json.dumps(keywords, ensure_ascii=False),
             json.dumps(profile_refs or [], ensure_ascii=False),
             case_id),
        )
    get_kb_search().add_case(case_id, name, description, keywords)


def _delete_case(case_id: int) -> None:
    with get_conn(DB_PATH) as conn:
        conn.execute("DELETE FROM cases WHERE id=?", (case_id,))
    get_kb_search().remove_case(case_id)


def _link_pattern(case_id: int, pattern_id: int) -> None:
    with get_conn(DB_PATH) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO case_patterns (case_id, pattern_id) VALUES (?, ?)",
            (case_id, pattern_id),
        )


def _unlink_pattern(case_id: int, pattern_id: int) -> None:
    with get_conn(DB_PATH) as conn:
        conn.execute(
            "DELETE FROM case_patterns WHERE case_id=? AND pattern_id=?",
            (case_id, pattern_id),
        )


# ── session_state 초기화 ──────────────────────────────────────────────────────

def _init_state() -> None:
    defaults = {
        "hu_case_name":          "",
        "hu_case_desc":          "",
        "hu_case_kws":           "",
        "hu_evidence":           "",
        "hu_selected_patterns":  [],   # list[str] — 기존 패턴 이름 목록
        "hu_new_patterns":       [],   # list[dict]
        "hu_np_type":            "PRESENCE",
        "hu_profile_refs":       [],   # list[str] — 연결할 분석 프로파일 이름 목록
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def _apply_prefill(prefill: dict) -> None:
    """분석 결과 또는 외부 데이터를 폼 session_state 에 적용한다."""
    if "case_name" in prefill:
        st.session_state["hu_case_name"] = prefill["case_name"]
    if "case_desc" in prefill:
        st.session_state["hu_case_desc"] = prefill["case_desc"]
    if "case_kws" in prefill:
        st.session_state["hu_case_kws"] = prefill["case_kws"]
    if "evidence" in prefill:
        st.session_state["hu_evidence"] = prefill["evidence"]
    if "selected_patterns" in prefill:
        st.session_state["hu_selected_patterns"] = prefill["selected_patterns"]


def _reset_form() -> None:
    for k in ("hu_case_name", "hu_case_desc", "hu_case_kws", "hu_evidence"):
        st.session_state[k] = ""
    st.session_state["hu_selected_patterns"] = []
    st.session_state["hu_new_patterns"] = []
    st.session_state["hu_profile_refs"] = []


# ── 페이지 ────────────────────────────────────────────────────────────────────

_init_state()

# 저장/초기화 완료 후 위젯 생성 전에 폼을 리셋 (위젯 생성 이후 session_state 직접 수정 금지 우회)
if st.session_state.pop("_pending_reset", False):
    _reset_form()

st.header("📚 이력 업데이트")

all_patterns = _load_all_patterns()
pattern_name_to_id = {p["name"]: p["id"] for p in all_patterns}
all_pattern_names  = [p["name"] for p in all_patterns]

tab_new, tab_result, tab_list = st.tabs(
    ["✍️ 수기 입력", "📊 분석 결과에서 가져오기", "📋 케이스 목록"]
)

# ══════════════════════════════════════════════════════════════════════════════
# 탭 1: 수기 입력
# ══════════════════════════════════════════════════════════════════════════════

with tab_new:
    st.subheader("케이스 + 문제 패턴 통합 입력")
    st.caption("케이스(문제 설명)와 연결할 문제 패턴을 함께 등록합니다.")

    # ── 케이스 정보 ───────────────────────────────────────────────────────────

    st.markdown("#### 케이스 정보")

    case_name = st.text_input(
        "케이스 이름 *",
        value=st.session_state["hu_case_name"],
        placeholder="예) ATA 드라이브 반복 타임아웃",
        key="hu_case_name",
    )
    case_desc = st.text_area(
        "문제 설명 * (BGE-M3 임베딩 대상 — 구체적으로 작성)",
        value=st.session_state["hu_case_desc"],
        placeholder="ATA 드라이브에서 반복적인 타임아웃이 발생하며 I/O 오류가 누적되는 문제. "
                    "드라이브 재설정 후에도 동일 증상 반복.",
        height=100,
        key="hu_case_desc",
    )
    case_kws_raw = st.text_input(
        "케이스 키워드 (쉼표 구분) — Stage 3 로그 재필터링에 사용",
        value=st.session_state["hu_case_kws"],
        placeholder="ata, timeout, error, reset",
        key="hu_case_kws",
    )
    evidence_log = st.text_area(
        "관련 로그 / Evidence (선택)",
        value=st.session_state["hu_evidence"],
        placeholder="분석 근거가 된 핵심 로그 라인을 붙여넣으세요.",
        height=120,
        key="hu_evidence",
    )

    _all_profile_names = [p.name for p in load_profiles()]
    _valid_prefs = [n for n in st.session_state["hu_profile_refs"] if n in _all_profile_names]
    if _valid_prefs != st.session_state["hu_profile_refs"]:
        st.session_state["hu_profile_refs"] = _valid_prefs
    selected_profile_refs: list[str] = st.multiselect(
        "추천 분석 프로파일 (선택)",
        options=_all_profile_names,
        key="hu_profile_refs",
        help=(
            "이 케이스가 매칭될 때 자동으로 적용할 분석 프로파일을 지정합니다.  \n"
            "사용자가 별도로 선택한 프로파일과 함께 병합되어 Stage 5(리포트 생성)에 반영됩니다."
        ),
    )

    st.divider()

    # ── 문제 패턴 연결 ────────────────────────────────────────────────────────

    st.markdown("#### 문제 패턴 연결 (최소 1개 필수)")

    # DB에 없는 패턴명이 session_state에 남아있으면 제거
    _valid = [n for n in st.session_state["hu_selected_patterns"] if n in all_pattern_names]
    if _valid != st.session_state["hu_selected_patterns"]:
        st.session_state["hu_selected_patterns"] = _valid

    selected_pattern_names: list[str] = st.multiselect(
        "기존 패턴에서 선택",
        options=all_pattern_names,
        key="hu_selected_patterns",
    )


    # 새 패턴 인라인 추가
    with st.expander("➕ 새 패턴 추가"):
        render_type_selector("hu_np_type")
        _pending_names = [p["name"] for p in st.session_state["hu_new_patterns"]]
        np_result, _ = render_pattern_form(
            "new_pattern_form",
            "hu_np_type",
            all_pattern_names,
            exclude_names=all_pattern_names + _pending_names,
            submit_label="목록에 추가",
        )
        if np_result is not None:
            st.session_state["hu_new_patterns"].append(np_result)
            st.rerun()


    new_patterns: list[dict] = st.session_state["hu_new_patterns"]
    if new_patterns:
        st.markdown("**추가 예정 패턴 (신규)**")
        for i, np_item in enumerate(new_patterns):
            col_info, col_del = st.columns([5, 1])
            req_tag = " ⭐필수" if np_item.get("is_required") else ""
            ptype = np_item.get("type", "PRESENCE")
            if ptype == "PRESENCE":
                detail = f"`{np_item.get('pattern', '')}`"
            elif ptype == "SEQUENCE":
                steps = np_item.get("steps") or []
                detail = f"{len(steps)}개 step"
            elif ptype == "WINDOW":
                detail = f"`{np_item.get('pattern', '')}` / {np_item.get('window_sec')}초 / {np_item.get('count_threshold')}회"
            elif ptype == "ABSENCE":
                detail = f"trigger=`{np_item.get('trigger_pattern', '')}` absent=`{np_item.get('absent_pattern', '')}`"
            elif ptype == "COMPOSITE":
                comps = np_item.get("components") or []
                detail = f"{np_item.get('operator')} [{', '.join(comps)}]"
            else:
                detail = ""
            col_info.markdown(
                f"- `{np_item['name']}` **[{ptype}]** — {detail}  "
                f"(weight={np_item['weight']}{req_tag})"
            )
            if col_del.button("삭제", key=f"del_np_{i}"):
                st.session_state["hu_new_patterns"].pop(i)
                st.rerun()

    total_patterns = len(selected_pattern_names) + len(new_patterns)

    st.divider()

    # ── 저장 ──────────────────────────────────────────────────────────────────

    col_save, col_reset = st.columns([2, 1])
    save_btn  = col_save.button("💾 케이스 + 패턴 저장", type="primary", use_container_width=True)
    reset_btn = col_reset.button("🔄 초기화", use_container_width=True)

    if reset_btn:
        st.session_state["_pending_reset"] = True
        st.rerun()

    if save_btn:
        # 유효성 검사
        errors = []
        if not case_name.strip():
            errors.append("케이스 이름을 입력하세요.")
        if not case_desc.strip():
            errors.append("문제 설명을 입력하세요.")
        if total_patterns == 0:
            errors.append("최소 1개 이상의 문제 패턴을 연결하거나 추가해야 합니다.")

        if errors:
            for e in errors:
                st.error(e)
        else:
            try:
                kws = [k.strip() for k in case_kws_raw.split(",") if k.strip()]
                existing_ids = [pattern_name_to_id[n] for n in selected_pattern_names]
                case_id = _save_case_with_patterns(
                    name                = case_name.strip(),
                    description         = case_desc.strip(),
                    keywords            = kws,
                    existing_pattern_ids = existing_ids,
                    new_patterns        = new_patterns,
                    evidence_log        = evidence_log,
                    profile_refs        = st.session_state.get("hu_profile_refs", []),
                )
                st.success(
                    f"케이스 **'{case_name}'** 등록 완료 "
                    f"(id={case_id}, 패턴 {total_patterns}개 연결)"
                )
                st.session_state["_pending_reset"] = True
                st.rerun()
            except Exception as exc:
                st.error(f"저장 실패: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# 탭 2: 분석 결과에서 가져오기
# ══════════════════════════════════════════════════════════════════════════════

with tab_result:
    st.subheader("분석 결과에서 자동 채움")

    result = st.session_state.get("last_result")
    problem_text: str = st.session_state.get("problem_text", "")

    if result is None:
        st.info("저장된 분석 결과가 없습니다. **🔍 분석** 페이지에서 먼저 로그를 분석해 주세요.")
    else:
        # 분석 요약 표시
        verdict_icon = {"문제": "🔴", "불확실": "🟡", "알 수 없음": "⚪"}.get(result.verdict, "⚪")
        st.markdown(f"**판정**: {verdict_icon} {result.verdict}")
        if problem_text:
            st.caption(f"문제 설명: {problem_text}")

        mc = result.matched_case
        mr = result.match_result

        if mc:
            st.info(f"📚 매칭 케이스: **{mc.name}** (관련성 {mc.relevance_score:.0%})")

        if mr and mr.matched:
            matched_names = [p.name for p in mr.matched]
            st.markdown(f"**매칭된 패턴** ({len(matched_names)}개): " + ", ".join(f"`{n}`" for n in matched_names))

        st.divider()

        # 불러오기 데이터 미리보기
        st.markdown("#### 불러올 데이터 미리보기")

        # 케이스 이름 제안: 매칭된 케이스가 있으면 "[기반] 케이스명", 없으면 문제 설명 첫 줄
        suggested_name = ""
        if mc:
            suggested_name = f"[기반] {mc.name}"
        elif problem_text:
            suggested_name = problem_text.split("\n")[0][:60]

        suggested_desc  = problem_text or (mc.description if mc else "")
        suggested_kws   = ", ".join(mc.keywords) if mc else ""
        suggested_patterns = [p.name for p in mr.matched] if mr and mr.matched else []

        # evidence 로그 수집 (매칭된 패턴 evidence)
        evidence_lines: list[str] = []
        if mr and mr.matched:
            for pm in mr.matched:
                if pm.evidence:
                    evidence_lines.extend(ll.render() for ll in pm.evidence)

        col_preview, _ = st.columns([3, 1])
        with col_preview:
            st.text_input("케이스 이름 (제안)", value=suggested_name, disabled=True)
            st.text_area("문제 설명 (제안)", value=suggested_desc[:300], height=80, disabled=True)
            if suggested_kws:
                st.text_input("키워드 (제안)", value=suggested_kws, disabled=True)
            if suggested_patterns:
                st.multiselect("연결할 패턴 (제안)", options=suggested_patterns, default=suggested_patterns, disabled=True)
            if evidence_lines:
                st.text_area("Evidence 로그", value="\n".join(evidence_lines[:20]), height=100, disabled=True)

        st.divider()

        if st.button("✏️ 수기 입력 폼에 불러오기", type="primary"):
            prefill = {
                "case_name":        suggested_name,
                "case_desc":        suggested_desc,
                "case_kws":         suggested_kws,
                "evidence":         "\n".join(evidence_lines[:20]),
                "selected_patterns": [
                    n for n in suggested_patterns if n in all_pattern_names
                ],
            }
            _apply_prefill(prefill)
            st.success("수기 입력 탭에 데이터를 채웠습니다. **✍️ 수기 입력** 탭으로 이동하여 검토 후 저장하세요.")


# ══════════════════════════════════════════════════════════════════════════════
# 탭 3: 케이스 목록
# ══════════════════════════════════════════════════════════════════════════════

with tab_list:
    st.subheader("등록된 케이스 목록")

    with st.sidebar:
        st.subheader("🔧 도구")
        if st.button("ChromaDB 전체 동기화", use_container_width=True):
            st.session_state["confirm_chroma_sync"] = True

        if st.session_state.get("confirm_chroma_sync"):
            st.warning("케이스 전체를 ChromaDB에 재임베딩합니다. 계속하시겠습니까?")
            c1, c2 = st.columns(2)
            if c1.button("✅ 확인", key="chroma_sync_ok"):
                st.session_state["confirm_chroma_sync"] = False
                with st.spinner("동기화 중..."):
                    count = get_kb_search().sync_from_db()
                st.success(f"{count}개 케이스 동기화 완료")
            if c2.button("❌ 취소", key="chroma_sync_cancel"):
                st.session_state["confirm_chroma_sync"] = False

    cases = _load_cases()

    if not cases:
        st.info("등록된 케이스가 없습니다.")
    else:
        st.caption(f"총 {len(cases)}개 케이스")
        for case in cases:
            kws = json.loads(case["keywords"]) if isinstance(case["keywords"], str) else case["keywords"]
            linked = _load_patterns_for_case(case["id"])

            case_profile_refs = json.loads(case["profile_refs"]) if case.get("profile_refs") else []
            with st.expander(f"**[{case['id']}] {case['name']}**", expanded=False):
                st.markdown(f"**설명**: {case['description']}")
                st.markdown(f"**키워드**: `{'`, `'.join(kws)}`" if kws else "**키워드**: (없음)")
                if linked:
                    linked_names = ", ".join(f"`{p['name']}`" for p in linked)
                    st.markdown(f"**연결 패턴**: {linked_names}")
                else:
                    st.markdown("**연결 패턴**: (없음)")
                if case_profile_refs:
                    st.markdown(f"**추천 프로파일**: `{'`, `'.join(case_profile_refs)}`")
                st.caption(f"생성: {case['created_at']}")

                col_edit, col_del = st.columns([1, 1])

                with col_edit:
                    if st.button("수정", key=f"edit_{case['id']}"):
                        st.session_state[f"editing_{case['id']}"] = True

                if st.session_state.get(f"editing_{case['id']}"):
                    with st.form(f"edit_form_{case['id']}"):
                        new_name = st.text_input("이름", value=case["name"])
                        new_desc = st.text_area("설명", value=case["description"])
                        new_kws_raw = st.text_input(
                            "키워드 (쉼표 구분)",
                            value=", ".join(kws),
                        )
                        linked_ids = [p["id"] for p in linked]
                        selected_labels = st.multiselect(
                            "연결 패턴",
                            options=all_pattern_names,
                            default=[p["name"] for p in linked],
                        )
                        _all_profile_names_edit = [p.name for p in load_profiles()]
                        _valid_case_refs = [n for n in case_profile_refs if n in _all_profile_names_edit]
                        new_profile_refs = st.multiselect(
                            "추천 분석 프로파일",
                            options=_all_profile_names_edit,
                            default=_valid_case_refs,
                            help=(
                                "이 케이스가 매칭될 때 자동으로 적용할 분석 프로파일을 지정합니다."
                            ),
                        )
                        sub = st.form_submit_button("저장")
                        if sub:
                            _update_case(
                                case["id"], new_name, new_desc,
                                [k.strip() for k in new_kws_raw.split(",") if k.strip()],
                                profile_refs=new_profile_refs,
                            )
                            new_linked_ids = [pattern_name_to_id[n] for n in selected_labels if n in pattern_name_to_id]
                            for pid in set(linked_ids) - set(new_linked_ids):
                                _unlink_pattern(case["id"], pid)
                            for pid in set(new_linked_ids) - set(linked_ids):
                                _link_pattern(case["id"], pid)
                            st.session_state[f"editing_{case['id']}"] = False
                            st.rerun()

                with col_del:
                    if st.button("삭제", key=f"del_{case['id']}", type="secondary"):
                        _delete_case(case["id"])
                        st.rerun()
