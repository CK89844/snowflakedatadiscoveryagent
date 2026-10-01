"""
Snowflake Data Discovery Agent
============================================================
Streamlit front end for mapping legacy report attributes onto the new
semantic model, using the Cortex Agent `cillian_test` that is connected to the
target semantic views.

Workflow
--------
1.  Upload a legacy report (CSV or Excel). The app reads its columns (and a few
    sample values) — these are the "legacy attributes" to be mapped.
2.  For each legacy attribute the app asks the Cortex Agent to find the best
    matching attribute in the new semantic model, returning a short JSON
    suggestion (target attribute, confidence, rationale).
3.  You review, tweak and confirm the mapping in an editable grid, then export
    it as CSV for downstream migration work.

Running locally with SSO
------------------------
1.  pip install -r requirements.txt
2.  Copy .streamlit/secrets.toml.example -> .streamlit/secrets.toml and fill in
    your DEV account details (authenticator = "externalbrowser").
3.  streamlit run streamlit_app.py
When deployed as Streamlit-in-Snowflake it uses the active session instead.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from io import BytesIO
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from cortex_agent import run_agent
from service_codes import classify_service_codes

# ------------------------------------------------------------------
# Page config
# ------------------------------------------------------------------
st.set_page_config(
    page_title="L&G · Client Report AI Mapping Tool",
    page_icon="🟢",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ------------------------------------------------------------------
# Theme / styling
# ------------------------------------------------------------------
def inject_css() -> None:
    """Inject a professional, L&G-branded look-and-feel."""
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

        :root {
            /* Legal & General — enterprise SaaS palette */
            --brand:        #00654F;   /* L&G deep green */
            --brand-dark:   #004C3B;   /* darker green */
            --brand-accent: #00735A;   /* mid green for text accents (AA on white) */
            --brand-tint:   #E4F1EC;   /* pale green wash */
            --brand-tint-2: #F0F7F4;   /* faintest green wash */

            --bg:           #F4F6F7;   /* app background */
            --bg-2:         #EBEFF1;   /* subtle panels */
            --card:         #FFFFFF;   /* cards */
            --card-2:       #F6F8F9;   /* raised card / hover */
            --line:         #E3E8EB;   /* hairline borders */
            --line-2:       #CFD8DD;

            --ink:          #10201A;   /* primary text */
            --muted:        #51625B;   /* secondary text (AA on white) */
            --faint:        #6E7F78;   /* tertiary (AA on white) */

            --good:         #0B7C48;   /* high confidence */
            --warn:         #B4670A;   /* medium */
            --bad:          #C0353B;   /* low */

            /* Radius scale */
            --r-sm: 8px; --r-md: 12px; --r-lg: 16px; --r-xl: 22px;
            /* Elevation scale */
            --e-1: 0 1px 2px rgba(16,32,26,.05), 0 1px 3px rgba(16,32,26,.04);
            --e-2: 0 4px 14px rgba(16,32,26,.07);
            --e-3: 0 12px 34px rgba(16,32,26,.10);
            /* Focus ring */
            --ring: 0 0 0 3px rgba(0,131,106,.28);
        }

        /* Accessible focus for keyboard users across interactive elements */
        button:focus-visible, a:focus-visible, input:focus-visible,
        [role="button"]:focus-visible, [data-baseweb="select"]:focus-within {
            outline: none !important; box-shadow: var(--ring) !important;
            border-radius: var(--r-md);
        }

        html, body, [class*="css"], .stMarkdown, .stButton, input, textarea, select {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif !important;
        }
        .stApp { background: var(--bg); color: var(--ink); }
        [data-testid="stHeader"] { background: transparent; }
        [data-testid="stSidebar"] { display: none; }
        p, span, label, div { color: var(--ink); }
        ::selection { background: var(--brand); color:#FFFFFF; }

        .block-container { padding-top: 1.1rem; padding-bottom: 3rem; max-width: 1180px; }

        /* ---- Top navigation ------------------------------------------- */
        .topnav {
            display:flex; align-items:center; gap:1rem;
            padding:.7rem 1.1rem; margin:-.5rem 0 1.2rem 0;
            background: rgba(255,255,255,.9); backdrop-filter: saturate(180%) blur(8px);
            border:1px solid var(--line); border-radius:var(--r-lg);
            box-shadow: var(--e-1);
        }
        .topnav .brandmark { display:flex; align-items:center; gap:.65rem; }
        .topnav .logo {
            display:inline-flex; align-items:center; justify-content:center;
            width:36px; height:36px; border-radius:10px; font-weight:800; font-size:.9rem;
            color:#FFFFFF; background:linear-gradient(135deg,var(--brand),var(--brand-accent));
            box-shadow:0 4px 14px rgba(0,101,79,.28);
        }
        .topnav .product { font-weight:800; font-size:1rem; letter-spacing:-.01em; color:var(--ink); line-height:1.1; }
        .topnav .product small { display:block; font-weight:600; font-size:.68rem;
            letter-spacing:.14em; text-transform:uppercase; color:var(--brand-accent); margin-top:.15rem; }
        .topnav .navspacer { flex:1; }
        .topnav .status { display:inline-flex; align-items:center; gap:.45rem;
            font-size:.76rem; color:var(--muted); font-weight:600; }
        .topnav .status .dot { width:8px; height:8px; border-radius:50%;
            background:var(--good); box-shadow:0 0 0 3px rgba(11,124,72,.16); }

        /* Streamlit nav buttons styled as a centered segmented pill group.
           Targets the widgets by their st-key-nav_* class (robust to DOM nesting). */
        .navrow { margin: 0 0 .2rem 0; }
        div[data-testid="stHorizontalBlock"]:has([class*="st-key-nav_"]) {
            gap:.4rem; align-items:center;
            background: var(--card-2); border:1px solid var(--line);
            border-radius:14px; padding:.35rem; box-shadow: var(--e-1);
            margin-bottom:1.4rem;
        }
        [class*="st-key-nav_"] .stButton > button,
        div[class*="st-key-nav_"] button {
            background: transparent; border:1px solid transparent; color: var(--muted);
            font-weight:650; font-size:.9rem; border-radius:10px; padding:.5rem .8rem;
            width:100%; min-height:2.5rem;
            transition: background .12s ease, color .12s ease, border-color .12s ease;
        }
        [class*="st-key-nav_"] button:hover {
            background: var(--card); color: var(--ink); border-color: var(--line-2);
        }
        [class*="st-key-nav_"] button[kind="primary"] {
            background: linear-gradient(135deg,var(--brand),var(--brand-dark));
            color: #FFFFFF !important; border:1px solid var(--brand-dark);
            box-shadow: 0 4px 12px rgba(0,101,79,.24);
        }
        [class*="st-key-nav_"] button[kind="primary"] * {
            color:#FFFFFF !important; fill:#FFFFFF !important;
        }
        [class*="st-key-nav_"] button[kind="primary"]:hover {
            filter: brightness(1.06); color:#FFFFFF !important;
        }

        /* ---- Home split entry cards ----------------------------------- */
        .entry {
            background: var(--card); border:1px solid var(--line); border-radius:18px;
            padding:1.6rem 1.6rem 1.3rem; height:100%;
            box-shadow: 0 4px 18px rgba(20,32,27,.06);
            transition: transform .15s ease, border-color .15s ease;
        }
        .entry:hover { transform: translateY(-2px); border-color: var(--line-2); }
        .entry .eyebrow { font-size:.72rem; font-weight:700; letter-spacing:.16em;
            text-transform:uppercase; color:var(--brand-accent); }
        .entry h2 { font-size:1.5rem; font-weight:800; margin:.35rem 0 .5rem; letter-spacing:-.02em; color:var(--ink); }
        .entry p.lead { color:var(--muted); font-size:.95rem; margin:0 0 1rem; }

        .dropzone {
            border:1.5px dashed var(--line-2); border-radius:14px; background:var(--bg-2);
            padding:2.4rem 1rem; text-align:center; margin:.2rem 0 1rem;
        }
        .dropzone .big { font-size:2.4rem; line-height:1; }
        .dropzone .cta { font-weight:700; color:var(--ink); margin-top:.5rem; font-size:1.02rem; }
        .dropzone .sub { color:var(--muted); font-size:.85rem; margin-top:.25rem; }

        .example-chips { display:flex; gap:.5rem; flex-wrap:wrap; margin-top:.7rem; }
        .example-chips .chip {
            background:var(--card-2); border:1px solid var(--line-2); color:var(--muted);
            padding:.28rem .7rem; border-radius:999px; font-size:.8rem; font-weight:600;
        }

        /* ---- Universal search box ------------------------------------- */
        .stTextInput > div > div input {
            background: var(--card-2) !important; color: var(--ink) !important;
            border:1px solid var(--line-2) !important; border-radius:12px !important;
            padding:.9rem 1rem !important; font-size:1rem !important;
        }
        .stTextInput > div > div input::placeholder { color: var(--faint) !important; }

        /* ---- File uploader as dropzone -------------------------------- */
        [data-testid="stFileUploaderDropzone"] {
            background: var(--bg-2); border:1.5px dashed var(--line-2);
            border-radius:14px; padding:2rem 1rem;
        }
        [data-testid="stFileUploaderDropzone"]:hover { border-color: var(--brand); }
        [data-testid="stFileUploaderDropzone"] * { color: var(--muted) !important; }
        [data-testid="stFileUploaderDropzone"] button {
            background: var(--card-2) !important; color: var(--ink) !important;
            border:1px solid var(--line-2) !important;
        }
        [data-testid="stSelectbox"] div[data-baseweb="select"] > div {
            background: var(--card-2) !important; border-color: var(--line-2) !important; color:var(--ink) !important;
        }
        /* Show the full selected value (long object/column names) instead of
           truncating with an ellipsis. */
        [data-testid="stSelectbox"] div[data-baseweb="select"] div[title],
        [data-testid="stSelectbox"] div[data-baseweb="select"] span {
            white-space: normal !important; overflow: visible !important;
            text-overflow: clip !important;
        }
        /* Dropdown option list: wrap long options rather than clip them. */
        div[data-baseweb="popover"] li,
        div[data-baseweb="popover"] div[role="option"] {
            white-space: normal !important; overflow: visible !important;
            text-overflow: clip !important; height: auto !important;
            line-height: 1.35 !important;
        }
        [data-testid="stExpander"] { border:1px solid var(--line) !important; border-radius:12px !important;
            background: var(--card) !important; }

        /* ---- Page heading --------------------------------------------- */
        .page-head { display:flex; align-items:baseline; gap:.8rem; margin:.2rem 0 1.1rem; }
        .page-head h1 { font-size:1.5rem; font-weight:800; letter-spacing:-.02em; margin:0; color:var(--ink); }
        .page-head .kicker { color:var(--brand-accent); font-weight:700; font-size:.72rem;
            text-transform:uppercase; letter-spacing:.16em; }

        /* ---- Metric strip --------------------------------------------- */
        .metric-row { display:flex; gap:1rem; flex-wrap:wrap; margin:.4rem 0 1.2rem 0; }
        .metric-card {
            flex:1; min-width:150px; background:var(--card); border:1px solid var(--line);
            border-radius:14px; padding:1rem 1.1rem;
        }
        .metric-card .label { color:var(--muted); font-size:.72rem; font-weight:700;
            text-transform:uppercase; letter-spacing:.06em; }
        .metric-card .value { color:var(--ink); font-size:1.9rem; font-weight:800; line-height:1.1; margin-top:.2rem; }
        .metric-card .value.good { color:var(--good); }
        .metric-card .value.warn { color:var(--warn); }
        .metric-card .value.bad  { color:var(--bad); }

        /* ---- Analysis steps ------------------------------------------- */
        .steps { background:var(--card); border:1px solid var(--line); border-radius:14px;
            padding:1rem 1.2rem; margin-bottom:1rem; }
        .steps .row { display:flex; align-items:center; gap:.7rem; padding:.32rem 0;
            font-size:.94rem; color:var(--muted); }
        .steps .row.done { color:var(--ink); }
        .steps .tick { width:22px; height:22px; border-radius:50%; display:inline-flex;
            align-items:center; justify-content:center; font-size:.75rem; font-weight:800;
            background:var(--line); color:var(--faint); }
        .steps .row.done .tick { background:var(--brand); color:#03110C; }
        .steps .row.active .tick { background:var(--brand-accent); color:#03110C; }

        /* ---- Attribute mapping grid ----------------------------------- */
        .grid { border:1px solid var(--line); border-radius:14px; overflow:hidden; background:var(--card); }
        .grid .ghead, .grid .grow {
            display:grid; grid-template-columns: 1.4fr 1.5fr 1.3fr .8fr; align-items:center;
        }
        .grid .ghead {
            background:var(--bg-2); padding:.7rem 1rem; font-size:.72rem; font-weight:700;
            text-transform:uppercase; letter-spacing:.06em; color:var(--muted);
            border-bottom:1px solid var(--line);
        }
        .grid .grow { padding:.7rem 1rem; border-bottom:1px solid var(--line); font-size:.9rem; }
        .grid .grow:last-child { border-bottom:0; }
        .grid .grow:hover { background:var(--card-2); }
        .grid .col-src { font-weight:700; color:var(--ink); }
        .grid .col-attr { color:var(--brand-accent); font-weight:600; }
        .grid .col-attr.none { color:var(--faint); font-style:italic; font-weight:500; }
        .grid .col-ds { color:var(--muted); }
        /* 6-column mapping grid (read-only, calm) */
        .grid.map5 .ghead, .grid.map5 .grow {
            grid-template-columns: 1.2fr 1.7fr .6fr 1fr .8fr .9fr;
            column-gap: 1rem; align-items:center;
        }
        .grid.map5 .grow { min-height:50px; }
        .grid.map5 .col-src { font-weight:600; }
        .grid.map5 .col-ds { display:flex; flex-direction:column; line-height:1.2;
            white-space:nowrap; overflow:hidden; }
        .grid .grow.ovr { background:rgba(0,168,112,.06); }
        .grid .grow.ovr:hover { background:rgba(0,168,112,.10); }
        .ovr-tag { display:inline-block; font-size:.56rem; font-weight:800; letter-spacing:.05em;
            text-transform:uppercase; color:var(--brand-accent); border:1px solid var(--brand-accent);
            border-radius:999px; padding:.02rem .34rem; margin-left:.4rem; vertical-align:middle; }
        .tgt-obj { color:var(--faint); font-size:.72rem; overflow:hidden; text-overflow:ellipsis; }
        .tgt-col { color:var(--ink); font-weight:600; overflow:hidden; text-overflow:ellipsis; }
        .dash { color:var(--faint); }
        .src-codes { color:var(--muted); font-size:.82rem; }
        .src-more { display:inline-block; font-size:.62rem; font-weight:800; color:var(--muted);
            background:var(--bg-2); border:1px solid var(--line); border-radius:999px;
            padding:.02rem .3rem; margin-left:.15rem; cursor:default; }
        .match-badge { display:inline-block; font-size:.6rem; font-weight:700; letter-spacing:.02em;
            padding:.08rem .4rem; border-radius:6px; margin-right:.25rem; }
        .match-badge.value { background:#E4F0FB; color:#1F5F8B; border:1px solid #BCD9F0; }
        .match-badge.pattern { background:#EEEAF7; color:#5A4A8A; border:1px solid #D3C9EC; }
        .status-pill { display:inline-block; font-size:.62rem; font-weight:800; letter-spacing:.04em;
            text-transform:uppercase; padding:.12rem .5rem; border-radius:999px; }
        .status-pill.mapped { background:#DFF3E8; color:#0B6E4F; border:1px solid #A9DCC4; }
        .status-pill.review { background:#FCEFD6; color:#8A5A00; border:1px solid #EBD199; }
        .status-pill.unmapped { background:#FBE6E1; color:#9A3412; border:1px solid #F1C2B4; }
        .conf { display:inline-flex; align-items:center; gap:.4rem; font-weight:700; font-size:.8rem; margin-left:.4rem; }
        .conf .dot { width:9px; height:9px; border-radius:50%; }
        .conf.good { color:var(--good); } .conf.good .dot { background:var(--good); }
        .conf.warn { color:var(--warn); } .conf.warn .dot { background:var(--warn); }
        .conf.bad  { color:var(--bad); }  .conf.bad  .dot { background:var(--bad); }

        /* ---- Match / dataset cards ------------------------------------ */
        .cards-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(300px,1fr)); gap:1rem; margin-top:.4rem; }
        .dscard { background:var(--card); border:1px solid var(--line); border-radius:16px;
            padding:1.2rem 1.3rem; position:relative; overflow:hidden;
            box-shadow:0 4px 16px rgba(20,32,27,.06); }
        .dscard .match { font-size:2rem; font-weight:800; letter-spacing:-.02em; line-height:1; }
        .dscard .match.good { color:var(--good); } .dscard .match.warn { color:var(--warn); } .dscard .match.bad { color:var(--bad); }
        .dscard .match small { font-size:.8rem; font-weight:700; color:var(--muted); margin-left:.35rem; }
        .dscard h3 { font-size:1.15rem; font-weight:800; margin:.3rem 0 .1rem; color:var(--ink); }
        .dscard .cert { display:inline-flex; align-items:center; gap:.35rem; font-size:.72rem; font-weight:700;
            color:var(--brand-accent); text-transform:uppercase; letter-spacing:.06em; margin-bottom:.7rem; }
        .dscard .meta { display:grid; grid-template-columns:1fr 1fr; gap:.5rem .9rem; margin-top:.6rem; }
        .dscard .meta .k { font-size:.68rem; text-transform:uppercase; letter-spacing:.05em; color:var(--faint); font-weight:700; }
        .dscard .meta .v { font-size:.92rem; font-weight:700; color:var(--ink); margin-top:.05rem; }
        .dscard .bar { height:6px; background:var(--line); border-radius:999px; margin-top:.35rem; overflow:hidden; }
        .dscard .bar > span { display:block; height:100%; background:linear-gradient(90deg,var(--brand),var(--brand-accent)); }

        /* ---- Authority card ------------------------------------------- */
        .authority {
            background: radial-gradient(120% 140% at 0% 0%, rgba(0,164,124,.16), transparent 55%), var(--card);
            border:1px solid var(--line-2); border-radius:18px; padding:1.5rem 1.7rem; margin-bottom:1.1rem;
            box-shadow:0 6px 22px rgba(20,32,27,.07);
        }
        .authority .eyebrow { font-size:.72rem; font-weight:700; letter-spacing:.16em; text-transform:uppercase; color:var(--brand-accent); }
        .authority h2 { font-size:2rem; font-weight:800; letter-spacing:-.02em; margin:.25rem 0 .5rem; color:var(--ink); }
        .authority .row { display:flex; align-items:center; gap:1.4rem; flex-wrap:wrap; margin-top:.4rem; }
        .authority .trust { font-size:2.2rem; font-weight:800; color:var(--good); line-height:1; }
        .authority .trust small { display:block; font-size:.7rem; font-weight:700; color:var(--muted); text-transform:uppercase; letter-spacing:.08em; }
        .authority .cert-badge { display:inline-flex; align-items:center; gap:.4rem;
            background:var(--brand-tint); border:1px solid var(--brand); color:var(--brand-dark);
            padding:.4rem .8rem; border-radius:999px; font-size:.8rem; font-weight:700; }
        .authority p.desc { color:var(--muted); margin:.7rem 0 0; font-size:.95rem; max-width:70ch; }

        /* ---- Field rows ----------------------------------------------- */
        .subhead { font-size:.78rem; font-weight:700; letter-spacing:.1em; text-transform:uppercase;
            color:var(--muted); margin:1.3rem 0 .6rem; }
        .field-row { display:flex; align-items:center; gap:.8rem; background:var(--card);
            border:1px solid var(--line); border-radius:11px; padding:.6rem .9rem; margin-bottom:.45rem; }
        .field-row .fname { font-weight:700; color:var(--ink); font-size:.95rem; }
        .field-row .fds { color:var(--brand-accent); font-size:.8rem; font-weight:600; }
        .field-row .fdesc { color:var(--muted); font-size:.85rem; margin-left:auto; text-align:right; max-width:48ch; }

        /* ---- Lineage graph -------------------------------------------- */
        .lineage { display:flex; align-items:stretch; gap:.6rem; flex-wrap:wrap; margin-top:.3rem; }
        .lineage .col { flex:1; min-width:180px; }
        .lineage .lbl { font-size:.68rem; font-weight:700; letter-spacing:.08em; text-transform:uppercase;
            color:var(--faint); margin-bottom:.4rem; text-align:center; }
        .lineage .node { background:var(--card); border:1px solid var(--line); border-radius:11px;
            padding:.6rem .7rem; margin-bottom:.4rem; text-align:center; font-weight:700; font-size:.86rem; color:var(--ink); }
        .lineage .node.strategic { border-color:var(--brand); background:var(--brand-tint); color:var(--brand-dark); }
        .lineage .arrow { display:flex; align-items:center; justify-content:center; color:var(--brand-accent);
            font-size:1.4rem; font-weight:800; }

        /* ---- Buttons -------------------------------------------------- */
        .stButton > button[kind="primary"],
        .stFormSubmitButton > button[kind="primary"],
        .stFormSubmitButton > button {
            background: linear-gradient(135deg,var(--brand),var(--brand-dark)); border:0; border-radius:11px;
            font-weight:700; color:#FFFFFF !important; box-shadow:0 6px 16px rgba(0,101,79,.22);
        }
        /* Force the label (rendered in a child element) to stay white on green */
        .stButton > button[kind="primary"] *,
        .stFormSubmitButton > button[kind="primary"] *,
        .stFormSubmitButton > button * {
            color:#FFFFFF !important; fill:#FFFFFF !important;
        }
        .stButton > button[kind="primary"]:hover,
        .stFormSubmitButton > button:hover { filter:brightness(1.06); }
        .stDownloadButton > button {
            background: var(--card-2); border:1px solid var(--line-2); border-radius:11px; font-weight:700; color:var(--ink);
        }
        .stDownloadButton > button:hover { border-color:var(--brand); color:var(--brand-accent); }

        /* Tabs, dataframe */
        .stTabs [aria-selected="true"] { color: var(--brand-accent) !important; }
        .stTabs [data-baseweb="tab-highlight"] { background-color: var(--brand) !important; }
        [data-testid="stDataFrame"] { border:1px solid var(--line); border-radius:12px; }

        /* Footer */
        .lg-footer {
            margin-top: 2.5rem; padding-top: 1rem; border-top:1px solid var(--line);
            color: var(--faint); font-size:.78rem; display:flex; justify-content:space-between;
            flex-wrap:wrap; gap:.5rem;
        }
        .lg-footer .brand { color: var(--brand-accent); font-weight:700; }
        .layer-badge { display:inline-block; font-size:.62rem; font-weight:800;
            letter-spacing:.04em; padding:.08rem .38rem; border-radius:999px;
            vertical-align:middle; margin-left:.25rem; }
        .layer-badge.gold { background:#F5E4B0; color:#7A5B00; border:1px solid #E7C86A; }
        .layer-badge.silver { background:#E6EAEE; color:#4A5A67; border:1px solid #C9D3DB; }
        .layer-badge.bronze { background:#EFD9C2; color:#7A4A1E; border:1px solid #D8B48C; }
        .basis-badge { display:inline-block; font-size:.6rem; font-weight:700;
            padding:.06rem .34rem; border-radius:999px; vertical-align:middle;
            margin-left:.25rem; background:#EAF3F0; color:#0B6E4F; border:1px solid #BFE0D5; }
        .src-badge { display:inline-block; font-size:.6rem; font-weight:800;
            letter-spacing:.03em; padding:.06rem .34rem; border-radius:999px;
            vertical-align:middle; margin-left:.25rem; }
        .src-badge.strategic { background:#DFF3E8; color:#0B6E4F; border:1px solid #A9DCC4; }
        .src-badge.legacy { background:#FBE6E1; color:#9A3412; border:1px solid #F1C2B4; }
        .src-badge.mixed { background:#FCEFD6; color:#8A5A00; border:1px solid #EBD199; }
        .src-badge.unknown { background:#ECEFF2; color:#5A6773; border:1px solid #D4DBE1; }

        /* ---- Search results (grouped by object) ----------------------- */
        .sr-summary { color:var(--muted); font-size:.92rem; margin:.2rem 0 1rem; }
        .sr-summary b { color:var(--ink); }
        .sr-group { background:var(--card); border:1px solid var(--line);
            border-radius:14px; padding:.2rem .2rem .4rem; margin-bottom:1rem; overflow:hidden; }
        .sr-obj { display:flex; align-items:center; gap:.55rem; flex-wrap:wrap;
            padding:.75rem .95rem; border-bottom:1px solid var(--line);
            background:var(--card-2); }
        .sr-obj .schema { color:var(--faint); font-weight:600; font-size:.9rem; }
        .sr-obj .table { color:var(--ink); font-weight:800; font-size:1rem; letter-spacing:.01em; }
        .sr-obj .count { margin-left:auto; color:var(--faint); font-size:.76rem; font-weight:700;
            text-transform:uppercase; letter-spacing:.06em; }
        .sr-col { display:grid; grid-template-columns: 260px 1fr; gap:1rem;
            align-items:start; padding:.6rem .95rem; border-bottom:1px solid var(--line); }
        .sr-col:last-child { border-bottom:0; }
        .sr-col .cname { font-weight:700; color:var(--ink); font-size:.92rem;
            font-family:"SFMono-Regular",Consolas,monospace; word-break:break-word; }
        .sr-col .ctype { display:block; color:var(--faint); font-size:.72rem; font-weight:600;
            margin-top:.15rem; text-transform:uppercase; letter-spacing:.04em; }
        .sr-col .cdesc { color:var(--muted); font-size:.88rem; line-height:1.4; }
        .sr-col .cdesc .none { color:var(--faint); font-style:italic; }
        .sr-empty { background:var(--card); border:1px solid var(--line); border-radius:14px;
            padding:1.4rem; color:var(--muted); text-align:center; }

        /* ---- Reusable states & overview components -------------------- */
        .state { display:flex; flex-direction:column; align-items:center; text-align:center;
            gap:.5rem; background:var(--card); border:1px solid var(--line);
            border-radius:var(--r-lg); padding:2.4rem 1.6rem; box-shadow:var(--e-1); }
        .state .glyph { width:52px; height:52px; border-radius:14px; display:flex;
            align-items:center; justify-content:center; font-size:1.5rem;
            background:var(--brand-tint); color:var(--brand-dark); }
        .state h3 { margin:.2rem 0 0; font-size:1.1rem; font-weight:800; color:var(--ink); }
        .state p { margin:0; color:var(--muted); font-size:.92rem; max-width:52ch; }
        .state.error .glyph { background:#FBE9EA; color:var(--bad); }
        .state.success .glyph { background:var(--brand-tint); color:var(--good); }

        .banner { display:flex; align-items:flex-start; gap:.7rem; border-radius:var(--r-md);
            padding:.8rem 1rem; font-size:.9rem; margin:.2rem 0 1rem; border:1px solid var(--line); }
        .banner.err { background:#FBE9EA; border-color:#F1C7C9; color:#8A2C30; }
        .banner.ok  { background:var(--brand-tint); border-color:#BFE0D5; color:var(--brand-dark); }
        .banner b { font-weight:800; }

        /* Overview hero + stats */
        .hero { position:relative; overflow:hidden; border-radius:var(--r-xl);
            border:1px solid var(--line-2); padding:2rem 2.1rem;
            background: radial-gradient(120% 160% at 100% 0%, rgba(0,131,106,.14), transparent 55%), var(--card);
            box-shadow:var(--e-2); margin-bottom:1.3rem; }
        .hero .eyebrow { font-size:.72rem; font-weight:700; letter-spacing:.16em;
            text-transform:uppercase; color:var(--brand-accent); }
        .hero h1 { font-size:2rem; font-weight:800; letter-spacing:-.02em;
            margin:.35rem 0 .5rem; color:var(--ink); max-width:22ch; }
        .hero p { color:var(--muted); font-size:1rem; margin:0; max-width:64ch; }
        .stat-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
            gap:.9rem; margin:1.1rem 0 0; }
        .stat { background:rgba(255,255,255,.7); border:1px solid var(--line);
            border-radius:var(--r-md); padding:.85rem 1rem; }
        .stat .v { font-size:1.55rem; font-weight:800; color:var(--ink); line-height:1.1; }
        .stat .k { font-size:.72rem; font-weight:700; text-transform:uppercase;
            letter-spacing:.05em; color:var(--muted); margin-top:.15rem; }

        /* Action tiles */
        .tiles { display:grid; grid-template-columns:1fr 1fr; gap:1rem; }
        @media (max-width: 820px){ .tiles { grid-template-columns:1fr; } }
        .tile { background:var(--card); border:1px solid var(--line); border-radius:var(--r-lg);
            padding:1.4rem 1.5rem; box-shadow:var(--e-1);
            transition: transform .15s ease, box-shadow .15s ease, border-color .15s ease; }
        .tile:hover { transform:translateY(-2px); box-shadow:var(--e-2); border-color:var(--line-2); }
        .tile .ic { width:44px; height:44px; border-radius:12px; display:flex; align-items:center;
            justify-content:center; font-size:1.3rem; background:var(--brand-tint); color:var(--brand-dark);
            margin-bottom:.8rem; }
        .tile h3 { font-size:1.15rem; font-weight:800; margin:0 0 .3rem; color:var(--ink); }
        .tile p { color:var(--muted); font-size:.92rem; margin:0 0 .3rem; }

        /* Domain shortcut chips (curated search entry) */
        .subhead-inline { font-size:.72rem; font-weight:700; letter-spacing:.1em;
            text-transform:uppercase; color:var(--muted); margin:.2rem 0 .6rem; }
        </style>
        """,
        unsafe_allow_html=True,
    )


