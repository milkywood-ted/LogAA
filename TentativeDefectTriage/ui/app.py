#!/usr/bin/env python3
"""테스트 UI — 파이프라인을 손으로 돌려보고 내부를 들여다보는 하니스.

설계 문서: `Document/신규 문제 분석 파이프라인/신규 문제 분석 파이프라인 설계.md` §9 Phase 5

    streamlit run TentativeDefectTriage/ui/app.py

**이것은 제품 UI 가 아니다.** LogAA `frontend/` 에 지금 통합하면 검증되지 않은
파이프라인의 오류가 기존 시스템으로 전파될 수 있어, 충분히 시험될 때까지 격리한
테스트 하니스다. 시험이 끝나면 frontend 와 연결한다.

그래서 우선순위가 제품 UI 와 반대다 — **보기 좋은 리포트보다 진단 정보가 먼저**다.
무엇이 발췌됐는지, 프롬프트가 얼마나 큰지, 질문이 왜 생략됐는지, 품질 신호가
떴는지를 보지 못하면 "왜 이런 답이 나왔는가" 를 되짚을 수 없다.

핵심 기능:
- **dry-run** — LLM 없이 프롬프트만 만들어 크기·내용 확인 (비용 0)
- **프롬프트 내려받기** — 다른 모델에 그대로 넣어 비교하기 위함
- 전문가별 진단 지표를 리포트와 함께 표시

의존성: streamlit(LogAA 공유 `.venv` 에 이미 있다) + 파이프라인 모듈.
"""

from __future__ import annotations

import io
import sys
import time
import traceback
from pathlib import Path

import streamlit as st

_ROOT = Path(__file__).resolve().parents[1]
for sub in ("", "analyze", "material", "refine"):
    p = str(_ROOT / sub) if sub else str(_ROOT)
    if p not in sys.path:
        sys.path.insert(0, p)

from analyze import (  # noqa: E402
    AnalysisInput, analyze, budget_summary, build_single_prompt, prepare,
)
from prompt import build_hypothesis_prompt, build_observation_prompt  # noqa: E402
from refine import RefineConfig  # noqa: E402
from report import render_markdown  # noqa: E402
from triage import DEFAULT_CONFIG, load_config, perspective_overlap, render, run_triage  # noqa: E402

st.set_page_config(page_title="TentativeDefectTriage — 테스트 하니스", layout="wide")


# ── 사이드바: 입력 ────────────────────────────────────────────────────────────

st.sidebar.title("입력")

cfg_path = Path(st.sidebar.text_input("설정 파일", str(DEFAULT_CONFIG)))
try:
    cfg = load_config(cfg_path)
except SystemExit as e:
    st.sidebar.error(str(e))
    st.stop()

st.sidebar.caption(f"자료 루트: `{cfg.material_root}`")
if not cfg.material_root.is_dir():
    st.sidebar.warning("자료 루트가 존재하지 않는다 — 경로를 확인할 것")

profiles = st.sidebar.multiselect(
    "전문가 (분석 프로파일)", options=sorted(cfg.profiles),
    default=sorted(cfg.profiles)[:1],
    help="**자동 선정하지 않는다** — 어떤 전문가가 관련 있는지 판단하는 것 자체가 "
         "분석의 일부이므로 사용자가 지정한다(설계 §2).",
)

# 칩 목록은 자료에서 실제로 있는 것만 뽑는다 — 오타로 헛도는 것을 막는다.
chips: set[str] = set()
for rel in cfg.profiles.values():
    d = cfg.material_root / rel
    if d.is_dir():
        chips |= {p.name for p in d.iterdir()
                  if p.is_dir() and (p / "11_log_index.tsv").is_file()}
chip = st.sidebar.selectbox("칩", sorted(chips) or ["(자료 없음)"])

uploaded = st.sidebar.file_uploader("로그 파일", accept_multiple_files=True)
log_path = st.sidebar.text_input(
    "또는 로그 경로", str(_ROOT / "fixtures" / "known_answer_ioctl_deadlock.log"))

