"""
GovCon Recompete Radar — Streamlit entrypoint & navigation controller.

Uses st.navigation so every page gets a clean title/icon (no raw filenames in the
nav). Page bodies live in views/. Run:  streamlit run streamlit_app/app.py
"""

import sys
from pathlib import Path

import streamlit as st

# This pilot repo is a full checkout — put src/ on the path so the app's components import
# the ONE scorer library (scoring.pursuit_score) instead of an inlined twin. Runs before
# st.navigation loads any view (which is what imports components).
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

st.set_page_config(page_title="Recompete Radar", page_icon="📡", layout="wide")

nav = st.navigation(
    [
        st.Page("views/home.py", title="Home", icon="📡", default=True),
        st.Page("views/company.py", title="Your Company", icon="🎯"),
        st.Page("views/explorer.py", title="Pipeline Explorer", icon="🔍"),
        st.Page("views/incumbents.py", title="Incumbent Landscape", icon="🏢"),
        st.Page("views/detail.py", title="Contract Detail", icon="📄"),
        st.Page("views/methodology.py", title="Methodology", icon="📐"),
    ]
)
nav.run()