# ------------------------------------------------------------------
# Snowflake session (SiS active session, or local SSO connection)
# ------------------------------------------------------------------
@st.cache_resource(show_spinner="Connecting to Snowflake…")
def get_session():
    """Return a Snowpark session (active in SiS, or local SSO via secrets).

    Cached with @st.cache_resource so the connection is built once per server
    run and reused across reruns. For local SSO we also enable Snowflake's
    on-disk token cache (client_store_temporary_credential) so repeat launches
    reuse the SSO token instead of popping the browser every time.
    """
    from snowflake.snowpark.context import get_active_session

    try:
        sess = get_active_session()
        st.session_state["_is_sis"] = True  # running inside Streamlit-in-Snowflake
        return sess
    except Exception:
        st.session_state["_is_sis"] = False
        pass  # No active session -> running locally.

    from snowflake.snowpark import Session

    try:
        has_conn = (
            "connections" in st.secrets
            and "snowflake" in st.secrets["connections"]
        )
    except Exception:  # noqa: BLE001 — no secrets file at all (e.g. SiS)
        has_conn = False
    if not has_conn:
        st.error(
            "No Snowflake connection found. When running locally, create "
            "`.streamlit/secrets.toml` with a [connections.snowflake] section "
            "(see .streamlit/secrets.toml.example)."
        )
        st.stop()

    cfg = dict(st.secrets["connections"]["snowflake"])

    # ---- SSO token caching -------------------------------------------------
    # For externalbrowser auth, cache the id-token so repeat launches reuse the
    # SSO login instead of popping the browser every time. This needs BOTH:
    #   * the `keyring` package installed locally (stores the token in the
    #     Windows Credential Manager), AND
    #   * the account parameter ALLOW_ID_TOKEN = TRUE (an ACCOUNTADMIN sets
    #     this once: `ALTER ACCOUNT SET ALLOW_ID_TOKEN = TRUE;`). Without it the
    #     server never issues a cacheable token and each new process re-auths.
    # `client_session_keep_alive` stops the live session expiring while idle,
    # so you don't get a surprise re-auth mid-work.
    if str(cfg.get("authenticator", "")).lower() == "externalbrowser":
        cfg.setdefault("client_store_temporary_credential", True)
        cfg.setdefault("client_request_mfa_token", True)
        cfg.setdefault("client_session_keep_alive", True)

    return Session.builder.configs(cfg).create()


def agent_config() -> dict:
    """Read agent object coordinates from secrets, with sensible defaults.

    In Streamlit-in-Snowflake there is no secrets.toml, so accessing st.secrets
    raises; we fall back to defaults (the app uses the in-database engine there
    and doesn't call the REST agent anyway).
    """
    try:
        cfg = dict(st.secrets.get("agent", {}))
    except Exception:  # noqa: BLE001 — no secrets file (SiS)
        cfg = {}
    return {
        "name": cfg.get("name", "DATA_DISCOVERY_AGENT"),
        "database": cfg.get("database", "DATA_ENGINEERING_HOME"),
        "schema": cfg.get("schema", "CILLIAN_TEST"),
    }


