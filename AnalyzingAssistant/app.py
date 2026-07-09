"""
app.py — Streamlit 진입점

실행:
    streamlit run app.py
"""

import streamlit as st
from pathlib import Path

from core.db import init_db, DB_PATH
from core.pattern_seeder import seed

# ── 앱 최초 기동 시 DB 초기화 ────────────────────────────────────────────────

@st.cache_resource
def _bootstrap() -> bool:
    """DB 스키마 생성 + 기본 패턴 시드 (최초 1회)."""
    init_db(DB_PATH)
    yaml_path = Path(__file__).parent / "config" / "patterns" / "default_patterns.yaml"
    if yaml_path.exists():
        seed(yaml_path, DB_PATH)
    return True


_bootstrap()

# ── 레이아웃 ──────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title = "Kernel Log Analyzer",
    page_icon  = "🔍",
    layout     = "wide",
    initial_sidebar_state = "expanded",
)

st.title("🔍 Kernel Log Analyzer")
st.markdown(
    "사이드바에서 페이지를 선택하세요.  \n"
    "**로그 분석** 페이지에서 문제 설명과 로그를 입력하면 자동으로 진단합니다."
)

st.info(
    "👈 왼쪽 사이드바의 **Pages** 섹션에서 원하는 페이지를 선택하세요.",
    icon="ℹ️",
)
