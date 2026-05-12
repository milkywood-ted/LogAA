"""
pages/5_📋_분석_프로파일_관리.py

분석 프로파일 관리 페이지.
  - 프로파일 CRUD (JSON 파일 기반, config/profiles/*.json)
  - 사전지식 CRUD (SQLite 구조화 지식 직접 입력)
"""

from __future__ import annotations

import streamlit as st

st.set_page_config(
    page_title = "Kernel Log Analyzer",
    page_icon  = "🔍",
    layout     = "wide",
    initial_sidebar_state = "expanded",
)

from core.db import DB_PATH, get_conn
from core.profile import (
    PROFILES_DIR,
    Profile,
    load_profiles, load_profile, save_profile, delete_profile, rename_profile,
    load_knowledge, DomainKnowledge,
    add_knowledge_to_chromadb, remove_knowledge_from_chromadb,
)

# ── 사전지식 DB 헬퍼 ──────────────────────────────────────────────────────────

def _create_knowledge(name: str, store_type: str, content: str, description: str) -> int:
    with get_conn(DB_PATH) as conn:
        cur = conn.execute(
            "INSERT INTO domain_knowledge (name, store_type, content, description) VALUES (?, ?, ?, ?)",
            (name, store_type, content, description),
        )
        return cur.lastrowid


def _update_knowledge(kid: int, name: str, store_type: str, content: str, description: str) -> None:
    with get_conn(DB_PATH) as conn:
        conn.execute(
            "UPDATE domain_knowledge SET name=?, store_type=?, content=?, description=?, "
            "updated_at=datetime('now') WHERE id=?",
            (name, store_type, content, description, kid),
        )


def _delete_knowledge(kid: int) -> None:
    with get_conn(DB_PATH) as conn:
        conn.execute("DELETE FROM domain_knowledge WHERE id=?", (kid,))
    remove_knowledge_from_chromadb(kid)


# ── 페이지 ────────────────────────────────────────────────────────────────────

st.header("📋 분석 프로파일")
st.caption(
    "분석 프로파일은 특정 도메인에 포커싱하여 분석 정확도를 높이기 위한 단위입니다.  \n"
    "분석 시 복수 프로파일을 선택할 수 있으며, 구성 요소는 병합되어 파이프라인에 적용됩니다.  \n"
    f"프로파일은 `{PROFILES_DIR}` 폴더에 JSON 파일로 저장됩니다."
)

tab_profiles, tab_knowledge = st.tabs(["📋 프로파일 관리", "📖 사전지식 관리"])

# ══════════════════════════════════════════════════════════════════════════════
# 탭 1: 프로파일 관리
# ══════════════════════════════════════════════════════════════════════════════