# ------------------------------------------------------------------
# Mapping history (persisted in Snowflake) — which reports are mapped,
# their coverage, and the ability to re-open a past run.
# ------------------------------------------------------------------
HISTORY_SCHEMA = "DATA_ENGINEERING_HOME.CILLIAN_TEST"
RUN_TBL = f"{HISTORY_SCHEMA}.MAPPING_RUN"
RESULT_TBL = f"{HISTORY_SCHEMA}.MAPPING_RESULT"


@st.cache_resource(show_spinner=False)
def ensure_history_tables(_session) -> bool:
    """Create the history tables once per server run if they don't exist."""
    try:
        _session.sql(
            f"""CREATE TABLE IF NOT EXISTS {RUN_TBL} (
                RUN_ID STRING NOT NULL,
                REPORT_NAME STRING, SOURCE_FILE STRING,
                ROW_COUNT NUMBER, COLUMN_COUNT NUMBER,
                MAPPED_COUNT NUMBER, REVIEW_COUNT NUMBER, UNMAPPED_COUNT NUMBER,
                AVG_CONFIDENCE NUMBER, STATUS STRING DEFAULT 'draft',
                CREATED_BY STRING,
                CREATED_AT TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
                UPDATED_AT TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
                NOTES STRING,
                CONSTRAINT PK_MAPPING_RUN PRIMARY KEY (RUN_ID)
            )"""
        ).collect()
        _session.sql(
            f"""CREATE TABLE IF NOT EXISTS {RESULT_TBL} (
                RUN_ID STRING NOT NULL, COL_ORDER NUMBER,
                LEGACY_COLUMN STRING NOT NULL, LEGACY_DTYPE STRING,
                SAMPLE_VALUES STRING, TARGET_LAYER STRING,
                TARGET_OBJECT STRING, TARGET_COLUMN STRING,
                CONFIDENCE NUMBER, MATCH_BASIS STRING, STATUS STRING,
                IS_OVERRIDE BOOLEAN DEFAULT FALSE, CANDIDATES STRING,
                RATIONALE STRING, FOUND_IN STRING, DISPOSITION STRING,
                OWNER STRING, DECISION STRING, NOTES STRING,
                REVIEWED_BY STRING, REVIEWED_AT TIMESTAMP_NTZ
            )"""
        ).collect()
        # Defensive: add newer columns to any table created before they existed.
        for _col in (
            "CANDIDATES STRING", "FOUND_IN STRING", "DISPOSITION STRING",
            "OWNER STRING", "DECISION STRING", "NOTES STRING",
        ):
            _session.sql(
                f"ALTER TABLE {RESULT_TBL} ADD COLUMN IF NOT EXISTS {_col}"
            ).collect()
        return True
    except Exception as exc:  # noqa: BLE001
        st.warning(f"Could not provision history tables: {exc}")
        return False


def _sql_str(v) -> str:
    """Return a SQL string literal (single-quoted, escaped) or NULL."""
    if v is None:
        return "NULL"
    s = str(v)
    if s == "" or s.lower() == "nan":
        return "NULL"
    return "'" + s.replace("'", "''") + "'"


def map_status(confidence, target_column) -> str:
    """Classify a row as mapped / review / unmapped from confidence + target."""
    try:
        c = float(confidence)
    except (TypeError, ValueError):
        c = 0.0
    tgt = str(target_column or "").strip().lower()
    if not tgt or tgt == "none":
        return "unmapped"
    if c >= 80:
        return "mapped"
    if c >= 50:
        return "review"
    return "unmapped"


# Four-tier medallion disposition. Maps the layer a column was found in to the
# business action required to make it usable in the strategic model.
_LAYER_FOUND_IN = {
    "GOLD": "Data Product",
    "SILVER": "EDW",
    "BRONZE": "Raw",
}

# Decision values a reviewer can set on each action-register row.
DECISION_VALUES = [
    "Pending", "Accept", "Promote", "Curate", "Source externally", "Reject",
]


def _found_in(layer) -> str:
    """Human label for the layer a column's match was found in (or Not found)."""
    return _LAYER_FOUND_IN.get(str(layer or "").strip().upper(), "Not found")


def _disposition(layer, target_column, status) -> str:
    """The action implied by where (and whether) a column was matched.

    GOLD   -> already in a Data Product, use it.
    SILVER -> in EDW; decide whether to promote it into a Data Product.
    BRONZE -> only in raw; must be curated up to EDW + Data Product.
    none   -> not in Snowflake at all; must be sourced externally.
    """
    tgt = str(target_column or "").strip().lower()
    lyr = str(layer or "").strip().upper()
    if not tgt or tgt == "none" or status == "unmapped":
        return "Source externally"
    if lyr == "GOLD":
        return "Use (Data Product)"
    if lyr == "SILVER":
        return "Promote to Data Product"
    if lyr == "BRONZE":
        return "Curate to EDW + Data Product"
    return "Source externally"


def save_mapping_run(session, report_name: str, source_file: str,
                     row_count: int, mapping: pd.DataFrame) -> str | None:
    """Persist a mapping run + its per-column results. Returns the RUN_ID."""
    if not ensure_history_tables(session):
        return None
    run_id = uuid.uuid4().hex

    work = mapping.copy()
    work["confidence"] = pd.to_numeric(work["confidence"], errors="coerce").fillna(0)
    statuses = [
        map_status(r["confidence"], r["target_attribute"])
        for _, r in work.iterrows()
    ]
    mapped = statuses.count("mapped")
    review = statuses.count("review")
    unmapped = statuses.count("unmapped")
    avg = int(round(work["confidence"].mean())) if len(work) else 0

    try:
        session.sql(
            f"INSERT INTO {RUN_TBL} (RUN_ID, REPORT_NAME, SOURCE_FILE, ROW_COUNT, "
            "COLUMN_COUNT, MAPPED_COUNT, REVIEW_COUNT, UNMAPPED_COUNT, "
            "AVG_CONFIDENCE, STATUS, CREATED_BY) SELECT "
            f"{_sql_str(run_id)}, {_sql_str(report_name)}, {_sql_str(source_file)}, "
            f"{int(row_count)}, {len(work)}, {mapped}, {review}, {unmapped}, {avg}, "
            "'draft', CURRENT_USER()"
        ).collect()

        values = []
        for order, ((_, r), status) in enumerate(zip(work.iterrows(), statuses)):
            cand = r.get("candidates", [])
            cand_json = json.dumps(cand) if isinstance(cand, (list, dict)) else ""
            is_override = "TRUE" if bool(r.get("is_override", False)) else "FALSE"
            values.append(
                "(" + ", ".join([
                    _sql_str(run_id), str(order),
                    _sql_str(r["legacy_attribute"]),
                    _sql_str(r.get("legacy_dtype", "")),
                    _sql_str(r.get("samples_str", "")),
                    _sql_str(r.get("layer", "")),
                    _sql_str(r.get("target_view", "")),
                    _sql_str(r.get("target_attribute", "")),
                    str(int(float(r["confidence"]))),
                    _sql_str(r.get("match_basis", "")),
                    _sql_str(status), is_override,
                    _sql_str(cand_json),
                    _sql_str(r.get("rationale", "")),
                    _sql_str(r.get("found_in", "")),
                    _sql_str(r.get("disposition", "")),
                    _sql_str(r.get("owner", "")),
                    _sql_str(r.get("decision", "Pending")),
                    _sql_str(r.get("notes", "")),
                ]) + ")"
            )
        if values:
            session.sql(
                f"INSERT INTO {RESULT_TBL} (RUN_ID, COL_ORDER, LEGACY_COLUMN, "
                "LEGACY_DTYPE, SAMPLE_VALUES, TARGET_LAYER, TARGET_OBJECT, "
                "TARGET_COLUMN, CONFIDENCE, MATCH_BASIS, STATUS, IS_OVERRIDE, "
                "CANDIDATES, RATIONALE, FOUND_IN, DISPOSITION, OWNER, DECISION, "
                "NOTES) VALUES " + ", ".join(values)
            ).collect()
        list_mapping_runs.clear()  # new run must appear in Historic Runs
        st.session_state.pop("_runs_cache", None)
        return run_id
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not save mapping run: {exc}")
        return None


@st.cache_data(show_spinner=False, ttl=120)
def list_mapping_runs(_session, limit: int = 100) -> pd.DataFrame:
    """Return recent mapping runs (most recent first). Cached to keep the
    Historic Runs page instant across reruns; cleared on save/status change."""
    if not ensure_history_tables(_session):
        return pd.DataFrame()
    try:
        return _session.sql(
            "SELECT RUN_ID, REPORT_NAME, SOURCE_FILE, COLUMN_COUNT, MAPPED_COUNT, "
            "REVIEW_COUNT, UNMAPPED_COUNT, AVG_CONFIDENCE, STATUS, CREATED_BY, "
            f"CREATED_AT FROM {RUN_TBL} ORDER BY CREATED_AT DESC LIMIT {int(limit)}"
        ).to_pandas()
    except Exception as exc:  # noqa: BLE001
        st.warning(f"Could not load history: {exc}")
        return pd.DataFrame()


@st.cache_data(show_spinner=False, ttl=300)
def load_mapping_run(_session, run_id: str) -> pd.DataFrame:
    """Return the per-column results for a saved run, in original order (cached)."""
    try:
        return _session.sql(
            "SELECT COL_ORDER, LEGACY_COLUMN, LEGACY_DTYPE, SAMPLE_VALUES, "
            "TARGET_LAYER, TARGET_OBJECT, TARGET_COLUMN, CONFIDENCE, MATCH_BASIS, "
            f"STATUS, RATIONALE FROM {RESULT_TBL} WHERE RUN_ID = {_sql_str(run_id)} "
            "ORDER BY COL_ORDER"
        ).to_pandas()
    except Exception as exc:  # noqa: BLE001
        st.warning(f"Could not load run: {exc}")
        return pd.DataFrame()


def update_run_status(session, run_id: str, status: str) -> None:
    """Mark a run as draft/confirmed."""
    try:
        session.sql(
            f"UPDATE {RUN_TBL} SET STATUS = {_sql_str(status)}, "
            f"UPDATED_AT = CURRENT_TIMESTAMP() WHERE RUN_ID = {_sql_str(run_id)}"
        ).collect()
        list_mapping_runs.clear()  # status badge must refresh in the list
        st.session_state.pop("_runs_cache", None)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not update status: {exc}")


def _sniff_delimiter(sample: str) -> str:
    """Guess the column delimiter of a text sample.

    Legacy extracts are frequently pipe- or tab-delimited rather than comma
    separated. We pick the candidate that both appears and yields the most
    consistent number of columns across the first few lines.
    """
    candidates = ["|", "\t", ";", ",", "~"]
    lines = [ln for ln in sample.splitlines() if ln.strip()][:10]
    if not lines:
        return ","

    best, best_score = ",", -1.0
    for delim in candidates:
        counts = [ln.count(delim) for ln in lines]
        if max(counts) == 0:
            continue  # delimiter not present at all
        # Prefer delimiters that appear many times and consistently per line.
        avg = sum(counts) / len(counts)
        consistency = 1.0 if len(set(counts)) == 1 else 0.5
        score = avg * consistency
        if score > best_score:
            best, best_score = delim, score
    return best


def read_report(uploaded, delimiter: str | None = None) -> pd.DataFrame:
    """Read an uploaded CSV or Excel file into a DataFrame.

    For delimited text we auto-detect the separator (comma, pipe, tab, etc.)
    so pipe-delimited legacy extracts are parsed into proper columns. Pass an
    explicit `delimiter` to override auto-detection.
    """
    name = uploaded.name.lower()
    data = uploaded.read()
    if name.endswith((".xlsx", ".xlsm", ".xls")):
        return pd.read_excel(BytesIO(data))

    # Decode a sample to sniff the delimiter (unless one was supplied).
    text = data.decode("utf-8-sig", errors="replace")
    delim = delimiter or _sniff_delimiter(text)
    df = pd.read_csv(BytesIO(data), sep=delim, engine="python", encoding="utf-8-sig")

    # Drop columns that are unnamed AND entirely empty (e.g. from a trailing
    # delimiter at the end of each line).
    drop = [
        c
        for c in df.columns
        if str(c).startswith("Unnamed:") and df[c].isna().all()
    ]
    if drop:
        df = df.drop(columns=drop)
    return df


def attribute_profile(df: pd.DataFrame, max_samples: int = 3) -> list[dict]:
    """Build a light profile for each column to send to the agent."""
    profile = []
    for col in df.columns:
        series = df[col].dropna()
        samples = [str(v) for v in series.head(max_samples).tolist()]
        profile.append(
            {
                "attribute": str(col),
                "dtype": str(df[col].dtype),
                "samples": samples,
            }
        )
    return profile


# ------------------------------------------------------------------
# Prompt building + response parsing
# ------------------------------------------------------------------
def build_prompt(attr: dict) -> str:
    """Ask the agent to map a single legacy attribute to the new model."""
    samples = ", ".join(attr["samples"]) if attr["samples"] else "(none)"
    return (
        "You are helping migrate a legacy report to the strategic data model, a "
        "medallion architecture: DATA_PRODUCT_CORE is GOLD (preferred) and "
        "EDW_CORE is SILVER (fallback). Identify the single best matching column, "
        "preferring a GOLD match and only falling back to SILVER when GOLD has "
        "none. If the name is unclear, match on the SAMPLE VALUES using the "
        "catalog's value_pattern and sample_values (value-based matching).\n\n"
        "IMPORTANT - expand common abbreviations when matching column names: "
        "PTY=PARTY, CTRY=COUNTRY, CCY=CURRENCY, CD=CODE, NM/NME=NAME, "
        "TOT=TOTAL, BS=BALANCE SHEET, AMT=AMOUNT, QTY=QUANTITY, DT=DATE, "
        "ISO_3=three-letter ISO code, ISO_2/ISO_3_CODE=ISO country code. "
        "e.g. legacy 'PTY_TYPE' should match column 'PARTY_TYPE'. Do NOT map to "
        "an unrelated column (e.g. an issuer name) just because it is GOLD - a "
        "correct SILVER column beats a wrong GOLD one.\n\n"
        "SOURCE CONTEXT (EDW SERVICE_CD): 'LGIM' rows are the legacy data plumbed "
        "in from the old Phoenix warehouse (the same lineage as this legacy "
        "report); 'CRDEF' rows are the strategic data. A column whose service "
        "codes include LGIM is strong evidence it carries the legacy Phoenix "
        "attribute you are mapping.\n\n"
        f"Legacy attribute name: {attr['attribute']}\n"
        f"Data type: {attr['dtype']}\n"
        f"Sample values: {samples}\n\n"
        "Respond with ONLY a compact JSON object using these keys:\n"
        '{"target_object": "<EDW_CORE.<TABLE> or DATA_PRODUCT_CORE.<TABLE>>", '
        '"target_column": "<matching column name or null>", '
        '"layer": "<GOLD or SILVER>", '
        '"match_basis": "<name|value|pattern>", '
        '"confidence": <0-100 integer>, '
        '"rationale": "<one short sentence>"}\n'
        "Always report target_object as the physical schema.table (EDW_CORE or "
        "DATA_PRODUCT_CORE), never a semantic view name."
    )