problem = st.sidebar.text_area(
    "문제 상황",
    "채널 전환 직후 화면이 정지하고 리모컨 입력에 반응하지 않는다는 신고. 재부팅하면 복구됨.",
    height=90)

st.sidebar.divider()
kw_raw = st.sidebar.text_input(
    "정제 키워드 (프로파일별)", "DTV_FRC=S_F;DTV_DP=DRM-DP",
    help="`이름=키워드,키워드;이름=…` 형식. 실사용에서는 프로파일의 "
         "prefilter_keywords 를 쓴다.")
mode = st.sidebar.radio(
    "호출 방식", ["two_stage", "single"], index=0,
    help="2단계(관측→가설)가 1단계보다 나은지는 **미검증 가정**이다(설계 §4). "
         "같은 입력으로 둘 다 돌려 비교할 것.")
window = st.sidebar.number_input("발췌 window", 0, 200, 10)
budget = st.sidebar.number_input("로그 예산(토큰)", 1_000, 200_000, 28_000, step=1_000)

dry_run = st.sidebar.checkbox(
    "dry-run (LLM 호출 없이 프롬프트만)", value=True,
    help="비용 없이 프롬프트 크기·내용을 확인한다. 다른 모델에 넣어볼 프롬프트도 여기서 받는다.")

run = st.sidebar.button("실행", type="primary", use_container_width=True)


# ── 본문 ──────────────────────────────────────────────────────────────────────

st.title("TentativeDefectTriage — 테스트 하니스")
st.caption("제품 UI 가 아니다. 파이프라인 내부를 들여다보기 위한 시험 도구이며, "
           "충분히 검증되면 LogAA frontend 와 연결한다.")

if not run:
    st.info(
        "왼쪽에서 전문가·칩·로그를 지정하고 **실행**을 누른다.\n\n"
        "- **dry-run 이 기본값**이다 — LLM 을 부르지 않고 프롬프트만 만든다.\n"
        "- 재현 가능한 시나리오가 `fixtures/` 에 있다(정답을 아는 ioctl 락 누수 로그).\n"
        "- 산출물은 **확정 진단이 아니라 참고자료**다. 전문가 간 우열은 매기지 않는다."
    )
    st.stop()

if not profiles:
    st.error("전문가를 하나 이상 지정할 것")
    st.stop()

# ── 로그 로드 ─────────────────────────────────────────────────────────────────
raw_logs: dict[str, str] = {}
if uploaded:
    for f in uploaded:
        raw_logs[f.name] = f.getvalue().decode("utf-8", errors="replace")
elif log_path:
    p = Path(log_path)
    if not p.is_file():
        st.error(f"로그 파일을 찾을 수 없다: {p}")
        st.stop()
    raw_logs[p.name] = p.read_bytes().decode("utf-8", errors="replace")

if not raw_logs:
    st.error("로그를 업로드하거나 경로를 지정할 것")
    st.stop()

keywords: dict[str, list[str]] = {}
for chunk in kw_raw.split(";"):
    if "=" in chunk:
        name, kws = chunk.split("=", 1)
        keywords[name.strip()] = [k.strip() for k in kws.split(",") if k.strip()]


def _prepare_one(profile: str):
    """LLM 호출 없이 프롬프트 재료까지 만든다."""
    module_root = cfg.module_root(profile)
    if module_root is None:
        raise ValueError(f"설정에 '{profile}' 의 module_root 가 없다")
    inp = AnalysisInput(profile_name=profile, chip=chip, module_root=module_root,
                        problem_text=problem, raw_logs=raw_logs,
                        keywords=keywords.get(profile, []))
    rcfg = RefineConfig(keywords=inp.keywords, budget_tokens=int(budget))
    ctx, meta = prepare(inp, rcfg, int(window))
    return ctx, meta