with tab_profiles:
    profiles = load_profiles(PROFILES_DIR)
    all_knowledge = load_knowledge(DB_PATH)
    all_knowledge_names = [k.name for k in all_knowledge]

    # ── 프로파일 목록 ─────────────────────────────────────────────────────────

    st.subheader(f"프로파일 목록 ({len(profiles)}개)")

    if not profiles:
        st.info("등록된 프로파일이 없습니다. 아래 폼으로 추가하세요.")
    else:
        for prof in profiles:
            kw_display = ", ".join(prof.prefilter_keywords) if prof.prefilter_keywords else "(없음)"
            refs_display = ", ".join(f"`{r}`" for r in prof.knowledge_refs) if prof.knowledge_refs else "(없음)"

            with st.expander(f"**{prof.name}**", expanded=False):
                if prof.description:
                    st.caption(prof.description)
                st.markdown(f"**사전정제 키워드**: `{kw_display}`")
                st.markdown(f"**사전지식 참조**: {refs_display}")
                if prof.analysis_guidelines.strip():
                    with st.expander("분석 지침 보기", expanded=False):
                        st.text(prof.analysis_guidelines)

                col_edit, col_del = st.columns([1, 1])
                with col_edit:
                    if st.button("수정", key=f"pedit_{prof.name}"):
                        st.session_state[f"p_editing_{prof.name}"] = True

                if st.session_state.get(f"p_editing_{prof.name}"):
                    with st.form(f"p_edit_form_{prof.name}"):
                        e_name  = st.text_input("이름", value=prof.name)
                        e_desc  = st.text_input("설명", value=prof.description)
                        e_guide = st.text_area(
                            "분석 지침 (LLM system prompt에 주입)",
                            value=prof.analysis_guidelines,
                            height=150,
                        )
                        e_kws = st.text_input(
                            "사전정제 키워드 (쉼표 구분)",
                            value=", ".join(prof.prefilter_keywords),
                        )
                        e_refs = st.multiselect(
                            "사전지식 참조",
                            options=all_knowledge_names,
                            default=[r for r in prof.knowledge_refs if r in all_knowledge_names],
                        )
                        if st.form_submit_button("저장"):
                            updated = Profile(
                                name                = e_name.strip() or prof.name,
                                description         = e_desc.strip(),
                                analysis_guidelines = e_guide.strip(),
                                prefilter_keywords  = [k.strip() for k in e_kws.split(",") if k.strip()],
                                knowledge_refs      = e_refs,
                            )
                            if updated.name != prof.name:
                                rename_profile(prof.name, updated, PROFILES_DIR)
                            else:
                                save_profile(updated, PROFILES_DIR)
                            st.session_state[f"p_editing_{prof.name}"] = False
                            st.rerun()

                with col_del:
                    if st.button("삭제", key=f"pdel_{prof.name}", type="secondary"):
                        delete_profile(prof.name, PROFILES_DIR)
                        st.rerun()

    st.divider()

    # ── 프로파일 추가 폼 ──────────────────────────────────────────────────────

    st.subheader("➕ 프로파일 추가")

    with st.form("add_profile_form"):
        p_name  = st.text_input("프로파일 이름 *", placeholder="ATA 스토리지")
        p_desc  = st.text_input("설명", placeholder="ATA/SATA 스토리지 관련 커널 로그 분석 프로파일")
        p_guide = st.text_area(
            "분석 지침 (LLM system prompt에 주입)",
            placeholder=(
                "이 로그는 ATA/SATA 스토리지 컨트롤러 관련 문제를 분석합니다.\n"
                "ata, sata, scsi, sd, blk 관련 메시지에 집중하세요.\n"
                "디바이스 재설정(reset)과 타임아웃(timeout) 패턴을 특히 주의하세요."
            ),
            height=150,
        )
        p_kws = st.text_input(
            "사전정제 키워드 (쉼표 구분) — Stage 1 whitelist",
            placeholder="ata, sata, scsi, timeout, error, reset",
        )
        p_refs = st.multiselect(
            "사전지식 참조",
            options=all_knowledge_names,
            help="선택한 사전지식의 이름이 JSON에 저장됩니다.",
        )
        submitted = st.form_submit_button("프로파일 추가", type="primary")

    if submitted:
        if not p_name.strip():
            st.error("프로파일 이름을 입력하세요.")
        else:
            existing_names = [p.name for p in profiles]
            if p_name.strip() in existing_names:
                st.error(f"'{p_name.strip()}' 이름의 프로파일이 이미 존재합니다.")
            else:
                try:
                    new_profile = Profile(
                        name                = p_name.strip(),
                        description         = p_desc.strip(),
                        analysis_guidelines = p_guide.strip(),
                        prefilter_keywords  = [k.strip() for k in p_kws.split(",") if k.strip()],
                        knowledge_refs      = p_refs,
                    )
                    path = save_profile(new_profile, PROFILES_DIR)
                    st.success(f"프로파일 **'{p_name}'** 추가 완료 (`{path.name}`)")
                    st.rerun()
                except Exception as exc:
                    st.error(f"추가 실패: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# 탭 2: 사전지식 관리
# ══════════════════════════════════════════════════════════════════════════════

with tab_knowledge:
    st.subheader("사전지식 관리")
    st.caption(
        "**SQLite** 타입: 구조화된 정보 (디바이스 특성, 스펙 등)를 직접 입력합니다.  \n"
        "**ChromaDB** 타입: 비구조화 문서 항목을 추적합니다 (실제 내용은 ChromaDB에 저장)."
    )

    all_knowledge = load_knowledge(DB_PATH)

    if not all_knowledge:
        st.info("등록된 사전지식이 없습니다. 아래 폼으로 추가하세요.")
    else:
        st.caption(f"총 {len(all_knowledge)}개")
        for dk in all_knowledge:
            label = f"**[{dk.id}] {dk.name}** ({dk.store_type})"
            with st.expander(label, expanded=False):
                if dk.description:
                    st.caption(dk.description)
                if dk.content.strip():
                    st.text_area("내용", value=dk.content, height=120, disabled=True,
                                 key=f"dk_preview_{dk.id}")
                    if dk.store_type == "chromadb":
                        st.caption("ChromaDB에 임베딩되어 분석 시 problem_text로 유사도 검색됩니다.")

                col_edit, col_del = st.columns([1, 1])
                with col_edit:
                    if st.button("수정", key=f"dkedit_{dk.id}"):
                        st.session_state[f"dk_editing_{dk.id}"] = True

                if st.session_state.get(f"dk_editing_{dk.id}"):
                    with st.form(f"dk_edit_form_{dk.id}"):
                        e_dname = st.text_input("이름", value=dk.name)
                        e_dtype = st.selectbox(
                            "저장소 타입",
                            ["sqlite", "chromadb"],
                            index=0 if dk.store_type == "sqlite" else 1,
                        )
                        e_ddesc = st.text_input("설명", value=dk.description)
                        e_dcont = st.text_area(
                            "내용 (LLM 컨텍스트에 주입 / ChromaDB 타입은 임베딩 대상)",
                            value=dk.content,
                            height=120,
                        )
                        if st.form_submit_button("저장"):
                            _update_knowledge(dk.id, e_dname, e_dtype, e_dcont, e_ddesc)
                            if e_dtype == "chromadb" and e_dcont.strip():
                                with st.spinner("ChromaDB 임베딩 중..."):
                                    add_knowledge_to_chromadb(dk.id, e_dname, e_dcont.strip())
                            elif e_dtype == "sqlite":
                                remove_knowledge_from_chromadb(dk.id)
                            st.session_state[f"dk_editing_{dk.id}"] = False
                            st.rerun()

                with col_del:
                    if st.button("삭제", key=f"dkdel_{dk.id}", type="secondary"):
                        _delete_knowledge(dk.id)
                        st.rerun()

    st.divider()

    # ── 사전지식 추가 폼 ──────────────────────────────────────────────────────

    st.subheader("➕ 사전지식 추가")

    with st.form("add_knowledge_form"):
        dk_name  = st.text_input("이름 *", placeholder="ATA 디바이스 특성")
        dk_type  = st.selectbox(
            "저장소 타입",
            ["sqlite", "chromadb"],
            help="sqlite: 구조화 정보 직접 입력 / chromadb: 비구조화 문서 (ChromaDB에 저장)",
        )
        dk_desc  = st.text_input("설명", placeholder="ATA 컨트롤러별 알려진 특성 및 한계값")
        dk_content = st.text_area(
            "내용 (SQLite 타입일 때만 LLM 컨텍스트에 주입)",
            placeholder=(
                "ata1: Samsung SSD 870 EVO\n"
                "  - 타임아웃 임계값: 30초\n"
                "  - 알려진 이슈: 펌웨어 RVT31 이전 버전에서 간헐적 NCQ 오류 발생\n"
            ),
            height=150,
        )
        dk_submitted = st.form_submit_button("사전지식 추가", type="primary")

    if dk_submitted:
        if not dk_name.strip():
            st.error("이름을 입력하세요.")
        else:
            try:
                kid = _create_knowledge(
                    dk_name.strip(), dk_type, dk_content.strip(), dk_desc.strip()
                )
                if dk_type == "chromadb" and dk_content.strip():
                    with st.spinner("ChromaDB 임베딩 중..."):
                        add_knowledge_to_chromadb(kid, dk_name.strip(), dk_content.strip())
                st.success(f"사전지식 **'{dk_name}'** 추가 완료 (id={kid})")
                st.rerun()
            except Exception as exc:
                st.error(f"추가 실패: {exc}")