def build_batch_prompt(profile: list[dict]) -> str:
    """Ask the agent to map ALL legacy attributes in a single call.

    Mapping every attribute in one round-trip is dramatically faster than one
    call per attribute. The agent is asked to return a JSON array, one object
    per input attribute, echoing the legacy name so we can align the results.
    """
    lines = []
    for a in profile:
        samples = ", ".join(a["samples"]) if a["samples"] else "(none)"
        lines.append(
            f'- name: "{a["attribute"]}" | type: {a["dtype"]} | samples: {samples}'
        )
    listing = "\n".join(lines)
    return (
        "You are helping migrate a legacy report to the strategic data model, a "
        "medallion architecture: DATA_PRODUCT_CORE is GOLD (preferred) and "
        "EDW_CORE is SILVER (fallback). For EACH legacy attribute below, identify "
        "the single best matching column, preferring a GOLD match and only "
        "falling back to SILVER when GOLD has none. If a name is unclear, match on "
        "the sample values using the catalog's value_pattern and sample_values "
        "(value-based matching).\n\n"
        "IMPORTANT - expand common abbreviations when matching: PTY=PARTY, "
        "CTRY=COUNTRY, CCY=CURRENCY, CD=CODE, NM/NME=NAME, TOT=TOTAL, "
        "BS=BALANCE SHEET, AMT=AMOUNT, QTY=QUANTITY, DT=DATE. e.g. 'PTY_TYPE' "
        "matches 'PARTY_TYPE'. A correct SILVER column beats a wrong GOLD one; "
        "reference entities like PARTY/counterparty exist only in EDW_CORE. "
        "SOURCE CONTEXT (EDW SERVICE_CD): 'LGIM' rows are legacy Phoenix data "
        "(same lineage as this report); 'CRDEF' is strategic data.\n\n"
        f"Legacy attributes:\n{listing}\n\n"
        "Respond with ONLY a compact JSON array. Return exactly one object per "
        "legacy attribute, in the same order, using these keys:\n"
        '[{"legacy_attribute": "<echo the legacy name>", '
        '"target_object": "<EDW_CORE.<TABLE> or DATA_PRODUCT_CORE.<TABLE>>", '
        '"target_column": "<matching column name or null>", '
        '"layer": "<GOLD or SILVER>", '
        '"match_basis": "<name|value|pattern>", '
        '"confidence": <0-100 integer>, '
        '"rationale": "<one short sentence>"}]\n'
        "Always report target_object as the physical schema.table (EDW_CORE or "
        "DATA_PRODUCT_CORE), never a semantic view name."
    )


def parse_agent_json(text: str) -> dict:
    """Extract a JSON object from an agent reply (tolerant of extra prose).

    The agent may stream several JSON fragments (or wrap the object in prose).
    We scan for every balanced ``{...}`` block and return the last one that
    parses successfully and contains a recognisable mapping key.
    """
    if not text:
        return {}

    candidates: list[dict] = []
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start != -1:
                    chunk = text[start : i + 1]
                    try:
                        obj = json.loads(chunk)
                        if isinstance(obj, dict):
                            candidates.append(obj)
                    except json.JSONDecodeError:
                        pass
                    start = -1

    # Prefer the last object that actually looks like a mapping result or the
    # agent's structured envelope (search/map/lineage).
    for obj in reversed(candidates):
        if any(
            k in obj
            for k in (
                "target_attribute",
                "target_view",
                "target_object",
                "target_column",
                "confidence",
                "mappings",
                "authoritative_source",
                "available_fields",
                "intent",
            )
        ):
            return obj
    if candidates:
        return candidates[-1]

    return {"rationale": text.strip()[:200]}


def parse_agent_json_array(text: str) -> list[dict]:
    """Extract a JSON array of mapping objects from a batch agent reply.

    Tolerant of extra prose and of the agent streaming the array in fragments.
    Returns [] if no usable array/objects are found.
    """
    if not text:
        return []

    # First, try to find a top-level [...] array and parse it directly.
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == "[":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "]":
            if depth > 0:
                depth -= 1
                if depth == 0 and start != -1:
                    try:
                        arr = json.loads(text[start : i + 1])
                        if isinstance(arr, list):
                            objs = [o for o in arr if isinstance(o, dict)]
                            if objs:
                                return objs
                    except json.JSONDecodeError:
                        pass
                    start = -1

    # Fallback: collect every balanced {...} object in document order.
    objs: list[dict] = []
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start != -1:
                    try:
                        obj = json.loads(text[start : i + 1])
                        if isinstance(obj, dict):
                            objs.append(obj)
                    except json.JSONDecodeError:
                        pass
                    start = -1
    return objs


def _coerce_mapping(parsed: dict) -> dict:
    """Normalise any agent shape into flat mapping keys.

    The agent sometimes ignores the requested flat shape and returns its
    structured envelope instead (intent/authoritative_source/available_fields
    or a mappings array). Pull the best target out of whichever shape we got so
    the app never shows a spurious "No match" when the agent actually found one.
    """
    if not isinstance(parsed, dict):
        return {}
    # Already flat.
    if parsed.get("target_object") or parsed.get("target_view") or parsed.get("target_column") or parsed.get("target_attribute"):
        return parsed
    # Envelope: mappings array -> first mapping.
    maps = parsed.get("mappings")
    if isinstance(maps, list) and maps:
        m = maps[0]
        if isinstance(m, dict):
            return {
                "target_object": m.get("target_object") or "",
                "target_column": m.get("target_column") or "",
                "layer": m.get("layer", ""),
                "match_basis": m.get("match_basis", ""),
                "confidence": m.get("confidence", 0) or 0,
                "rationale": m.get("transformation") or parsed.get("summary", "") or "",
            }
    # Envelope: search shape -> authoritative_source + best available field.
    src = parsed.get("authoritative_source") or {}
    fields = parsed.get("available_fields") or []
    obj = str(src.get("physical_object") or src.get("dataset") or "")
    layer = src.get("layer", "")
    col = ""
    if isinstance(fields, list) and fields:
        # Prefer a field in the authoritative object; else the first field.
        pick = next(
            (f for f in fields if isinstance(f, dict) and str(f.get("physical_object", "")) == obj),
            fields[0] if isinstance(fields[0], dict) else {},
        )
        col = pick.get("name") or pick.get("column") or ""
        if not obj:
            obj = str(pick.get("physical_object", ""))
        if not layer:
            layer = pick.get("layer", "")
    if obj or col:
        return {
            "target_object": obj,
            "target_column": col,
            "layer": layer,
            "match_basis": "name",
            # Envelope search has no explicit confidence; signal a found match.
            "confidence": 60,
            "rationale": src.get("reason") or parsed.get("summary", "") or "",
        }
    return parsed


def _row_from_parsed(legacy: str, parsed: dict) -> dict:
    """Normalise a parsed mapping object into a result row.

    Accepts the new physical-object keys (target_object / target_column) and
    falls back to the legacy keys (target_view / target_attribute) so older
    responses still render.
    """
    parsed = _coerce_mapping(parsed)
    obj = parsed.get("target_object") or parsed.get("target_view") or ""
    col = parsed.get("target_column") or parsed.get("target_attribute") or ""
    layer = str(parsed.get("layer", "") or "").upper()
    # Infer layer from the object if the agent didn't state it.
    if not layer and obj:
        up = str(obj).upper()
        if "DATA_PRODUCT_CORE" in up:
            layer = "GOLD"
        elif "EDW_CORE" in up:
            layer = "SILVER"
    return {
        "legacy_attribute": legacy,
        "target_view": obj,
        "target_attribute": col,
        "layer": layer,
        "match_basis": str(parsed.get("match_basis", "") or ""),
        "confidence": parsed.get("confidence", 0) or 0,
        "rationale": parsed.get("rationale", "") or "",
    }


def _extract_mappings(text: str) -> list[dict]:
    """Return mapping objects from a batch reply.

    Handles both shapes the agent produces: a bare JSON array of mapping
    objects, or the structured envelope object containing a "mappings" array.
    """
    objs = parse_agent_json_array(text)
    # If we got the envelope object(s), unwrap their mappings array.
    flat: list[dict] = []
    saw_envelope = False
    for o in objs:
        if isinstance(o, dict) and isinstance(o.get("mappings"), list):
            saw_envelope = True
            for m in o["mappings"]:
                if isinstance(m, dict):
                    flat.append(m)
    if saw_envelope and flat:
        return flat
    return objs


def _legacy_key(o: dict) -> str:
    """Echoed legacy name from either the flat or envelope mapping shape."""
    for k in ("legacy_attribute", "legacy_field", "legacy", "name", "attribute"):
        v = o.get(k)
        if v:
            return str(v).strip()
    return ""


def _map_chunk(session, agent, chunk: list[dict]) -> dict[str, dict]:
    """Map a small chunk of attributes in ONE agent call.

    Returns {legacy_name: row}. Names the chunk failed to return are simply
    absent, so the caller can retry just those individually.
    """
    resp = run_agent(
        session,
        agent_name=agent["name"],
        database=agent["database"],
        schema=agent["schema"],
        prompt=build_batch_prompt(chunk),
    )
    if resp.error:
        return {}

    objs = _extract_mappings(resp.text)
    if not objs:
        return {}

    names = [c["attribute"] for c in chunk]
    lower = {n.lower(): n for n in names}
    by_name: dict[str, dict] = {}
    for o in objs:
        key = _legacy_key(o).lower()
        if key in lower:
            by_name[lower[key]] = o

    out: dict[str, dict] = {}
    if by_name:
        for name, parsed in by_name.items():
            out[name] = _row_from_parsed(name, parsed)
    elif len(objs) == len(names):
        # No usable echo, but right count -> align by order.
        for name, parsed in zip(names, objs):
            out[name] = _row_from_parsed(name, parsed)
    return out


def _map_single(session, agent, attr: dict) -> dict:
    """Map one attribute in its own call (used to fill chunk gaps)."""
    resp = run_agent(
        session,
        agent_name=agent["name"],
        database=agent["database"],
        schema=agent["schema"],
        prompt=build_prompt(attr),
    )
    if resp.error:
        return {
            "legacy_attribute": attr["attribute"],
            "target_view": "",
            "target_attribute": "",
            "confidence": 0,
            "rationale": f"ERROR: {resp.error}",
        }
    return _row_from_parsed(attr["attribute"], parse_agent_json(resp.text))


def map_attributes(
    session,
    agent,
    profile: list[dict],
    chunk_size: int = 12,
    max_workers: int = 6,
) -> pd.DataFrame:
    """Map every attribute fast, with a live progress bar.

    Strategy: split the attributes into small chunks and map each chunk in a
    SINGLE agent call, running the chunks concurrently. This is dramatically
    fewer round-trips than one-call-per-attribute (e.g. ~5 calls for 60
    attributes instead of 60), while small chunks keep the agent's output
    aligned. Any attribute a chunk fails to return is retried individually so
    nothing is silently dropped.
    """
    if not profile:
        return pd.DataFrame(
            columns=[
                "legacy_attribute",
                "target_view",
                "target_attribute",
                "confidence",
                "rationale",
            ]
        )

    from concurrent.futures import ThreadPoolExecutor, as_completed

    total = len(profile)
    by_name_result: dict[str, dict] = {}
    progress = st.progress(0.0, text=f"Mapping attributes… (0/{total})")

    chunks = [profile[i : i + chunk_size] for i in range(0, total, chunk_size)]

    def done_count() -> int:
        return len(by_name_result)

    with ThreadPoolExecutor(max_workers=min(max_workers, len(chunks))) as pool:
        futures = {pool.submit(_map_chunk, session, agent, ch): ch for ch in chunks}
        for fut in as_completed(futures):
            ch = futures[fut]
            try:
                rows = fut.result()
            except Exception:  # noqa: BLE001
                rows = {}
            by_name_result.update(rows)
            progress.progress(
                min(done_count() / total, 1.0),
                text=f"Mapping attributes… ({min(done_count(), total)}/{total})",
            )

    # Retry any attributes the chunks didn't return, individually + concurrently.
    missing = [a for a in profile if a["attribute"] not in by_name_result]
    if missing:
        progress.progress(
            min(done_count() / total, 1.0),
            text=f"Finishing {len(missing)} attribute(s)…",
        )
        with ThreadPoolExecutor(max_workers=min(max_workers, len(missing))) as pool:
            futures = {
                pool.submit(_map_single, session, agent, a): a for a in missing
            }
            for fut in as_completed(futures):
                a = futures[fut]
                try:
                    row = fut.result()
                except Exception as exc:  # noqa: BLE001
                    row = {
                        "legacy_attribute": a["attribute"],
                        "target_view": "",
                        "target_attribute": "",
                        "confidence": 0,
                        "rationale": f"ERROR: {exc}",
                    }
                by_name_result[a["attribute"]] = row
                progress.progress(
                    min(done_count() / total, 1.0),
                    text=f"Mapping attributes… ({min(done_count(), total)}/{total})",
                )

    progress.empty()

    # Preserve original attribute order.
    ordered = []
    for a in profile:
        name = a["attribute"]
        ordered.append(
            by_name_result.get(
                name,
                {
                    "legacy_attribute": name,
                    "target_view": "",
                    "target_attribute": "",
                    "confidence": 0,
                    "rationale": "No mapping returned.",
                },
            )
        )
    return pd.DataFrame(ordered)


# ------------------------------------------------------------------
# In-database mapping engine (Streamlit-in-Snowflake native — no network)
# ------------------------------------------------------------------
# Ordered preference of Cortex LLMs to try for the reranker. The first that
# succeeds in the account/region is cached and reused.
_CORTEX_MODELS = ["claude-3-5-sonnet", "llama3.1-70b", "mistral-large2", "llama3.1-8b"]


def _sql_lit(text: str) -> str:
    """Escape a Python string for safe embedding in a Snowflake string literal."""
    return str(text).replace("\\", " ").replace("'", "''")


@st.cache_resource(show_spinner=False)
def _cortex_model(_session) -> str:
    """Return the first Cortex COMPLETE model that works here (cached)."""
    for model in _CORTEX_MODELS:
        try:
            _session.sql(
                f"SELECT SNOWFLAKE.CORTEX.COMPLETE('{model}', 'ok') AS R"
            ).collect()
            return model
        except Exception:  # noqa: BLE001 — try the next model
            continue
    return ""


def _cortex_complete(session, prompt: str) -> str:
    """Run SNOWFLAKE.CORTEX.COMPLETE in-database and return the text answer.

    Fully server-side (no outbound network), so it works in Streamlit-in-
    Snowflake without an External Access Integration. Returns '' on failure so
    callers fall back to the deterministic top candidate.
    """
    model = _cortex_model(session)
    if not model:
        return ""
    sql = (
        f"SELECT SNOWFLAKE.CORTEX.COMPLETE('{model}', '{_sql_lit(prompt)}') AS R"
    )
    try:
        return str(session.sql(sql).collect()[0]["R"] or "")
    except Exception:  # noqa: BLE001
        return ""


def _llm_pick_best(session, name: str, dtype: str, samples: list[str],
                   candidates: list[dict]) -> tuple[int, int, str] | None:
    """Have an in-database LLM choose the best target among grounded candidates.

    This is the 'reasoned tie-break' the REST agent gave us, but SiS-native: the
    model can only choose from the top candidates the Cortex Search + rerank
    already found, so it can never hallucinate a table/column. Returns
    (index, confidence 0-100, rationale) or None if the model is unavailable or
    its answer can't be parsed (caller keeps the deterministic top candidate).
    """
    if not candidates:
        return None
    sample_txt = ", ".join([str(s).strip() for s in (samples or []) if str(s).strip()][:8]) or "(none)"
    lines = []
    for i, c in enumerate(candidates):
        src = c.get("source_label", "") or ("—" if c.get("layer") == "GOLD" else "")
        desc = str(c.get("description", "") or "")[:120]
        lines.append(
            f"{i}. object={c.get('object','')} column={c.get('column','')} "
            f"layer={c.get('layer','')} source={src} "
            f"class={c.get('classification','')} basis={c.get('basis','')} "
            f"desc=\"{desc}\""
        )
    cand_block = "\n".join(lines)
    prompt = (
        "You are mapping a legacy report column to the strategic data model. "
        "Choose the SINGLE best target from the numbered candidates below.\n"
        "Decide primarily on BUSINESS MEANING, not on layer:\n"
        "- The target must mean the same business thing as the legacy column; "
        "use the sample values as evidence.\n"
        "- For a generic column (a plain date, code, id, name or amount) the "
        "column name alone is NOT enough. Pick the candidate whose TABLE "
        "(object) subject-area fits the attribute, and prefer the canonical / "
        "master-data entity (e.g. party, issuer, security, instrument, account) "
        "over an analytics / snapshot table. Only choose an analytics AS_OF / "
        "snapshot date when the legacy report is itself an analytics extract.\n"
        "- Between two equally-good business matches, prefer GOLD (data product) "
        "over SILVER (EDW), and a STRATEGIC source over a LEGACY (Phoenix) one.\n"
        "- If none is a genuine match, set index to -1.\n"
        "Confidence must be HONEST: 85-96 only with strong evidence (exact name "
        "or matching sample values); 55-75 for a solid name/subject match; "
        "20-45 when the only basis is a generic name with no value evidence.\n\n"
        f"Legacy column: {name}\n"
        f"Data type: {dtype or 'unknown'}\n"
        f"Sample values: {sample_txt}\n\n"
        f"Candidates:\n{cand_block}\n\n"
        "Respond with ONLY a JSON object, no prose, no code fence, exactly: "
        '{\"index\": <int>, \"confidence\": <int 0-100>, \"reason\": \"<short>\"}'
    )
    raw = _cortex_complete(session, prompt)
    if not raw:
        return None
    # Extract the first JSON object from the model's text.
    try:
        s = raw.find("{")
        e = raw.rfind("}")
        obj = json.loads(raw[s : e + 1]) if s != -1 and e != -1 else {}
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(obj, dict) or "index" not in obj:
        return None
    try:
        idx = int(obj["index"])
    except (TypeError, ValueError):
        return None
    if idx < 0 or idx >= len(candidates):
        return None
    try:
        conf = int(float(obj.get("confidence", candidates[idx].get("confidence", 0))))
    except (TypeError, ValueError):
        conf = int(candidates[idx].get("confidence", 0))
    conf = max(0, min(100, conf))
    reason = str(obj.get("reason", "") or "").strip()
    return idx, conf, reason