# ── dry-run ───────────────────────────────────────────────────────────────────
if dry_run:
    st.subheader("dry-run — 프롬프트만 생성")
    st.caption("LLM 을 호출하지 않는다. 여기서 받은 프롬프트를 다른 모델에 그대로 넣어 비교할 수 있다.")

    for profile in profiles:
        with st.container(border=True):
            st.markdown(f"### {profile}")
            try:
                ctx, meta = _prepare_one(profile)
            except Exception as e:
                st.error(f"실패: {type(e).__name__}: {e}")
                continue

            if mode == "single":
                prompts = {"single": build_single_prompt(ctx)}
            else:
                prompts = {
                    "1차 관측": build_observation_prompt(ctx),
                    "2차 가설": build_hypothesis_prompt(ctx, "(1차 결과 자리 — dry-run)"),
                }

            c1, c2, c3, c4 = st.columns(4)
            # **호출별로 본다.** 2단계의 두 프롬프트는 독립된 LLM 호출이라
            # 합산하면 실사용량의 2배로 보인다(`analyze.budget_summary`).
            b = budget_summary(prompts)
            wname, worst = b["worst"]
            c1.metric("최대 호출", f"{worst:,} 토큰",
                      f"잔여 {b['headroom']:,} / {b['budget']:,}",
                      delta_color="inverse" if b["headroom"] < 0 else "normal")
            c2.metric("발췌 §", f"{meta['sections_used']}/{meta['sections_total']}",
                      f"{meta['excerpt_chars']:,}자")
            c3.metric("정제 로그", f"{meta['refine']['lines_final']:,}줄",
                      f"{meta['refine']['est_tokens']:,} 토큰")
            res_counts = meta["resolutions"]
            c4.metric("로그↔코드", f"단일 {res_counts.get('단일', 0)}",
                      f"복수 {res_counts.get('복수후보', 0)} / "
                      f"과다 {res_counts.get('후보과다', 0)} / "
                      f"미매칭 {res_counts.get('미매칭', 0)}", delta_color="off")
            if meta.get("narrowed"):
                st.info(
                    f"소스 대조로 좁힌 위치 **{meta['narrowed']}건** — 인덱스가 직접 준 "
                    "위치가 아니라 런타임 값을 소스에서 확인해 유도한 것이다."
                )

            if b["ratio"] > 0.9:
                st.warning(
                    f"입력 예산의 {b['ratio']:.0%} 를 썼다(`{wname}` {worst:,} 토큰 / "
                    f"{b['budget']:,}) — 로그 예산이나 발췌 window 를 줄일 것."
                )
            if mode != "single":
                st.caption(
                    "2차 가설은 실행 시 1차 결과만큼 더 커진다 — dry-run 은 자리표시자라 "
                    "위 수치는 하한이다. 두 호출은 독립이므로 합산하지 않는다."
                )

            if meta["questions_skipped"]:
                st.warning(
                    f"근거 자료가 없어 생략된 질문: **{', '.join(meta['questions_skipped'])}** "
                    f"— 이 전문가는 그 관점을 답하지 못한다(추측으로 메우지 않도록 프롬프트에 명시됨)."
                )
            if meta.get("basis_supplemented"):
                st.info("질문 근거 보충: " + ", ".join(
                    f"`{n}` {c:,}자" for n, c in meta["basis_supplemented"]))

            for label, text in prompts.items():
                with st.expander(f"{label} — {len(text):,}자"):
                    st.download_button(f"{label} 내려받기", text,
                                       file_name=f"prompt_{profile}_{label}.txt",
                                       key=f"dl_{profile}_{label}")
                    st.code(text[:20_000] +
                            ("\n\n… (표시 생략, 내려받기로 전체 확인)" if len(text) > 20_000 else ""),
                            language="text")
    st.stop()


# ── 실제 실행 ─────────────────────────────────────────────────────────────────
st.subheader("실행 중")
try:
    from analyze import default_llm_call
    llm = default_llm_call()
