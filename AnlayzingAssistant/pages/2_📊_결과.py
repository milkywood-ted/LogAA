"""
pages/2_📊_결과.py

분석 결과 페이지.
  - 판정 배너 (문제 / 불확실 / 알 수 없음)
  - 진단 점수 및 매칭 케이스 정보
  - 매칭/미매칭 패턴 상세 + evidence 로그
  - Stage 1 정제 로그 (L_common)
  - Markdown 리포트
  - KB 추가 제안 (알 수 없음 경로)
"""

from __future__ import annotations

import streamlit as st

st.set_page_config(
    page_title = "Kernel Log Analyzer",
    page_icon  = "🔍",
    layout     = "wide",
    initial_sidebar_state = "expanded",
)

from core.pipeline import PipelineResult
from core.log_refiner import render_lines
from core.config import cfg
from core.observability import load_logs

# Stage 코드 → 한글 라벨
_STAGE_LABELS = {
    "stage1":      "Stage 1 — 로그 정제",
    "master_rule": "마스터 룰 — 전역 정규화",
    "stage2":      "Stage 2 — KB 검색",
    "stage3":      "Stage 3 — 로그 재정제",
    "stage4":      "Stage 4 — 패턴 매칭",
    "fallback":    "Stage 3/4 — Fallback",
    "stage5":      "Stage 5 — 리포트 생성",
    "stage6":      "Stage 6 — Reflection",
}

# ── 페이지 ────────────────────────────────────────────────────────────────────

st.header("📊 분석 결과")

result: PipelineResult | None = st.session_state.get("last_result")

if result is None:
    st.info("아직 분석 결과가 없습니다. **🔍 분석** 페이지에서 먼저 로그를 분석해 주세요.")
    st.stop()

problem_text: str = st.session_state.get("problem_text", "")

# ── 판정 배너 ─────────────────────────────────────────────────────────────────

VERDICT_COLOR = {
    "문제":       ("🔴", "error"),
    "불확실":     ("🟡", "warning"),
    "알 수 없음": ("⚪", "info"),
}

icon, color = VERDICT_COLOR.get(result.verdict, ("⚪", "info"))

getattr(st, color)(f"{icon} **판정: {result.verdict}**")

if problem_text:
    st.caption(f"문제 설명: {problem_text}")

# ── 진단 요약 ─────────────────────────────────────────────────────────────────

if result.match_result:
    mc = result.match_result
    c1, c2, c3 = st.columns(3)
    c1.metric("진단 점수", f"{mc.score:.0%}")
    c2.metric("매칭 패턴", len(mc.matched))
    c3.metric("미매칭 패턴", len(mc.unmatched))

if result.matched_case:
    case = result.matched_case
    if getattr(case, "pinned", False):
        st.info(
            f"📌 **매칭 케이스 (사용자 지정)**: {case.name}",
            icon="📌",
        )
    else:
        st.info(
            f"📚 **매칭 케이스**: {case.name}  |  관련성: {case.relevance_score:.0%}",
            icon="📚",
        )

st.divider()

# ── 탭 구성 ───────────────────────────────────────────────────────────────────

tab_report, tab_patterns, tab_log, tab_kb, tab_obs = st.tabs(
    ["📝 리포트", "🎯 문제 패턴 상세", "📋 정제 로그", "💡 KB 제안", "🔬 Stage 상세 로그"]
)

# ── 탭 1: Markdown 리포트 ─────────────────────────────────────────────────────

with tab_report:
    st.markdown(result.report_md)

    # Stage 6 Reflection 노트 (있을 때만 표시)
    notes = getattr(result, "reflection_notes", "")
    if notes and "변경 없음" not in notes and "원본 사용" not in notes:
        with st.expander("🔍 Stage 6 검증 노트", expanded=False):
            st.caption("Stage 6 Reflection 에서 수정된 항목입니다.")
            st.markdown(notes)

# ── 탭 2: 패턴 상세 ──────────────────────────────────────────────────────────

with tab_patterns:
    if result.match_result:
        mc = result.match_result

        if mc.matched:
            st.subheader(f"✅ 매칭된 문제 패턴 ({len(mc.matched)}개)")
            for pr in mc.matched:
                with st.expander(
                    f"**{pr.name}** — {pr.type}  |  weight={pr.weight}",
                    expanded=True,
                ):
                    if pr.evidence:
                        evidence_txt = "\n".join(ll.render() for ll in pr.evidence)
                        st.code(evidence_txt, language="text")
                    else:
                        st.caption("evidence 없음")
                    if pr.analysis_guidelines.strip():
                        with st.expander("📌 분석지침", expanded=False):
                            st.text(pr.analysis_guidelines)

        if mc.unmatched:
            st.subheader(f"❌ 미매칭 문제 패턴 ({len(mc.unmatched)}개)")
            for pr in mc.unmatched:
                st.markdown(
                    f"- `{pr.name}` ({pr.type}, weight={pr.weight})"
                )
    else:
        st.caption("패턴 매칭 결과 없음")

# ── 탭 3: 정제 로그 ──────────────────────────────────────────────────────────

with tab_log:
    st.subheader(f"L_common ({len(result.l_common)}줄)")
    if result.l_common:
        log_txt = render_lines(result.l_common)
        st.code(log_txt, language="text")
    else:
        st.caption("정제된 커널 로그가 없습니다.")

# ── 탭 4: KB 추가 제안 ────────────────────────────────────────────────────────

with tab_kb:
    if result.kb_suggestion is None:
        st.caption("이 경로에서는 KB 추가 제안이 생성되지 않습니다.")
    else:
        suggestion = result.kb_suggestion
        st.subheader("💡 KB 추가 후보")
        st.info(
            "이 문제를 KB 에 등록하면 향후 같은 증상을 자동으로 진단할 수 있습니다.",
            icon="💡",
        )

        pat = suggestion.pattern
        st.markdown(f"**패턴 이름**: `{pat.name}`")
        st.markdown(f"**타입**: {pat.type}")
        if hasattr(pat, "description") and pat.description:
            st.markdown(f"**설명**: {pat.description}")

        if suggestion.relations:
            st.subheader("기존 패턴과의 관계")
            for rel in suggestion.relations:
                st.markdown(f"- `{rel.target_name}` — **{rel.type}**: {rel.reason}")

        st.subheader("패턴 원본 (JSON)")
        import json
        st.json(json.loads(pat.model_dump_json()))

# ── 탭 5: Stage 상세 로그 (Observability) ────────────────────────────────────

with tab_obs:
    if not cfg.observability_enabled:
        st.info(
            "Observability 가 비활성화되어 있습니다.  \n"
            "설정 페이지에서 **Observability (Stage별 상세 로그) 활성화** 체크박스를 켜세요.",
            icon="ℹ️",
        )
    elif result.history_id is None:
        st.caption("이력 저장이 비활성화되어 Stage 로그를 불러올 수 없습니다.")
    else:
        logs = load_logs(result.history_id)
        if not logs:
            st.caption("이 분석에 대한 Stage 로그가 없습니다.")
        else:
            st.caption(f"history_id = {result.history_id}  ·  총 {len(logs)}개 Stage 로그")
            for entry in logs:
                label = _STAGE_LABELS.get(entry["stage"], entry["stage"])
                with st.expander(f"**{label}**  (`{entry['stage']}`)", expanded=False):
                    st.caption(f"기록 시각: {entry['created_at']}")
                    st.json(entry["payload_parsed"])