def _native_row(session, attr: dict) -> dict:
    """Map one attribute fully in-database: candidates -> LLM pick -> row."""
    name = attr["attribute"]
    samples = attr.get("samples", []) or []
    cands = candidate_targets(session, name, samples, top_n=5)
    if not cands:
        return {
            "legacy_attribute": name, "target_view": "", "target_attribute": "",
            "layer": "", "match_basis": "", "confidence": 0,
            "rationale": "No catalogue candidate found.",
        }
    pick = _llm_pick_best(session, name, attr.get("dtype", ""), samples, cands)
    if pick is None:
        best = cands[0]
        conf, reason = best["confidence"], "Top catalogue match (grounded search)."
    else:
        idx, llm_conf, reason = pick
        best = cands[idx]
        reason = reason or "Chosen by in-database reranker from grounded candidates."
        # Blend the model's judgement with the deterministic evidence score so a
        # generic-name pick can't report inflated confidence: average the two,
        # but never exceed the evidence ceiling by more than a small margin.
        ev_conf = int(best.get("confidence", 0))
        conf = int(round((llm_conf + ev_conf) / 2))
        conf = min(conf, ev_conf + 15)
    return {
        "legacy_attribute": name,
        "target_view": best["object"],
        "target_attribute": best["column"],
        "layer": best["layer"],
        "match_basis": best["basis"],
        "confidence": conf,
        "rationale": reason,
    }


def map_attributes_native(session, profile: list[dict],
                          max_workers: int = 6) -> pd.DataFrame:
    """SiS-native mapping: grounded Cortex Search + in-database LLM reranker.

    Mirrors map_attributes' output shape but needs no outbound network, so it
    runs inside Streamlit-in-Snowflake. Each column's top candidates come from
    Cortex Search + deterministic rerank; an in-database CORTEX.COMPLETE call
    then picks the best of those grounded candidates.
    """
    if not profile:
        return pd.DataFrame(columns=[
            "legacy_attribute", "target_view", "target_attribute",
            "confidence", "rationale",
        ])

    from concurrent.futures import ThreadPoolExecutor

    total = len(profile)
    results: dict[str, dict] = {}
    progress = st.progress(0.0, text=f"Mapping attributes… (0/{total})")
    done = 0
    with ThreadPoolExecutor(max_workers=min(max_workers, total)) as pool:
        futs = {pool.submit(_native_row, session, a): a for a in profile}
        from concurrent.futures import as_completed
        for fut in as_completed(futs):
            a = futs[fut]
            try:
                row = fut.result()
            except Exception as exc:  # noqa: BLE001
                row = {
                    "legacy_attribute": a["attribute"], "target_view": "",
                    "target_attribute": "", "layer": "", "match_basis": "",
                    "confidence": 0, "rationale": f"ERROR: {exc}",
                }
            results[a["attribute"]] = row
            done += 1
            progress.progress(min(done / total, 1.0),
                              text=f"Mapping attributes… ({done}/{total})")
    progress.empty()

    ordered = [results.get(a["attribute"], {
        "legacy_attribute": a["attribute"], "target_view": "",
        "target_attribute": "", "confidence": 0, "rationale": "No mapping.",
    }) for a in profile]
    return pd.DataFrame(ordered)


# ------------------------------------------------------------------
# Presentation helpers
# ------------------------------------------------------------------
def _confidence_band(conf) -> str:
    """Return 'good' | 'warn' | 'bad' for a confidence score."""
    try:
        c = float(conf)
    except (TypeError, ValueError):
        return "bad"
    if c >= 80:
        return "good"
    if c >= 50:
        return "warn"
    return "bad"


def render_summary(df: pd.DataFrame) -> None:
    """Render headline metrics above the mapping."""
    total = len(df)
    conf = pd.to_numeric(df["confidence"], errors="coerce").fillna(0)
    high = int((conf >= 80).sum())
    med = int(((conf >= 50) & (conf < 80)).sum())
    low = int((conf < 50).sum())
    avg = round(conf.mean(), 0) if total else 0

    cards = (
        '<div class="metric-row">'
        f'<div class="metric-card"><div class="label">Attributes</div>'
        f'<div class="value">{total}</div></div>'
        f'<div class="metric-card"><div class="label">High confidence</div>'
        f'<div class="value good">{high}</div></div>'
        f'<div class="metric-card"><div class="label">Needs review</div>'
        f'<div class="value warn">{med}</div></div>'
        f'<div class="metric-card"><div class="label">Low / unmatched</div>'
        f'<div class="value bad">{low}</div></div>'
        f'<div class="metric-card"><div class="label">Avg confidence</div>'
        f'<div class="value">{int(avg)}%</div></div>'
        '</div>'
    )
    st.markdown(cards, unsafe_allow_html=True)


def render_cards(df: pd.DataFrame) -> None:
    """Render each mapping as a clear, colour-coded card."""
    import html as _html

    for _, row in df.iterrows():
        band = _confidence_band(row["confidence"])
        target = str(row["target_attribute"]).strip()
        if target and target.lower() != "none":
            target_html = f'<span class="map-target">{_html.escape(target)}</span>'
        else:
            target_html = '<span class="map-target none">No match found</span>'
        view = str(row["target_view"]).strip()
        view_html = (
            f'<span class="map-view">in {_html.escape(view)}</span>' if view else ""
        )
        layer = str(row.get("layer", "") or "").upper()
        layer_html = ""
        if layer in ("GOLD", "SILVER", "BRONZE"):
            layer_html = f'<span class="layer-badge {layer.lower()}">{layer}</span>'
        basis = str(row.get("match_basis", "") or "").lower()
        basis_html = (
            f'<span class="basis-badge">{_html.escape(basis)} match</span>'
            if basis in ("value", "pattern")
            else ""
        )
        rationale = _html.escape(str(row["rationale"]).strip())
        legacy = _html.escape(str(row["legacy_attribute"]))
        try:
            conf_val = int(float(row["confidence"]))
        except (TypeError, ValueError):
            conf_val = 0

        rationale_html = (
            f'<div class="map-rationale">{rationale}</div>' if rationale else ""
        )
        # NOTE: keep this HTML on single lines with no leading indentation —
        # Streamlit's markdown treats 4+ leading spaces as a code block.
        card = (
            f'<div class="map-card {band}">'
            f'<div class="map-flow">'
            f'<span class="map-legacy">{legacy}</span>'
            f'<span class="map-arrow">&#8594;</span>'
            f'{target_html}{view_html}{layer_html}{basis_html}'
            f'<span style="flex:1"></span>'
            f'<span class="pill {band}">{conf_val}% confidence</span>'
            f'</div>{rationale_html}</div>'
        )
        st.markdown(card, unsafe_allow_html=True)


# ------------------------------------------------------------------
# Catalogue search — served DIRECTLY from CORE_COLUMN_CATALOG (fast + grounded)
# ------------------------------------------------------------------
CATALOG_FQN = "DATA_ENGINEERING_HOME.CILLIAN_TEST.CORE_COLUMN_CATALOG"
# Cortex Search service over the same catalogue (semantic retrieval).
CORTEX_SEARCH_FQN = "DATA_ENGINEERING_HOME.CILLIAN_TEST.CORE_CATALOG_SEARCH"

# Expand common legacy abbreviations so a search term still finds the column.
SEARCH_ABBREV = {
    "pty": "party", "ctry": "country", "cty": "country", "ccy": "currency",
    "cd": "code", "nm": "name", "nme": "name", "tot": "total", "amt": "amount",
    "qty": "quantity", "dt": "date", "rtg": "rating", "cpty": "counterparty",
    "org": "organisation", "bmk": "benchmark", "val": "value", "px": "price",
}


_SEARCH_STOP = {
    "where", "can", "i", "find", "the", "in", "of", "a", "an", "for", "show",
    "me", "all", "and", "or", "to", "is", "are", "list", "get", "which",
    "what", "data", "product", "core", "edw", "gold", "silver", "layer",
    "column", "columns", "field", "fields", "table",
}


@st.cache_data(show_spinner=False, ttl=600)
def _catalog_query(_session, sql: str) -> pd.DataFrame:
    """Run a catalogue SQL statement and return a DataFrame (cached per SQL)."""
    return _session.sql(sql).to_pandas()


# Columns we ask the Cortex Search service to return for every hit.
_SEARCH_COLUMNS = [
    "PHYSICAL_OBJECT", "COLUMN_NAME", "COLUMN_COMMENT", "LAYER",
    "LAYER_RANK", "DATA_TYPE", "VALUE_PATTERN", "SAMPLE_VALUES",
    "SERVICE_CODES",
]


@st.cache_data(show_spinner=False, ttl=600)
def cortex_search_catalog(_session, query: str, limit: int = 25,
                          layer: str | None = None) -> list[dict]:
    """Semantic retrieval from the CORE_CATALOG_SEARCH Cortex Search service.

    Returns catalogue columns ranked by semantic similarity to `query`. Results
    are normalised to the same shape the SQL search produces so callers can use
    either path interchangeably. Returns [] on any failure so callers can fall
    back to the deterministic ILIKE search.
    """
    q = (query or "").strip()
    if not q:
        return []
    payload: dict = {"query": q, "columns": _SEARCH_COLUMNS, "limit": int(limit)}
    if layer:
        payload["filter"] = {"@eq": {"LAYER": layer}}
    js = json.dumps(payload).replace("'", "''")
    sql = (
        "SELECT PARSE_JSON(SNOWFLAKE.CORTEX.SEARCH_PREVIEW('"
        f"{CORTEX_SEARCH_FQN}', '{js}')):results AS RESULTS"
    )
    try:
        raw = _session.sql(sql).collect()[0]["RESULTS"]
        rows = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:  # noqa: BLE001 — caller falls back to SQL search
        return []
    out: list[dict] = []
    for r in (rows or []):
        out.append({
            "physical_object": str(r.get("PHYSICAL_OBJECT", "")),
            "column_name": str(r.get("COLUMN_NAME", "")),
            "description": str(r.get("COLUMN_COMMENT") or "").strip(),
            "layer": str(r.get("LAYER") or "").upper(),
            "layer_rank": r.get("LAYER_RANK"),
            "data_type": str(r.get("DATA_TYPE") or "").strip(),
            "value_pattern": str(r.get("VALUE_PATTERN") or "").upper(),
            "sample_values": str(r.get("SAMPLE_VALUES") or "").strip(),
            "service_codes": str(r.get("SERVICE_CODES") or "").strip(),
        })
    return out


# ------------------------------------------------------------------
# Ranked candidate retrieval (retrieve-then-rerank for mapping)
# ------------------------------------------------------------------
def _legacy_tokens(name: str) -> list[str]:
    """Tokenise a legacy column name with abbreviation expansion.

    Splits on non-alphanumerics and camelCase, drops stop words, and adds the
    expanded form of any known abbreviation (PTY->PARTY etc.) so the candidate
    search matches the strategic column even when the legacy name is cryptic.
    """
    raw = re.findall(r"[a-z0-9]+", (name or "").lower().replace("_", " "))
    toks: list[str] = []
    for t in raw:
        if len(t) < 2 or t in _SEARCH_STOP:
            continue
        toks.append(t)
        if t in SEARCH_ABBREV:
            toks.append(SEARCH_ABBREV[t])
    seen: set[str] = set()
    return [t for t in toks if not (t in seen or seen.add(t))][:8]


def _infer_value_pattern(samples: list[str]) -> str:
    """Best-effort shape detection for a legacy column's sample values.

    Mirrors the catalogue's VALUE_PATTERN vocabulary so we can boost a candidate
    whose fingerprint matches the shape of the legacy data (name-independent).
    """
    vals = [str(v).strip() for v in (samples or []) if str(v).strip()]
    if not vals:
        return ""
    import re as _re

    def all_match(rx) -> bool:
        return all(_re.fullmatch(rx, v, _re.IGNORECASE) for v in vals)

    if all_match(r"[A-Z]{2}[A-Z0-9]{9}[0-9]"):
        return "ISIN"
    if all_match(r"[A-Z0-9]{18}[0-9]{2}"):
        return "LEI"
    if all_match(r"[B-DF-HJ-NP-TV-XZ0-9]{6,7}"):
        return "SEDOL"
    if all_match(r"[A-Z]{3}"):
        return "ISO_CCY"
    if all_match(r"[A-Z]{2}"):
        return "ISO_COUNTRY"
    if all_match(r"\d{4}-\d{2}-\d{2}.*") or all_match(r"\d{2}/\d{2}/\d{4}"):
        return "DATE"
    if all_match(r"-?\d+"):
        return "INTEGER"
    if all_match(r"-?\d+\.\d+"):
        return "DECIMAL"
    return ""


_VALUE_STOP = {"", "NONE", "NULL", "N/A", "NA", "-", "(NONE)"}


def _norm_values(values) -> set:
    """Normalise a column's example values into a comparable set of tokens.

    Accepts either a list of samples or a comma-separated string (the catalogue
    stores SAMPLE_VALUES as a comma-joined string). Upper-cases and trims so a
    legacy column's values can be compared for real content overlap against a
    candidate strategic column's values — a name-independent match signal.
    """
    if isinstance(values, str):
        parts = values.split(",")
    else:
        parts = list(values or [])
    out = set()
    for p in parts:
        v = str(p).strip().upper()
        if v and v not in _VALUE_STOP:
            out.add(v)
    return out


@st.cache_data(show_spinner=False, ttl=3600)
def _object_service_codes(_session) -> dict:
    """Map each SILVER physical object to its EDW service code(s).

    EDW profiling records service codes at the table level, so every column in a
    physical object shares the same codes. Some catalogue rows have an empty
    SERVICE_CODES (not sampled); we use this per-object lookup to backfill them so
    a SILVER target always shows which service code it maps to. Cached for an hour.
    """
    sql = (
        "SELECT PHYSICAL_OBJECT, MAX(SERVICE_CODES) AS CODES\n"
        f"FROM {CATALOG_FQN}\n"
        "WHERE LAYER = 'SILVER' AND NULLIF(TRIM(SERVICE_CODES),'') IS NOT NULL\n"
        "GROUP BY PHYSICAL_OBJECT"
    )
    try:
        df = _catalog_query(_session, sql)
    except Exception:  # noqa: BLE001
        return {}
    return {
        str(r["PHYSICAL_OBJECT"]).upper(): str(r["CODES"] or "").strip()
        for _, r in df.iterrows()
    }


def _service_info(codes, layer: str = "") -> dict:
    """Classify a column's service codes into a compact UI/agent payload.

    GOLD (DATA_PRODUCT_CORE) data products are the strategic model itself and
    carry no service codes, so their source is left blank (implicitly
    strategic). SILVER (EDW_CORE) columns are sourced from one or more EDW
    service codes, so we surface which code(s) and whether that source is
    strategic (ADP / Fundipedia / Lipper / CRPM / CRDEF) or legacy (Phoenix).
    """
    lyr = str(layer or "").upper()
    if lyr == "GOLD":
        return {
            "classification": "STRATEGIC",
            "service_codes": "",
            "strategic_codes": "",
            "legacy_codes": "",
            "label": "",  # blank on purpose — GOLD has no service code
        }
    info = classify_service_codes(codes)
    cls = info["classification"]
    # Source label is just the service code(s); the badge conveys strategic vs
    # legacy separately, so we don't repeat the word here.
    label = ", ".join(info["all"])
    return {
        "classification": cls,
        "service_codes": ", ".join(info["all"]),
        "strategic_codes": ", ".join(info["strategic"]),
        "legacy_codes": ", ".join(info["legacy"]),
        "label": label,
    }


