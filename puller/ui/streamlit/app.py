"""
Streamlit UI (임시 테스트용)

Core(WebDownloader)와 완전히 분리되어 있습니다.
UI는 WebDownloader의 Result 객체만 알면 됩니다.
"""

import sys
import asyncio
import pandas as pd
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import streamlit as st
from core import WebDownloader, load_config, build_url, get_input_params

# =============================================================================
# 설정 로드
# =============================================================================

config       = load_config()
browser_config = config.get("browser", {})
sites        = config.get("sites", [])
site_names   = [s["name"] for s in sites]

st.title("🗂️ Puller")
st.sidebar.header("설정")

# 사이트 선택
selected_name = st.sidebar.selectbox("사이트 선택", site_names)
site          = next(s for s in sites if s["name"] == selected_name)

st.subheader("📋 현재 설정")
st.json(site)

# =============================================================================
# 파라미터 입력 (진입점에서 한 번만 처리)
# =============================================================================

param_values = {}
input_params = get_input_params(site.get("parameters", []))
if input_params:
    st.subheader("🔧 파라미터 입력")
    for ui_name, param_key in input_params:
        param_values[ui_name] = st.text_input(f"{ui_name}", key=f"param_{ui_name}")

final_url        = build_url(site["url"], site.get("parameters", []), param_values)
site_with_params = {**site, "url": final_url}

if input_params:
    st.caption(f"최종 URL: `{final_url}`")

# 파라미터 입력 여부 확인 - 미입력 시 버튼 비활성화
params_ok = all(param_values.get(ui_name) for ui_name, _ in input_params)

# =============================================================================
# interaction 단계 선택
# =============================================================================

interaction_steps = site.get("interactions", {}).get("steps", [])
step_names        = [f"{i+1}. {s.get('name', f'step-{i+1}')}" for i, s in enumerate(interaction_steps)]
until_step_name   = None

if interaction_steps:
    st.sidebar.divider()
    st.sidebar.header("interaction 단계 선택")
    st.sidebar.caption("테스트/탐색 시 어느 조건까지 진행할지 선택합니다.")
    selected_step = st.sidebar.selectbox("중단할 단계", options=["전체 실행"] + step_names)
    if selected_step != "전체 실행":
        until_step_name = selected_step.split(". ", 1)[1]

    st.sidebar.markdown("**interaction steps**")
    for i, name_str in enumerate(step_names):
        selected_idx = step_names.index(selected_step) if selected_step != "전체 실행" else len(step_names)
        if i <= selected_idx:
            st.sidebar.markdown(f"✅ {name_str}")
        else:
            st.sidebar.markdown(f"⏭ ~~{name_str}~~")

    msg = "전체 실행" if until_step_name is None else f'"{until_step_name}" 조건 충족 시 중단'
    st.info(f"interaction 모드: {msg}")

st.divider()


def run_async(coro):
    """Streamlit에서 async 함수를 실행합니다."""
    return asyncio.run(coro)


def make_downloader(step_name=None):
    return WebDownloader(site_with_params, browser_config)


# =============================================================================
# ① 셀렉터 스캔
# =============================================================================

st.subheader("① 셀렉터 스캔")
st.caption("페이지 내 input, 버튼, 다운로드 링크, 클릭 가능 요소를 자동으로 찾아줍니다.")

scan_url = st.text_input("스캔할 URL", value=site_with_params.get("url", ""))