except Exception as e:
    st.error(f"LLM 클라이언트를 준비하지 못했다: {type(e).__name__}: {e}")
    st.caption("LogAA 공유 `.venv` 에서 실행했는지, AnalyzingAssistant_v2 설정이 있는지 확인할 것.")
    st.stop()

t0 = time.monotonic()
with st.spinner(f"전문가 {len(profiles)}명 병렬 실행 중…"):
    try:
        res = run_triage(profiles, chip, problem, raw_logs, cfg, llm,
                         keywords=keywords, mode=mode, window=int(window),
                         budget_tokens=int(budget))
    except Exception:
        st.error("실행 실패")
        st.code(traceback.format_exc(), language="text")
        st.stop()
elapsed = time.monotonic() - t0

ok = res.succeeded
st.success(f"전문가 {len(res.outcomes)}명 중 {len(ok)}명 성공 · {elapsed:.1f}초")

# ── 진단 (제품 UI 라면 숨길 것들 — 시험 도구라 전면에 둔다) ──────────────────
st.subheader("진단")
cols = st.columns(max(len(res.outcomes), 1))
for col, o in zip(cols, res.outcomes):
    with col:
        st.markdown(f"**{o.profile_name}**")
        if not o.ok:
            st.error(o.error)
            continue
        st.metric("소요", f"{o.seconds:.1f}s")
        st.metric("가설", len(o.report.hypotheses))
        if o.report.errors:
            st.error(f"구조 오류 {len(o.report.errors)}건")
            for e in o.report.errors:
                st.caption(f"· {e}")
        if o.report.quality_flags:
            st.warning(f"품질 신호 {len(o.report.quality_flags)}건")
            for q in o.report.quality_flags:
                st.caption(f"· {q}")
        else:
            st.caption("품질 신호 없음")
        m = o.meta or {}
        if m.get("prompt_chars"):
            st.caption("프롬프트: " + ", ".join(f"{k} {v:,}자" for k, v in m["prompt_chars"].items()))
        if m.get("questions_skipped"):
            st.caption(f"생략된 질문: {', '.join(m['questions_skipped'])}")

ov = perspective_overlap(res)
if ov:
    st.subheader("관점 차이")
    st.caption("전문가들이 같은 얘기만 반복하면 다중 전문가 구조가 무의미하다. "
               "**판정하지 않고 드러내기만 한다** — 겹침이 크다고 나쁜 것은 아니다.")
    for p in ov["pairs"]:
        a, b = p["pair"]
        ratio = p["file_overlap_ratio"]
        if p["shared_locations"] == 0 and not p["shared_files"]:
            st.success(f"`{a}` ↔ `{b}` — 겹치는 인용이 없다. 서로 다른 곳을 보고 있다")
        elif ratio >= 0.8:
            st.warning(f"`{a}` ↔ `{b}` — 인용이 대부분 겹친다(파일 기준 {ratio:.0%}). "
                       f"같은 원인을 독립 확인한 것인지, 실질적 중복인지 확인이 필요하다")
        else:
            st.info(f"`{a}` ↔ `{b}` — 일부 겹침(파일 기준 {ratio:.0%})")

# ── 리포트 ────────────────────────────────────────────────────────────────────
st.subheader("전문가별 참고자료")
st.caption("확정 진단이 아니다. 우열을 매기지 않았으므로 최종 판단은 읽는 사람이 한다.")

md = render(res)
st.download_button("전체 리포트 내려받기(Markdown)", md,
                   file_name=f"triage_{chip}_{int(time.time())}.md")

tabs = st.tabs([o.profile_name + ("" if o.ok else " (실패)") for o in res.outcomes])
for tab, o in zip(tabs, res.outcomes):
    with tab:
        if not o.ok:
            st.error(o.error)
            continue
        st.markdown(render_markdown(o.report))
        with st.expander("LLM 원문 응답"):
            st.code(o.report.raw_response or "(없음)", language="json")