# Generic tokens carry little discriminating power — almost every table has a
# *_DATE, *_CODE, *_ID or *_NAME column, so a match on one of these alone is a
# weak signal and must NOT produce a confident mapping.
_GENERIC_TOKENS = {
    "date", "code", "id", "name", "type", "status", "amount", "value",
    "number", "num", "flag", "desc", "description", "key", "ref", "time",
    "day", "month", "year", "ind", "indicator", "text", "cd", "dt", "amt",
    "business", "record", "row", "entry", "item", "detail",
}


def _evidence_confidence(full: str, col: str, tokens: list[str],
                         pattern_match: bool, overlap: int, desc: str,
                         rank_prior: float) -> int:
    """Absolute, evidence-based confidence (0-100) for one candidate.

    Unlike a relative normalisation (which always makes the top candidate look
    ~95%), this scores the ACTUAL evidence so a weak, generic-only match reads
    low. Strong signals: exact column-name equality, real value-content overlap,
    specific (non-generic) token matches. Weak signals: generic tokens
    (date/code/id/name…), value-shape pattern, description mentions.
    """
    col_l = col.lower()
    ev = 0.0
    exact = (col == full)
    if exact:
        ev += 55
    if overlap:
        ev += min(overlap, 3) * 12          # up to +36 — strongest content signal
    if pattern_match:
        ev += 10
    spec = sum(1 for t in tokens if t not in _GENERIC_TOKENS and t in col_l)
    gen = sum(1 for t in tokens if t in _GENERIC_TOKENS and t in col_l)
    ev += min(spec, 2) * 14                  # specific name tokens are strong
    ev += min(gen, 2) * 5                     # generic tokens are weak
    dl = (desc or "").lower()
    dhits = sum(1 for t in tokens if t not in _GENERIC_TOKENS and t in dl)
    ev += min(dhits, 3) * 3
    ev += max(0.0, rank_prior)               # small semantic tie-break prior
    # If nothing strong lines up (no exact name, no value overlap, no specific
    # token), this is a generic guess — cap it so it can't look confident.
    strong = exact or overlap > 0 or spec > 0
    if not strong:
        ev = min(ev, 48)
    return int(round(max(10, min(96, ev))))


def _rerank_candidates(name: str, tokens: list[str], pattern: str,
                       hits: list[dict], top_n: int = 5,
                       samples: list[str] | None = None) -> list[dict]:
    """Rerank semantic hits with deterministic business signals.

    Cortex Search gives semantic ordering; we layer on the same signals the SQL
    path used — exact-name equality, token overlap on name/description, value
    pattern (shape) match, real value-content overlap against the candidate's
    example values, a GOLD preference and a strategic-source preference — then
    normalise to a 0-100 confidence. This is the 'rerank' half of
    retrieve-then-rerank.
    """
    full = (name or "").strip().upper()
    legacy_vals = _norm_values(samples)
    n = len(hits)
    scored: list[tuple[float, dict]] = []
    for idx, h in enumerate(hits):
        col = h["column_name"].upper()
        desc = (h["description"] or "").lower()
        svc = _service_info(h.get("service_codes", ""), h.get("layer", ""))
        # Base: preserve semantic order (earlier = higher).
        score = (n - idx) * 0.5
        if col == full:
            score += 12
        for t in tokens:
            if t in col.lower():
                score += 4
            if t in desc:
                score += 2
        if pattern and h.get("value_pattern", "").upper() == pattern:
            score += 3
        # Real value-content overlap: shared example values are a strong,
        # name-independent signal that both columns hold the same domain.
        overlap = len(legacy_vals & _norm_values(h.get("sample_values", ""))) if legacy_vals else 0
        if overlap:
            score += min(overlap, 3) * 4
        if h["layer"] == "GOLD":
            score += 1
        # Prefer strategic-sourced columns over purely legacy (Phoenix) ones
        # when mapping to EDW, so the migration target is surfaced first.
        if svc["classification"] == "STRATEGIC":
            score += 1.5
        elif svc["classification"] == "LEGACY":
            score -= 0.5
        scored.append((score, {**h, "_svc": svc, "_valmatch": overlap > 0}))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:top_n]
    out: list[dict] = []
    for rank_idx, (raw, h) in enumerate(top):
        col_u = h["column_name"].upper()
        matched_pattern = bool(pattern and h.get("value_pattern", "").upper() == pattern)
        overlap = len(legacy_vals & _norm_values(h.get("sample_values", ""))) if legacy_vals else 0
        conf = _evidence_confidence(
            full, col_u, tokens, matched_pattern, overlap,
            h.get("description", "") or "", rank_prior=max(0.0, 6 - rank_idx),
        )
        # Value-content match is the strongest evidence; if both value overlap
        # and value shape match, report 'both'.
        valmatch = bool(h.get("_valmatch"))
        if valmatch and matched_pattern:
            basis = "both"
        elif valmatch:
            basis = "value"
        elif matched_pattern:
            basis = "pattern"
        else:
            basis = "name"
        svc = h["_svc"]
        out.append({
            "layer": h["layer"],
            "object": h["physical_object"],
            "column": h["column_name"],
            "data_type": h["data_type"],
            "description": h["description"],
            "confidence": conf,
            "basis": basis,
            "classification": svc["classification"],
            "service_codes": svc["service_codes"],
            "source_label": svc["label"],
        })
    return out


def candidate_targets(session, name: str, samples: list[str] | None = None,
                      top_n: int = 5) -> list[dict]:
    """Return the top-N ranked strategic target columns for a legacy attribute.

    Retrieve-then-rerank: first pull semantically-similar catalogue columns from
    the Cortex Search service, then rerank them with deterministic business
    signals (exact name, token overlap, value pattern, GOLD preference). Falls
    back to the deterministic ILIKE scan straight from CORE_COLUMN_CATALOG if the
    search service is unavailable, so the app degrades gracefully.
    """
    tokens = _legacy_tokens(name)
    if not tokens:
        return []
    pattern = _infer_value_pattern(samples or [])

    # Per-object service-code backfill (SILVER columns with empty SERVICE_CODES).
    obj_codes = _object_service_codes(session)

    def _backfill(items: list[dict]) -> list[dict]:
        for it in items:
            if (it.get("layer") == "SILVER" and not it.get("service_codes")
                    and it.get("object")):
                codes = obj_codes.get(str(it["object"]).upper(), "")
                if codes:
                    svc = _service_info(codes, "SILVER")
                    it["classification"] = svc["classification"]
                    it["service_codes"] = svc["service_codes"]
                    it["source_label"] = svc["label"]
        return items

    # 1) Semantic retrieval + rerank. Enrich the query with the inferred value
    # shape and a few example values so retrieval considers content, not just
    # the column name (the catalogue's SEARCH_TEXT indexes both).
    sample_terms = [s for s in (samples or []) if str(s).strip()][:5]
    query = " ".join([name] + tokens + ([pattern] if pattern else []) + sample_terms)
    hits = cortex_search_catalog(session, query, limit=25)
    if hits:
        ranked = _rerank_candidates(name, tokens, pattern, hits, top_n=top_n,
                                    samples=samples)
        if ranked:
            return _backfill(ranked)

    # 2) Deterministic fallback: ILIKE scan of the catalogue.
    score_terms, where_terms = [], []
    full = (name or "").strip().replace("'", "''")
    # Exact column-name equality is the strongest signal.
    score_terms.append(f"IFF(UPPER(COLUMN_NAME)=UPPER('{full}'),12,0)")
    for t in tokens:
        e = t.replace("'", "''")
        score_terms.append(f"IFF(COLUMN_NAME ILIKE '%{e}%',4,0)")
        score_terms.append(f"IFF(COLUMN_COMMENT ILIKE '%{e}%',2,0)")
        score_terms.append(f"IFF(SEARCH_TEXT ILIKE '%{e}%',1,0)")
        where_terms.append(f"SEARCH_TEXT ILIKE '%{e}%'")
    if pattern:
        pe = pattern.replace("'", "''")
        score_terms.append(f"IFF(UPPER(VALUE_PATTERN)='{pe}',3,0)")
    score = " + ".join(score_terms)
    where = " OR ".join(where_terms)

    sql = (
        "SELECT PHYSICAL_OBJECT, COLUMN_NAME, COLUMN_COMMENT AS DESCRIPTION, "
        f"LAYER, DATA_TYPE, VALUE_PATTERN, SAMPLE_VALUES, SERVICE_CODES, ({score}) AS SCORE\n"
        f"FROM {CATALOG_FQN}\n"
        f"WHERE ({where})\n"
        "QUALIFY SCORE > 0\n"
        "ORDER BY SCORE DESC, LAYER_RANK ASC, PHYSICAL_OBJECT, COLUMN_NAME\n"
        f"LIMIT {int(top_n)}"
    )
    try:
        df = _catalog_query(session, sql)
    except Exception:  # noqa: BLE001
        return []

    # Normalise raw score into a 0-100 confidence for the top candidate band.
    # Add a value-content overlap boost first so shared example values lift a
    # candidate the same way they do on the semantic path.
    legacy_vals = _norm_values(samples)
    adjusted: list[tuple[float, bool, int, pd.Series]] = []
    for _, r in df.iterrows():
        overlap = (len(legacy_vals & _norm_values(r.get("SAMPLE_VALUES", "")))
                   if legacy_vals else 0)
        boost = min(overlap, 3) * 4.0
        adjusted.append((float(r["SCORE"]) + boost, overlap > 0, overlap, r))
    adjusted.sort(key=lambda x: x[0], reverse=True)
    out = []
    full_u = (name or "").strip().upper()
    for rank_idx, (raw, valmatch, overlap, r) in enumerate(adjusted):
        pat_match = bool(pattern and str(r["VALUE_PATTERN"]).upper() == pattern)
        conf = _evidence_confidence(
            full_u, str(r["COLUMN_NAME"]).upper(), tokens, pat_match, overlap,
            str(r["DESCRIPTION"] or ""), rank_prior=max(0.0, 6 - rank_idx),
        )
        svc = _service_info(r.get("SERVICE_CODES", ""), str(r["LAYER"] or ""))
        if valmatch and pat_match:
            basis = "both"
        elif valmatch:
            basis = "value"
        elif pat_match:
            basis = "pattern"
        else:
            basis = "name"
        out.append(
            {
                "layer": str(r["LAYER"] or "").upper(),
                "object": str(r["PHYSICAL_OBJECT"]),
                "column": str(r["COLUMN_NAME"]),
                "data_type": str(r["DATA_TYPE"] or ""),
                "description": str(r["DESCRIPTION"] or "").strip(),
                "confidence": conf,
                "basis": basis,
                "classification": svc["classification"],
                "service_codes": svc["service_codes"],
                "source_label": svc["label"],
            }
        )
    return _backfill(out)


# ------------------------------------------------------------------
# Rich renderers (enterprise components — no chat bubbles)
# ------------------------------------------------------------------
def _band(v) -> str:
    return _confidence_band(v)


def render_metrics(cols: int, coverage: int) -> None:
    """(kept for compatibility) simple metric strip."""
    pass


def render_mapping_grid(df: pd.DataFrame) -> None:
    """Render the attribute mapping as a dense, colour-coded grid."""
    import html as _html

    head = (
        '<div class="ghead">'
        '<div>Uploaded Column</div><div>Strategic Attribute</div>'
        '<div>Dataset</div><div>Confidence</div></div>'
    )
    rows = []
    for _, r in df.iterrows():
        band = _band(r["confidence"])
        band_label = {"good": "High", "warn": "Medium", "bad": "Low"}.get(band, "")
        try:
            cv = int(float(r["confidence"]))
        except (TypeError, ValueError):
            cv = 0
        tgt = str(r["target_attribute"]).strip()
        if tgt and tgt.lower() != "none":
            attr = f'<div class="col-attr">{_html.escape(tgt)}</div>'
        else:
            attr = '<div class="col-attr none">No match</div>'
        ds = _html.escape(str(r["target_view"]).strip() or "—")
        layer = str(r.get("layer", "") or "").upper()
        if layer in ("GOLD", "SILVER", "BRONZE"):
            ds += f' <span class="layer-badge {layer.lower()}">{layer}</span>'
        basis = str(r.get("match_basis", "") or "").lower()
        if basis in ("value", "pattern"):
            ds += f' <span class="basis-badge">{basis}</span>'
        cls = str(r.get("classification", "") or "").upper()
        src_label = str(r.get("source_label", "") or "")
        if cls in ("STRATEGIC", "LEGACY", "MIXED", "UNKNOWN") and src_label:
            ds += (f' <span class="src-badge {cls.lower()}" title="{_html.escape(src_label)}">'
                   f'{cls}</span>')
        src = _html.escape(str(r["legacy_attribute"]))
        rows.append(
            f'<div class="grow">'
            f'<div class="col-src">{src}</div>{attr}'
            f'<div class="col-ds">{ds}</div>'
            f'<div><span class="conf {band}" title="{band_label} confidence">'
            f'<span class="dot"></span>{cv}% <span style="color:var(--faint);font-weight:600">{band_label}</span></span></div>'
            f'</div>'
        )
    st.markdown(f'<div class="grid">{head}{"".join(rows)}</div>', unsafe_allow_html=True)


def attach_candidates(session, mapping: pd.DataFrame) -> pd.DataFrame:
    """Add a `candidates` column (ranked alternates) to a mapping DataFrame.

    For each legacy column we retrieve the top strategic targets directly from
    the catalogue. Where the agent returned nothing but a strong catalogue
    candidate exists, we promote the best candidate into the primary target so
    'unmapped' rows still surface a real suggestion for review.

    Candidate retrieval runs concurrently (Cortex Search is I/O-bound) with a
    live progress bar, so even a wide file resolves in a couple of seconds.
    """
    from concurrent.futures import ThreadPoolExecutor

    rows = list(mapping.iterrows())
    total = len(rows)
    cand_col: list[list[dict]] = [[] for _ in range(total)]

    def _fetch(i_r):
        i, r = i_r
        samples = str(r.get("samples_str", "") or "").split(",")
        return i, candidate_targets(session, r["legacy_attribute"], samples, top_n=5)

    if total:
        progress = st.progress(0.0, text=f"Retrieving candidates… (0/{total})")
        done = 0
        with ThreadPoolExecutor(max_workers=min(8, total)) as pool:
            for i, cands in pool.map(_fetch, list(enumerate([r for _, r in rows]))):
                cand_col[i] = cands
                done += 1
                progress.progress(done / total,
                                  text=f"Retrieving candidates… ({done}/{total})")
        progress.empty()

    mapping = mapping.copy().reset_index(drop=True)
    mapping["candidates"] = cand_col

    # Promote a candidate where the agent gave no target.
    for i, r in mapping.iterrows():
        tgt = str(r.get("target_attribute", "") or "").strip().lower()
        if (not tgt or tgt == "none") and cand_col[i]:
            top = cand_col[i][0]
            mapping.at[i, "target_view"] = top["object"]
            mapping.at[i, "target_attribute"] = top["column"]
            mapping.at[i, "layer"] = top["layer"]
            mapping.at[i, "match_basis"] = top["basis"]
            mapping.at[i, "confidence"] = top["confidence"]
            mapping.at[i, "rationale"] = "Suggested from catalogue (semantic match)."

    # Stamp each row with the source service code + strategic/legacy label of the
    # chosen target, so it is clear which service code the column maps to. We
    # first match the target back to its candidate (same object+column). The
    # agent may pick a column outside the top-5 candidates, so for any SILVER
    # target still missing a code we backfill from the per-object service-code
    # lookup — an EDW target must always show which service code it maps to.
    obj_codes = _object_service_codes(session)
    src_labels: list[str] = []
    src_class: list[str] = []
    for i, r in mapping.iterrows():
        obj = str(r.get("target_view", "") or "").strip().upper()
        col = str(r.get("target_attribute", "") or "").strip().upper()
        layer = str(r.get("layer", "") or "").strip().upper()
        label, cls = "", ""
        for c in (cand_col[i] or []):
            if (str(c.get("object", "")).upper() == obj
                    and str(c.get("column", "")).upper() == col):
                label = c.get("source_label", "")
                cls = c.get("classification", "")
                break
        if layer == "GOLD":
            # GOLD data products carry no service code — source stays blank.
            label, cls = "", "STRATEGIC"
        elif obj and not label:
            # SILVER without a matched candidate: backfill from the object's codes.
            codes = obj_codes.get(obj, "")
            svc = _service_info(codes, "SILVER")
            label, cls = svc["label"], svc["classification"]
        src_labels.append(label)
        src_class.append(cls)
    mapping["source_label"] = src_labels
    mapping["classification"] = src_class

    mapping["status"] = [
        map_status(r["confidence"], r["target_attribute"])
        for _, r in mapping.iterrows()
    ]
    mapping["found_in"] = [_found_in(r.get("layer", "")) for _, r in mapping.iterrows()]
    mapping["disposition"] = [
        _disposition(r.get("layer", ""), r.get("target_attribute", ""), r.get("status", ""))
        for _, r in mapping.iterrows()
    ]
    return mapping