if st.button("🔎 셀렉터 스캔", width="stretch", disabled=not params_ok):
    with st.spinner("페이지 스캔 중..."):
        result = run_async(make_downloader().scan(until_step_name=until_step_name))

    st.write(f"**페이지 타이틀:** {result.title}")
    st.write(f"**현재 URL:** {result.current_url}")

    if not result.success:
        st.error(f"⚠️ {result.error}")
    else:
        if result.inputs:
            st.markdown("**📝 Input 요소**")
            st.table([{
                "셀렉터": i["selector"], "type": i["type"], "name": i["name"],
                "label": i.get("label", ""), "placeholder": i["placeholder"],
                "checked": "✅" if i.get("checked") else ("☐" if i.get("checked") is False else ""),
                "frame": i.get("frame_name", "main")
            } for i in result.inputs])

        if result.buttons:
            st.markdown("**🔘 버튼 요소**")
            st.table([{"셀렉터": b["selector"], "type": b["type"], "텍스트": b["text"], "frame": b.get("frame_name", "main")} for b in result.buttons])
        else:
            st.info("버튼이 없습니다.")

        if result.links:
            st.markdown("**🔗 다운로드 링크**")
            st.table([{"셀렉터": l["selector"], "텍스트": l["text"], "href": l["href"], "frame": l.get("frame_name", "main")} for l in result.links])
        else:
            st.info("다운로드 링크가 없습니다.")

        if result.tables:
            st.markdown("**📊 data-table 테이블**")
            st.table([{
                "셀렉터": t["selector"], "data-table": t["data_table"],
                "헤더": ", ".join(t["headers"]), "행 수": t["row_count"],
                "frame": t.get("frame_name", "main")
            } for t in result.tables])
        else:
            st.info("data-table 속성이 있는 테이블이 없습니다.")

        if result.clickables:
            st.markdown("**👆 클릭 가능 요소**")
            st.table([{"셀렉터": c["selector"], "tag": c["tag"], "텍스트": c["text"], "class": c["class"], "frame": c.get("frame_name", "main")} for c in result.clickables])
        else:
            st.info("클릭 가능 요소가 없습니다.")

st.divider()

# =============================================================================
# ② 페이지 탐색 테스트
# =============================================================================

st.subheader("② 페이지 탐색 테스트")
st.caption("interaction을 실행하고 최종 페이지 상태를 확인합니다.")

if st.button("🧭 페이지 탐색", width="stretch", disabled=not params_ok):
    with st.spinner("페이지 탐색 중..."):
        result = run_async(make_downloader().inspect(until_step_name=until_step_name))

    st.write(f"**페이지 타이틀:** {result.title}")
    st.write(f"**현재 URL:** {result.current_url}")

    if not result.success:
        st.error(f"⚠️ {result.error}")
    else:
        st.success("✅ 탐색 완료")

st.divider()

# =============================================================================
# ③ 텍스트 읽기
# =============================================================================

st.subheader("③ 텍스트 읽기")
st.caption("interaction 실행 후 final step의 read_text action 결과를 표시합니다.")

if st.button("📝 텍스트 읽기", width="stretch", disabled=not params_ok):
    with st.spinner("텍스트 읽는 중..."):
        result = run_async(make_downloader().read_text())

    st.write(f"**페이지 타이틀:** {result.title}")
    st.write(f"**현재 URL:** {result.current_url}")

    if not result.success:
        st.error(f"⚠️ {result.error}")
    else:
        for selector, text in result.texts.items():
            st.markdown(f"**셀렉터:** `{selector}`")
            st.text(text)

st.divider()

# =============================================================================
# ④ 테이블 읽기
# =============================================================================

st.subheader("④ 테이블 읽기")
st.caption("interaction 실행 후 final step의 read_table action 결과를 표시합니다.")

if st.button("📊 테이블 읽기", width="stretch", disabled=not params_ok):
    with st.spinner("테이블 읽는 중..."):
        result = run_async(make_downloader().read_table())

    st.write(f"**페이지 타이틀:** {result.title}")
    st.write(f"**현재 URL:** {result.current_url}")

    if not result.success:
        st.error(f"⚠️ {result.error}")
    else:
        for table in result.tables:
            st.markdown(f"**테이블:** `{table.selector}`")
            if table.rows:
                df = pd.DataFrame(
                    table.rows,
                    columns=table.headers if table.headers and len(table.headers) == len(table.rows[0]) else None
                )
                st.dataframe(df, width="stretch")
            elif table.headers:
                st.dataframe(pd.DataFrame(columns=table.headers), width="stretch")
                st.info("테이블에 데이터 행이 없습니다.")

st.divider()

# =============================================================================
# ⑤ 다운로드 실행
# =============================================================================

st.subheader("⑤ 다운로드 실행")
st.caption("전체 다운로드를 실행합니다.")

if st.button("⬇️ 다운로드 실행", width="stretch", disabled=not params_ok):
    progress_bar = st.progress(0, text="다운로드 준비 중...")
    with st.spinner("실행 중..."):
        result = run_async(make_downloader().download(until_step_name=until_step_name))
    progress_bar.progress(100, text="완료")

    if result.success:
        st.success(f"✅ 완료!")
        download_dir = Path(site_with_params.get("download_dir", "./downloads"))
        if download_dir.exists():
            files = sorted(download_dir.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True)
            if files:
                st.markdown("**📁 다운로드된 파일 목록**")
                df = pd.DataFrame([{
                    "파일명": f.name,
                    "크기": f"{f.stat().st_size / 1024:.1f} KB",
                    "경로": str(f)
                } for f in files if f.is_file()])
                st.dataframe(df, width="stretch")
    else:
        st.error(f"❌ 다운로드 실패: {result.error}")

# =============================================================================
# ⑥ 개별 다운로드 실행
# =============================================================================

steps = site.get("interactions", {}).get("steps", [])
has_individual_download = any(
    action.get("individual_download")
    for step in steps
    for action in step.get("actions", [])
    if action.get("type") == "download"
)

if has_individual_download:
    st.divider()
    st.subheader("⑥ 개별 다운로드 실행")
    st.caption("파일을 하나씩 개별적으로 다운로드합니다. (체크 → 다운로드 → 해제 순서)")

    if st.button("📥 개별 다운로드 실행", width="stretch", disabled=not params_ok):
        with st.spinner("개별 다운로드 중..."):
            result = run_async(make_downloader().download())

        if result.success:
            st.success(f"✅ 완료! 총 {result.total}개 처리")
            if result.files:
                st.markdown("**📁 개별 다운로드 결과**")
                df = pd.DataFrame([{
                    "번호": i + 1,
                    "파일명": f.filename,
                    "상태": "✅ 성공" if f.success else "❌ 실패",
                    "경로": f.path or "-"
                } for i, f in enumerate(result.files)])
                st.dataframe(df, width="stretch")
        else:
            st.error(f"❌ 개별 다운로드 실패: {result.error}")

    st.divider()
    st.subheader("⑦ 통합 테스트")
    st.caption("개별 다운로드 + read_text + read_table 결과를 한 번에 확인합니다.")

    if st.button("🧪 통합 테스트 실행", width="stretch", disabled=not params_ok):
        with st.spinner("통합 테스트 중..."):
            result = run_async(make_downloader().final_result())

        st.write(f"**페이지 타이틀:** {result.title}")
        st.write(f"**현재 URL:** {result.current_url}")

        if not result.success:
            st.error(f"❌ 실패: {result.error}")
        else:
            st.success("✅ 완료!")

            # 파일 결과
            if result.files:
                st.markdown("**📁 개별 다운로드 결과**")
                df = pd.DataFrame([{
                    "번호": i + 1,
                    "파일명": f.filename,
                    "상태": "✅ 성공" if f.success else "❌ 실패",
                    "경로": f.path or "-"
                } for i, f in enumerate(result.files)])
                st.dataframe(df, width="stretch")

            # 텍스트 결과
            if result.texts:
                st.markdown("**📝 텍스트 읽기 결과**")
                for selector, text in result.texts.items():
                    st.markdown(f"셀렉터: `{selector}`")
                    st.text(text)

            # 테이블 결과
            if result.tables:
                st.markdown("**📊 테이블 읽기 결과**")
                for table in result.tables:
                    st.markdown(f"테이블: `{table.selector}`")
                    if table.rows:
                        df = pd.DataFrame(
                            table.rows,
                            columns=table.headers if table.headers and len(table.headers) == len(table.rows[0]) else None
                        )
                        st.dataframe(df, width="stretch")