@st.cache_data(show_spinner=False, ttl=3600)
def _catalog_targets(_session) -> dict:
    """Return the valid override targets straight from the catalogue.

    Powers the cascading override dropdowns (pick object -> pick column) so a
    reviewer can only choose real physical objects/columns, never a typo. Cached
    for an hour. Returns ordered objects (GOLD first), the columns available in
    each object, each object's layer, and each column's EDW service code(s).
    """
    empty = {"objects": [], "columns_by_object": {}, "object_layer": {}, "col_service": {}}
    sql = (
        "SELECT PHYSICAL_OBJECT, COLUMN_NAME, LAYER, "
        "COALESCE(SERVICE_CODES,'') AS SERVICE_CODES\n"
        f"FROM {CATALOG_FQN}\n"
        "ORDER BY LAYER_RANK, PHYSICAL_OBJECT, COLUMN_NAME"
    )
    try:
        df = _catalog_query(_session, sql)
    except Exception:  # noqa: BLE001
        return empty
    columns_by_object: dict[str, list] = {}
    object_layer: dict[str, str] = {}
    col_service: dict[tuple, str] = {}
    for _, r in df.iterrows():
        obj = str(r["PHYSICAL_OBJECT"])
        col = str(r["COLUMN_NAME"])
        columns_by_object.setdefault(obj, []).append(col)
        object_layer[obj] = str(r["LAYER"] or "").upper()
        col_service[(obj, col)] = str(r["SERVICE_CODES"] or "").strip()
    return {
        "objects": list(columns_by_object.keys()),
        "columns_by_object": columns_by_object,
        "object_layer": object_layer,
        "col_service": col_service,
    }


def _apply_override(mapping: pd.DataFrame, legacy: str, obj: str, col: str,
                    targets: dict) -> pd.DataFrame:
    """Set a chosen object/column as the target for one legacy attribute.

    Derives the layer, service-code source label and classification from the
    catalogue so the override is fully consistent with a machine match, marks it
    as a manual override at full confidence, and recomputes its status.
    """
    out = mapping.copy().reset_index(drop=True)
    idx = out.index[out["legacy_attribute"] == legacy]
    if not len(idx):
        return out
    i = idx[0]
    layer = targets["object_layer"].get(obj, "")
    codes = targets["col_service"].get((obj, col), "")
    svc = _service_info(codes, layer)
    out.at[i, "target_view"] = obj
    out.at[i, "target_attribute"] = col
    out.at[i, "layer"] = layer
    out.at[i, "source_label"] = svc["label"]
    out.at[i, "classification"] = svc["classification"]
    out.at[i, "confidence"] = 100
    out.at[i, "match_basis"] = "manual"
    out.at[i, "is_override"] = True
    out.at[i, "status"] = map_status(100, col)
    return out


def render_editable_mapping(mapping: pd.DataFrame, key: str) -> pd.DataFrame:
    """Slick read-only mapping grid + a cascading override panel. Returns the df.

    The grid is presentation-only; overrides are made below via dependent
    dropdowns (object -> column) sourced live from the catalogue, so a reviewer
    can only pick real targets. Applying an override updates the mapping in
    session state and reruns, so the grid reflects the change instantly.
    """
    import html as _html

    def _src_summary(r) -> str:
        # GOLD data products carry no service code, so their source is blank
        # (implicitly strategic). SILVER shows the service code(s) it maps to.
        if str(r.get("layer", "") or "").upper() == "GOLD":
            return ""
        return str(r.get("source_label", "") or "").strip()

    # ---- Calm read-only grid ---------------------------------------------
    head = (
        '<div class="ghead"><div>Report Attribute</div><div>Mapped Column</div>'
        '<div>Layer</div><div>EDW Source</div><div>Match</div><div>Status</div></div>'
    )
    rows_html = []
    for _, r in mapping.iterrows():
        legacy = _html.escape(str(r["legacy_attribute"]))
        obj = str(r.get("target_view", "") or "").strip()
        col = str(r.get("target_attribute", "") or "").strip()
        if col and col.lower() != "none":
            tgt = (f'<span class="tgt-col">{_html.escape(col)}</span>'
                   f'<span class="tgt-obj">{_html.escape(obj)}</span>')
        else:
            tgt = '<span class="col-attr none">No match</span>'
        layer = str(r.get("layer", "") or "").upper()
        layer_html = (f'<span class="layer-badge {layer.lower()}">{layer}</span>'
                      if layer in ("GOLD", "SILVER", "BRONZE") else '<span class="dash">—</span>')
        # EDW source = the service code(s) only (GOLD data products carry none).
        # No strategic/legacy wording here — this column is just the source code.
        src_label = _src_summary(r)
        if layer == "GOLD" or not src_label:
            src_html = '<span class="dash">—</span>'
        else:
            codes = [c.strip() for c in src_label.split(",") if c.strip()]
            shown = ", ".join(codes[:2])
            extra = len(codes) - 2
            more = (f' <span class="src-more" title="{_html.escape(src_label)}">'
                    f'+{extra}</span>') if extra > 0 else ""
            src_html = (f'<span class="src-codes" title="{_html.escape(src_label)}">'
                        f'{_html.escape(shown)}</span>{more}')
        # Match: how the target was found — value and/or pattern (else —).
        basis = str(r.get("match_basis", "") or "").lower()
        if basis == "both":
            match_html = ('<span class="match-badge value">value</span>'
                          '<span class="match-badge pattern">pattern</span>')
        elif basis == "value":
            match_html = '<span class="match-badge value">value</span>'
        elif basis == "pattern":
            match_html = '<span class="match-badge pattern">pattern</span>'
        else:
            match_html = '<span class="dash">—</span>'
        status = str(r.get("status", "") or "").lower()
        try:
            cv = int(float(r["confidence"]))
        except (TypeError, ValueError):
            cv = 0
        band = _band(cv)
        status_html = (f'<span class="status-pill {status}">{status or "—"}</span>'
                       f' <span class="conf {band}"><span class="dot"></span>{cv}%</span>')
        is_ovr = bool(r.get("is_override", False))
        ovr_tag = '<span class="ovr-tag">override</span>' if is_ovr else ""
        rows_html.append(
            f'<div class="grow{" ovr" if is_ovr else ""}">'
            f'<div class="col-src">{legacy}{ovr_tag}</div>'
            f'<div class="col-ds">{tgt}</div>'
            f'<div>{layer_html}</div><div>{src_html}</div>'
            f'<div>{match_html}</div>'
            f'<div>{status_html}</div></div>'
        )
    st.markdown(f'<div class="grid map5">{head}{"".join(rows_html)}</div>',
                unsafe_allow_html=True)

    # ---- Slick cascading override panel (object -> column) ----------------
    targets = _catalog_targets(get_session())
    if targets["objects"]:
        # Reset dependent selections when a parent changes, so switching layer
        # (GOLD<->SILVER) or object updates the child dropdowns cleanly in one go.
        def _reset_obj_col():
            st.session_state.pop(f"{key}_ov_obj", None)
            st.session_state.pop(f"{key}_ov_col", None)

        def _reset_col():
            st.session_state.pop(f"{key}_ov_col", None)

        with st.expander("✏️  Override a mapping — pick object, then column", expanded=False):
            # Row 1: which legacy column, and the layer filter.
            r1 = st.columns([2, 1])
            legacy_choice = r1[0].selectbox(
                "Legacy column", options=list(mapping["legacy_attribute"]),
                key=f"{key}_ov_legacy",
            )
            layer_filter = r1[1].selectbox(
                "Layer", options=["All", "GOLD", "SILVER", "BRONZE"],
                key=f"{key}_ov_layer",
                help="Filter the object list. GOLD (data products) is preferred.",
                on_change=_reset_obj_col,
            )
            # Row 2: object then column — full width so long names are visible.
            obj_options = [
                o for o in targets["objects"]
                if layer_filter == "All" or targets["object_layer"].get(o) == layer_filter
            ]
            obj_choice = st.selectbox(
                "Target object", options=obj_options, key=f"{key}_ov_obj",
                help="Physical object the column lives in.",
                on_change=_reset_col,
            )
            col_options = targets["columns_by_object"].get(obj_choice, [])
            col_choice = st.selectbox(
                "Target column", options=col_options, key=f"{key}_ov_col",
                help="Only columns that exist in the chosen object.",
            )
            # Live preview of the source this target resolves to.
            if obj_choice and col_choice:
                _lyr = targets["object_layer"].get(obj_choice, "")
                _svc = _service_info(
                    targets["col_service"].get((obj_choice, col_choice), ""), _lyr)
                _src = _svc["label"] or ("—" if _lyr == "GOLD" else "")
                st.caption(
                    f"Will map **{legacy_choice}** → **{obj_choice}.{col_choice}** "
                    f"· layer **{_lyr or '—'}** · source **{_src or '—'}** "
                    f"({_svc['classification'].title()})"
                )
            apply_ov = st.button(
                "Apply override", key=f"{key}_ov_apply", type="primary",
                disabled=not (obj_choice and col_choice),
            )
            if apply_ov:
                st.session_state["mapping"] = _apply_override(
                    mapping, legacy_choice, obj_choice, col_choice, targets)
                st.rerun()

    # Fold edits back into the working mapping, preserving prior overrides.
    out = mapping.copy().reset_index(drop=True)
    if "is_override" not in out.columns:
        out["is_override"] = False
    out["is_override"] = out["is_override"].fillna(False).astype(bool)
    out["status"] = [
        map_status(r["confidence"], r["target_attribute"]) for _, r in out.iterrows()
    ]
    return out


# ------------------------------------------------------------------
# UI
# ------------------------------------------------------------------
inject_css()
agent = agent_config()

# ---- Navigation state -----------------------------------------------------
if "view" not in st.session_state:
    st.session_state["view"] = "Home"


def go(view: str) -> None:
    st.session_state["view"] = view


# ---- Top navigation -------------------------------------------------------
st.markdown(
    '<div class="topnav">'
    '<div class="brandmark">'
    '<span class="logo">L&amp;G</span>'
    '<span class="product">Client Report AI Mapping Tool'
    '<small>Asset Management Data Marketplace</small></span>'
    '</div><span class="navspacer"></span>'
    f'<span class="status"><span class="dot"></span>Connected · {agent["name"]}</span>'
    '</div>',
    unsafe_allow_html=True,
)

# Mapping-focused destinations. Active page is highlighted.
NAV = [
    ("Home", "Overview"),
    ("Upload & Map", "Map a File"),
    ("Single Attribute", "Single Attribute"),
    ("Reports", "Historic Runs"),
    ("How it works", "How it works"),
]
st.markdown('<div class="navrow"></div>', unsafe_allow_html=True)
nav = st.columns([1.2, 1, 1, 1, 1, 1, 1.2])
for i, (target, label) in enumerate(NAV):
    is_active = st.session_state.get("view") == target
    nav[i + 1].button(
        label,
        use_container_width=True,
        on_click=go,
        args=(target,),
        key=f"nav_{target}",
        type="primary" if is_active else "secondary",
    )

view = st.session_state["view"]


# ---- Shared flows ---------------------------------------------------------
def run_mapping_flow(uploaded, key_prefix: str, show_delimiter: bool = True) -> None:
    """Parse an uploaded file, animate analysis, and render the mapping experience."""
    is_excel = uploaded.name.lower().endswith((".xlsx", ".xlsm", ".xls"))
    override = None
    if show_delimiter and not is_excel:
        labels = {"Auto-detect": None, "Pipe  |": "|", "Comma  ,": ",", "Tab": "\t", "Semicolon  ;": ";"}
        choice = st.selectbox("Column delimiter", list(labels), index=0, key=f"{key_prefix}_delim")
        override = labels[choice]

    try:
        df = read_report(uploaded, delimiter=override)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not read the file: {exc}")
        return

    profile = attribute_profile(df)
    sig = f"{uploaded.name}:{df.shape[0]}x{df.shape[1]}:{override}"

    # Map only when this file/config changes. The mapping steps show their own
    # live progress bar, so we don't add any artificial step animation here.
    if st.session_state.get(f"{key_prefix}_sig") != sig:
        session = get_session()
        # In Streamlit-in-Snowflake there is no outbound network for the REST
        # agent, so use the SiS-native engine (grounded Cortex Search + an
        # in-database CORTEX.COMPLETE reranker). Locally we keep the REST agent.
        if st.session_state.get("_is_sis"):
            mapping = map_attributes_native(session, profile)
        else:
            mapping = map_attributes(session, agent, profile)
        # Attach dtype + sample values per legacy column so the saved history
        # captures the evidence behind each mapping decision.
        prof_by_name = {p["attribute"]: p for p in profile}
        mapping["legacy_dtype"] = mapping["legacy_attribute"].map(
            lambda n: prof_by_name.get(n, {}).get("dtype", "")
        )
        mapping["samples_str"] = mapping["legacy_attribute"].map(
            lambda n: ", ".join(prof_by_name.get(n, {}).get("samples", []))
        )
        # Retrieve ranked catalogue candidates for every column (grounded
        # alternates) and promote the best candidate where the agent found none.
        mapping = attach_candidates(session, mapping)
        st.session_state["mapping"] = mapping
        st.session_state["source_name"] = uploaded.name
        st.session_state["source_rows"] = int(df.shape[0])
        st.session_state["profile"] = profile
        st.session_state.pop(f"{key_prefix}_saved", None)
        st.session_state[f"{key_prefix}_sig"] = sig

    mapping = st.session_state.get("mapping")
    if mapping is None or mapping.empty:
        return

    conf = pd.to_numeric(mapping["confidence"], errors="coerce").fillna(0)
    status_series = mapping.get("status", pd.Series(dtype=str))
    mapped = int((status_series == "mapped").sum())
    review = int((status_series == "review").sum())
    unmapped = int((status_series == "unmapped").sum())
    avg = int(round(conf.mean())) if len(conf) else 0

    st.markdown(
        '<div class="metric-row">'
        f'<div class="metric-card"><div class="label">Columns</div><div class="value">{df.shape[1]}</div></div>'
        f'<div class="metric-card"><div class="label">Mapped</div><div class="value good">{mapped}</div></div>'
        f'<div class="metric-card"><div class="label">Needs Review</div><div class="value warn">{review}</div></div>'
        f'<div class="metric-card"><div class="label">Unmapped</div><div class="value bad">{unmapped}</div></div>'
        f'<div class="metric-card"><div class="label">Avg Confidence</div><div class="value">{avg}%</div></div>'
        '</div>',
        unsafe_allow_html=True,
    )

    # ---- Four-tier gap analysis (where was each column found?) -------------
    found = mapping.get("found_in", pd.Series(["Not found"] * len(mapping)))
    n_dp = int((found == "Data Product").sum())
    n_edw = int((found == "EDW").sum())
    n_raw = int((found == "Raw").sum())
    n_none = int((found == "Not found").sum())
    st.markdown(
        '<div class="metric-row">'
        f'<div class="metric-card"><div class="label">In Data Product</div><div class="value good">{n_dp}</div></div>'
        f'<div class="metric-card"><div class="label">In EDW</div><div class="value">{n_edw}</div></div>'
        f'<div class="metric-card"><div class="label">In Raw</div><div class="value warn">{n_raw}</div></div>'
        f'<div class="metric-card"><div class="label">Not in Snowflake</div><div class="value bad">{n_none}</div></div>'
        '</div>',
        unsafe_allow_html=True,
    )
    gaps = []
    if n_edw:
        gaps.append(f"**{n_edw}** in EDW — decide whether to promote to a Data Product")
    if n_raw:
        gaps.append(f"**{n_raw}** only in Raw — curate up to EDW + Data Product")
    if n_none:
        gaps.append(f"**{n_none}** not found in Snowflake — source externally")
    if gaps:
        st.info("Action needed — " + "  ·  ".join(gaps))
    st.markdown(
        '<div class="subhead">Attribute Mapping (GOLD &rarr; SILVER &rarr; BRONZE)</div>',
        unsafe_allow_html=True,
    )
    st.caption(
        "Machine-suggested targets, GOLD first then SILVER. To change any row, "
        "use the override panel below — changes apply instantly and are flagged "
        "and saved to history."
    )
    edited = render_editable_mapping(mapping, key=f"{key_prefix}_editor")
    st.session_state["mapping"] = edited
    mapping = edited

    # ---- Ranked alternates for review / unmapped columns ------------------
    needs = mapping[mapping["status"].isin(["review", "unmapped"])]
    if not needs.empty:
        with st.expander(f"Suggested alternates for {len(needs)} column(s) to review"):
            for _, r in needs.iterrows():
                cands = r.get("candidates", []) or []
                st.markdown(
                    f'**{r["legacy_attribute"]}** '
                    f'<span style="color:var(--muted)">→ current: '
                    f'{r["target_view"] or "—"}.{r["target_attribute"] or "—"}</span>',
                    unsafe_allow_html=True,
                )
                if cands:
                    alt = pd.DataFrame([
                        {
                            "Layer": c["layer"], "Object": c["object"],
                            "Column": c["column"], "Type": c["data_type"],
                            "Source": c.get("source_label", ""),
                            "Confidence": c["confidence"],
                            "Description": c["description"][:80],
                        }
                        for c in cands
                    ])
                    st.dataframe(alt, hide_index=True, use_container_width=True)
                else:
                    st.caption("No catalogue candidates found — map manually above.")

    # ---- Actionable review register ---------------------------------------
    st.markdown('<div class="subhead">Action register</div>', unsafe_allow_html=True)
    st.caption(
        "One row per report attribute with the action required. Set an Owner, a "
        "Decision and Notes — these save with the run and export to CSV."
    )
    reg = pd.DataFrame({
        "Report Attribute": mapping["legacy_attribute"],
        "Found In": mapping.get("found_in", ""),
        "Target": [
            (f"{o}.{c}" if str(c or "").strip() and str(c).lower() != "none" else "—")
            for o, c in zip(mapping.get("target_view", ""), mapping.get("target_attribute", ""))
        ],
        "Confidence": pd.to_numeric(mapping["confidence"], errors="coerce").fillna(0).astype(int),
        "Disposition": mapping.get("disposition", ""),
        "Owner": mapping.get("owner", ""),
        "Decision": mapping.get("decision", "Pending").fillna("Pending")
        if "decision" in mapping else ["Pending"] * len(mapping),
        "Notes": mapping.get("notes", ""),
    })
    reg_edited = st.data_editor(
        reg,
        key=f"{key_prefix}_register",
        hide_index=True,
        use_container_width=True,
        disabled=["Report Attribute", "Found In", "Target", "Confidence", "Disposition"],
        column_config={
            "Decision": st.column_config.SelectboxColumn(
                "Decision", options=DECISION_VALUES, required=True, width="small",
            ),
            "Owner": st.column_config.TextColumn("Owner", width="small"),
            "Notes": st.column_config.TextColumn("Notes", width="large"),
        },
    )
    # Fold reviewer inputs back onto the mapping so they persist + export.
    mapping["owner"] = reg_edited["Owner"].values
    mapping["decision"] = reg_edited["Decision"].values
    mapping["notes"] = reg_edited["Notes"].values
    st.session_state["mapping"] = mapping

    # ---- Save to history + export -----------------------------------------
    c1, c2 = st.columns([1, 1])
    saved_id = st.session_state.get(f"{key_prefix}_saved")
    with c1:
        if saved_id:
            st.success(f"Saved to history · run {saved_id[:8]}")
        elif st.button("Save mapping run", key=f"{key_prefix}_save", type="primary",
                       use_container_width=True):
            session = get_session()
            rid = save_mapping_run(
                session,
                report_name=st.session_state.get("source_name", uploaded.name),
                source_file=uploaded.name,
                row_count=st.session_state.get("source_rows", df.shape[0]),
                mapping=mapping,
            )
            if rid:
                st.session_state[f"{key_prefix}_saved"] = rid
                st.rerun()
    with c2:
        base = st.session_state.get("source_name", "report").rsplit(".", 1)[0]
        export_cols = [c for c in mapping.columns if c != "candidates"]
        csv = mapping[export_cols].to_csv(index=False).encode("utf-8")
        st.download_button(
            "Export mapping (CSV)",
            data=csv,
            file_name=f"mapping_{base}.csv",
            mime="text/csv",
            key=f"{key_prefix}_dl",
            use_container_width=True,
        )


# ==================================================================
# ROUTER
# ==================================================================
if view == "Home":
    st.markdown(
        '<div class="hero">'
        '<div class="eyebrow">Legacy Report Mapping</div>'
        '<h1>Map legacy reports to the strategic data model</h1>'
        '<p>Upload a legacy report — or check a single attribute — and let Cortex '
        'match every column to its authoritative home in the strategic layers, '
        'preferring GOLD (Data Product) then SILVER (EDW). Review, confirm and '
        'keep a full history of what has been mapped.</p>'
        '<div class="stat-grid">'
        '<div class="stat"><div class="v">GOLD &rarr; SILVER</div><div class="k">Match priority</div></div>'
        '<div class="stat"><div class="v">7,810</div><div class="k">Target columns</div></div>'
        '<div class="stat"><div class="v">Cortex</div><div class="k">Matching engine</div></div>'
        '<div class="stat"><div class="v">Saved</div><div class="k">Run history</div></div>'
        '</div></div>',
        unsafe_allow_html=True,
    )

    st.markdown('<div class="tiles">', unsafe_allow_html=True)
    tcols = st.columns(2, gap="large")
    with tcols[0]:
        st.markdown(
            '<div class="tile"><div class="ic">&#128228;</div>'
            '<h3>Map a legacy file</h3>'
            '<p>Upload an Excel, CSV or delimited report. Every column is profiled '
            'and matched to a strategic attribute with a confidence score.</p></div>',
            unsafe_allow_html=True,
        )
        st.button("Upload a file", key="home_go_upload", use_container_width=True,
                  on_click=go, args=("Upload & Map",))
    with tcols[1]:
        st.markdown(
            '<div class="tile"><div class="ic">&#128269;</div>'
            '<h3>Check a single attribute</h3>'
            '<p>Paste one legacy field name (and a few sample values) to find its '
            'best strategic target — handy for ad-hoc questions.</p></div>',
            unsafe_allow_html=True,
        )
        st.button("Map an attribute", key="home_go_single", use_container_width=True,
                  on_click=go, args=("Single Attribute",))
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div style="height:1.3rem"></div>', unsafe_allow_html=True)
    st.button("View historic runs", key="home_go_reports", on_click=go,
              args=("Reports",))

elif view == "Upload & Map":
    st.markdown(
        '<div class="page-head"><span class="kicker">Mapping</span>'
        '<h1>Map a File</h1></div>',
        unsafe_allow_html=True,
    )
    up = st.file_uploader(
        "Upload", type=["csv", "xlsx", "xlsm", "xls", "txt"],
        key="page_upload", label_visibility="collapsed",
    )
    if up is not None:
        run_mapping_flow(up, "page")
    else:
        st.markdown(
            '<div class="state"><div class="glyph">&#128228;</div>'
            '<h3>Onboard a legacy report</h3>'
            '<p>Upload an Excel, CSV or delimited file. We profile every column and map it '
            'to the authoritative strategic attribute (GOLD first, then SILVER) with a '
            'confidence score. Save the run to keep a history you can revisit.</p></div>',
            unsafe_allow_html=True,
        )

elif view == "Single Attribute":
    st.markdown(
        '<div class="page-head"><span class="kicker">Mapping</span>'
        '<h1>Single Attribute</h1></div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<p class="sr-summary">Check where one legacy attribute maps to in the '
        'strategic model. Sample values sharpen the match when the name is cryptic.</p>',
        unsafe_allow_html=True,
    )
    with st.form("single_attr_form"):
        a1, a2 = st.columns([1, 1])
        name = a1.text_input("Legacy attribute name", placeholder="e.g. PTY_TYPE")
        samples = a2.text_input("Sample values (optional, comma-separated)",
                                placeholder="e.g. FUND, COUNTERPARTY, ISSUER")
        submitted = st.form_submit_button("Find strategic target", type="primary")

    if submitted and name.strip():
        attr = {
            "attribute": name.strip(),
            "dtype": "string",
            "samples": [s.strip() for s in samples.split(",") if s.strip()],
        }
        session = get_session()
        # Fast path: use the grounded Cortex Search + rerank engine directly.
        # This replaces the slow agent SSE round-trip — the top ranked candidate
        # IS the best strategic target, returned in well under a second.
        with st.spinner("Matching against the strategic catalogue…"):
            cands = candidate_targets(session, attr["attribute"], attr["samples"], top_n=5)
        if cands:
            top = cands[0]
            row = {
                "legacy_attribute": attr["attribute"],
                "target_view": top["object"],
                "target_attribute": top["column"],
                "layer": top["layer"],
                "match_basis": top["basis"],
                "confidence": top["confidence"],
                "classification": top.get("classification", ""),
                "source_label": top.get("source_label", ""),
                "rationale": (
                    f"Best semantic match in {top['layer']} "
                    f"({top['object']}.{top['column']})."
                    + (f" Source: {top['source_label']}." if top.get('source_label') else "")
                ),
            }
        else:
            row = {
                "legacy_attribute": attr["attribute"],
                "target_view": "", "target_attribute": "",
                "layer": "", "match_basis": "", "confidence": 0,
                "rationale": "No catalogue candidate found — try adding sample values.",
            }
        row["status"] = map_status(row["confidence"], row.get("target_attribute"))
        single_df = pd.DataFrame([row])
        st.markdown('<div style="height:.6rem"></div>', unsafe_allow_html=True)
        render_mapping_grid(single_df)
        rationale = str(row.get("rationale", "") or "").strip()
        if rationale:
            st.markdown(
                f'<p style="color:var(--muted);font-size:.9rem;margin-top:.6rem;">'
                f'{rationale}</p>',
                unsafe_allow_html=True,
            )
        if cands:
            st.markdown('<div class="subhead">Ranked candidates</div>',
                        unsafe_allow_html=True)
            st.dataframe(
                pd.DataFrame([
                    {
                        "Layer": c["layer"], "Object": c["object"],
                        "Column": c["column"], "Type": c["data_type"],
                        "Source": c.get("source_label", ""),
                        "Confidence": c["confidence"], "Basis": c["basis"],
                        "Description": c["description"][:100],
                    }
                    for c in cands
                ]),
                hide_index=True, use_container_width=True,
            )
    elif submitted:
        st.warning("Enter a legacy attribute name to map.")

elif view == "Reports":
    st.markdown(
        '<div class="page-head"><span class="kicker">History</span>'
        '<h1>Historic Runs</h1></div>',
        unsafe_allow_html=True,
    )
    session = get_session()

    # Detail view for a selected run.
    open_run = st.session_state.get("open_run")
    if open_run:
        if st.button("← Back to all runs", key="reports_back"):
            st.session_state.pop("open_run", None)
            st.rerun()
        meta = st.session_state.get("open_run_meta", {})
        st.markdown(
            f'<div class="subhead">{meta.get("name", "Report")} · '
            f'{meta.get("status", "draft").upper()}</div>',
            unsafe_allow_html=True,
        )
        detail = load_mapping_run(session, open_run)
        if detail.empty:
            st.info("No stored results for this run.")
        else:
            grid = detail.rename(columns={
                "LEGACY_COLUMN": "legacy_attribute",
                "TARGET_COLUMN": "target_attribute",
                "TARGET_OBJECT": "target_view",
                "TARGET_LAYER": "layer",
                "MATCH_BASIS": "match_basis",
                "CONFIDENCE": "confidence",
            })
            render_mapping_grid(grid)
            cc1, cc2 = st.columns([1, 1])
            with cc1:
                if meta.get("status") != "confirmed" and st.button(
                    "Mark as confirmed", key="reports_confirm", type="primary",
                    use_container_width=True):
                    update_run_status(session, open_run, "confirmed")
                    st.session_state["open_run_meta"]["status"] = "confirmed"
                    st.rerun()
            with cc2:
                csv = grid.to_csv(index=False).encode("utf-8")
                st.download_button(
                    "Export (CSV)", data=csv,
                    file_name=f"mapping_{meta.get('name','report')}.csv",
                    mime="text/csv", key="reports_dl", use_container_width=True,
                )
    else:
        hc1, hc2 = st.columns([5, 1])
        with hc2:
            if st.button("↻ Refresh", key="reports_refresh",
                         use_container_width=True):
                list_mapping_runs.clear()
                st.session_state.pop("_runs_cache", None)
                st.rerun()
        runs = st.session_state.get("_runs_cache")
        if runs is None:
            with st.spinner("Loading history…"):
                runs = list_mapping_runs(session)
            st.session_state["_runs_cache"] = runs
        if runs.empty:
            st.markdown(
                '<div class="state"><div class="glyph">&#128203;</div>'
                '<h3>No historic runs yet</h3>'
                '<p>Map a file and save the run to build up your history here.</p>'
                '</div>',
                unsafe_allow_html=True,
            )
            st.button("Map a file", key="reports_go_map", on_click=go,
                      args=("Upload & Map",))
        else:
            for _, r in runs.iterrows():
                cols = str(int(r["COLUMN_COUNT"] or 0))
                mapped = int(r["MAPPED_COUNT"] or 0)
                review = int(r["REVIEW_COUNT"] or 0)
                unmapped = int(r["UNMAPPED_COUNT"] or 0)
                avg = int(r["AVG_CONFIDENCE"] or 0)
                status = str(r["STATUS"] or "draft")
                created = str(r["CREATED_AT"])[:16]
                badge = ("gold" if status == "confirmed" else "silver")
                rc1, rc2 = st.columns([4, 1])
                with rc1:
                    st.markdown(
                        f'<div class="sr-group" style="margin-bottom:.5rem;">'
                        f'<div class="sr-obj">'
                        f'<span class="table">{r["REPORT_NAME"]}</span>'
                        f'<span class="layer-badge {badge}">{status.upper()}</span>'
                        f'<span class="count">{created} · {r["CREATED_BY"]}</span>'
                        f'</div>'
                        f'<div class="sr-col"><div><span class="cname">Coverage</span></div>'
                        f'<div class="cdesc">{cols} columns · '
                        f'<b style="color:var(--good)">{mapped} mapped</b> · '
                        f'<b style="color:var(--warn)">{review} review</b> · '
                        f'<b style="color:var(--bad)">{unmapped} unmapped</b> · '
                        f'avg {avg}%</div></div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                with rc2:
                    st.markdown('<div style="height:.4rem"></div>', unsafe_allow_html=True)
                    if st.button("Open", key=f"open_{r['RUN_ID']}",
                                 use_container_width=True):
                        st.session_state["open_run"] = r["RUN_ID"]
                        st.session_state["open_run_meta"] = {
                            "name": r["REPORT_NAME"], "status": status,
                        }
                        st.rerun()

elif view == "How it works":
    st.markdown(
        '<div class="page-head"><span class="kicker">About</span>'
        '<h1>How it works</h1></div>',
        unsafe_allow_html=True,
    )
    st.caption(
        "A plain-English walkthrough of how the tool maps a legacy report to the "
        "strategic data model — useful for sharing with a wider audience."
    )
    _doc = Path(__file__).parent / "docs" / "how_it_works.html"
    try:
        _html_doc = _doc.read_text(encoding="utf-8")
        components.html(_html_doc, height=3200, scrolling=True)
        st.download_button(
            "Download this page (HTML)",
            data=_html_doc.encode("utf-8"),
            file_name="how_it_works.html",
            mime="text/html",
        )
    except FileNotFoundError:
        st.warning("Explainer document not found (docs/how_it_works.html).")

# Retired discovery/domain surfaces route to the mapping home.
elif view in ("Discover", "Data Domains", "Strategic Datasets", "Lineage Explorer"):
    go("Home")
    st.rerun()

# ------------------------------------------------------------------
# Footer
# ------------------------------------------------------------------
st.markdown(
    '<div class="lg-footer">'
    '<span>&copy; Legal &amp; General Group plc &middot; <span class="brand">Asset Management Data Marketplace</span></span>'
    '<span>Powered by Snowflake Cortex &middot; Internal use only</span>'
    '</div>',
    unsafe_allow_html=True,
)
