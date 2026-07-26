"""
IndiGo Pricing Intelligence Dashboard  ·  v7
Team 5 — ISB Action Learning Project 2026

WHAT CHANGED IN v7 (all fixes from the pricing-lead review)
  Pricing engine
    - Cabin class is now a PRODUCT TIER multiplier applied before demand logic,
      not an additive adjustment competing for room under the cap. Previously a
      Business fare spent 80 of its 100 available points on cabin alone, so every
      demand signal was crushed by the cap.
    - Competition check now runs in two passes: a provisional fare is built from
      all non-competition signals, and THAT is compared against the competitor,
      rather than the untouched list price.
    - Competitor matching picks the flight with the nearest departure time using
      circular clock distance, so morning is compared with morning.
    - Cost per seat scales with cabin floor space, so Business profit is no longer
      overstated by costing a lie-flat seat like an economy seat.
  New capability
    - Triage view: every SKU ranked by revenue at risk with exception flags.
    - Overnight competitor movement.
    - Empirical booking pace curve, so load factor is read against a target.
    - Back-test of dynamic pricing versus flat base pricing over history.
    - Demand curve scatter (fare versus load factor).
    - Automatic outcome tracking: bookings in the 24h after each decision.
    - Analyst identity on every decision, standing route strategy, competitor
      snapshot captured at decision time, and an efficiency counter.

CONFIG LIVES IN STREAMLIT SECRETS. See README block at the bottom of this file.
"""

import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests
from datetime import datetime, timedelta


# ═════════════════════════════════════════════════════════════
# CONFIG
# ═════════════════════════════════════════════════════════════
def _secret(key, default=""):
    try:
        return st.secrets.get(key, default)
    except Exception:
        return default

GOOGLE_SHEET_NAME = _secret("GOOGLE_SHEET_NAME", "Price Intelligence")
LLM_PROVIDER      = str(_secret("LLM_PROVIDER", "gemini")).strip().lower()
GEMINI_API_KEY    = _secret("GEMINI_API_KEY", "")
GEMINI_MODEL      = _secret("GEMINI_MODEL", "gemini-2.5-flash-lite")
GROQ_API_KEY      = _secret("GROQ_API_KEY", "")
GROQ_MODEL        = _secret("GROQ_MODEL", "llama-3.3-70b-versatile")

COMPETITOR_TAB = "Competitor Prices"
INDIGO_OPS_TAB = "IndiGo Operations"
FEEDBACK_TAB   = "Feedback"
AI_LOG_TAB     = "AI Price Log"
STRATEGY_TAB   = "Route Strategy"

# Route base fare = ECONOMY list fare for that route
BASE_FARES = {
    "Mumbai to Delhi": 10000, "Bangalore to Delhi": 8000, "Mumbai to Goa": 7500,
    "Mumbai to Dubai": 14000, "Mumbai to London": 20000,
}
# Product tier multiplier, applied to base BEFORE demand adjustments
CABIN_MULT = {"Economy": 1.00, "Premium Economy": 1.50, "Business": 2.50}
# Cost scales with floor space a seat occupies, not with fare
CABIN_COST_MULT = {"Economy": 1.00, "Premium Economy": 1.40, "Business": 2.20}

COST_PER_SEAT = {   # economy-equivalent variable cost per seat
    "Mumbai to Delhi": 2800, "Bangalore to Delhi": 3200, "Mumbai to Goa": 1200,
    "Mumbai to Dubai": 4500, "Mumbai to London": 14000,
}
TOTAL_SEATS_MAP = {
    "Mumbai to Delhi": 180, "Bangalore to Delhi": 180, "Mumbai to Goa": 180,
    "Mumbai to Dubai": 220, "Mumbai to London": 280,
}
PASSENGER_ADJ = {
    "Adult": 0.00, "Corporate": -0.05, "Student": -0.10,
    "Senior Citizen": -0.08, "Child": -0.15,
}

# Demand adjustments are capped independently of the cabin tier
DEMAND_CAP_LO, DEMAND_CAP_HI = -0.30, 0.60

STRATEGIC_OPTIONS = [
    "None — let AI decide",
    "Grow Traffic — prioritise volume, price competitively",
    "Charge Premium — maximise revenue per seat",
    "Match Competition — stay within 3% of lowest competitor",
    "Holiday Surge — apply festival premium pricing",
    "Fill Last Seats — aggressive discounting to maximise load",
]

MANUAL_MINUTES_PER_SKU = 180   # charter claim: 2-4 hours of analyst work

NAVY, MAGENTA, SKY = "#1B2D6B", "#E91E8C", "#2F6FD0"
GREEN, AMBER, RED  = "#16A34A", "#D97706", "#DC2626"
GREY               = "#8095bd"

st.set_page_config(page_title="IndiGo · Pricing Intelligence",
                   page_icon="✈️", layout="wide",
                   initial_sidebar_state="expanded")


# ═════════════════════════════════════════════════════════════
# STYLES
# ═════════════════════════════════════════════════════════════
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600;700&family=DM+Mono:wght@400;500&display=swap');

html, body, [class*="css"] { font-family:'DM Sans',sans-serif; background:#F5F7FA; color:#1a2740; }
.main { background:#F5F7FA; }
.block-container { padding:2.7rem 1.4rem 2rem !important; max-width:100% !important; }

.pid-hdr { background:linear-gradient(100deg,#1B2D6B 0%,#2b4aa8 55%,#E91E8C 130%);
  padding:0.85rem 1.5rem; margin:0 -1.4rem 1rem; display:flex; align-items:center;
  justify-content:space-between; gap:1rem; flex-wrap:wrap;
  box-shadow:0 3px 18px rgba(27,45,107,0.22); border-radius:0 0 10px 10px; }
.pid-title { font-size:1.15rem; font-weight:700; color:#fff; line-height:1.2; }
.pid-sub { font-size:0.58rem; color:rgba(255,255,255,0.62); text-transform:uppercase;
  letter-spacing:0.08em; margin-top:0.15rem; }
.pid-ctx { display:flex; gap:1rem; align-items:center; flex-wrap:wrap; }
.pid-ctx-val { font-size:0.88rem; font-weight:700; color:#fff;
  font-family:'DM Mono',monospace; line-height:1.1; }
.pid-ctx-lbl { font-size:0.52rem; color:rgba(255,255,255,0.6);
  text-transform:uppercase; letter-spacing:0.07em; }
.pid-div { width:1px; height:26px; background:rgba(255,255,255,0.25); }
.live-pill { background:rgba(255,255,255,0.14); border:1px solid rgba(255,255,255,0.3);
  color:#8affc0; border-radius:20px; padding:0.2rem 0.65rem; font-size:0.58rem;
  font-weight:700; display:flex; align-items:center; gap:0.3rem; letter-spacing:0.06em; }
.live-dot { width:6px; height:6px; background:#2ecc71; border-radius:50%;
  animation:blink 1.8s infinite; }
@keyframes blink { 0%,100%{opacity:1} 50%{opacity:0.25} }

.sec-hd { font-size:0.63rem; font-weight:700; letter-spacing:0.14em;
  text-transform:uppercase; color:#1B2D6B; margin:0.3rem 0 0.6rem 0;
  display:flex; align-items:center; gap:0.5rem; }
.sec-hd::after { content:''; flex:1; height:1px; background:#dde3f0; }
.sec-note { font-size:0.68rem; color:#8095bd; margin:-0.3rem 0 0.7rem 0; }

.kpi-strip { display:grid; grid-template-columns:repeat(5,minmax(0,1fr));
  gap:0.75rem; margin-bottom:0.5rem; }
.kpi-strip4 { display:grid; grid-template-columns:repeat(4,minmax(0,1fr));
  gap:0.75rem; margin-bottom:0.5rem; }
.kpi-card { background:#fff; border:1px solid #dde3f0; border-radius:11px;
  padding:0.8rem 0.95rem; box-shadow:0 1px 7px rgba(27,45,107,0.06); min-width:0; }
.kpi-card.accent { border-left:3px solid #E91E8C; }
.kpi-val { font-size:1.4rem; font-weight:700; color:#1B2D6B;
  font-family:'DM Mono',monospace; line-height:1.1;
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.kpi-lbl { font-size:0.56rem; color:#6a80ad; text-transform:uppercase;
  letter-spacing:0.09em; margin-top:0.3rem; }
.kpi-sub { font-size:0.64rem; color:#8095bd; margin-top:0.12rem;
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.k-green{color:#16A34A !important;} .k-amber{color:#D97706 !important;}
.k-red{color:#DC2626 !important;} .k-mag{color:#E91E8C !important;}
.k-navy{color:#1B2D6B !important;} .k-grey{color:#8095bd !important;}

table.dt { width:100%; border-collapse:separate; border-spacing:0; font-size:0.72rem;
  border:1px solid #dde3f0; border-radius:10px; overflow:hidden;
  table-layout:fixed; background:#fff; }
table.dt thead tr { background:#f2f5fc; }
table.dt th { padding:0.48rem 0.5rem; font-size:0.56rem; font-weight:700;
  letter-spacing:0.07em; text-transform:uppercase; color:#1B2D6B;
  border-bottom:2px solid #dde3f0; text-align:left;
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
table.dt td { padding:0.42rem 0.5rem; border-bottom:1px solid #f2f5fc; color:#2a4060;
  font-family:'DM Mono',monospace; font-size:0.7rem;
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
table.dt tr:last-child td { border-bottom:none; }
table.dt tbody tr:hover td { background:#fafbff; }
.grp td { background:#eaf0fb !important; color:#1B2D6B !important;
  font-family:'DM Sans',sans-serif !important; font-weight:700 !important;
  font-size:0.7rem !important; padding:0.32rem 0.6rem !important;
  border-top:2px solid #c9d6f0 !important; }
.row-red td { background:#fff5f5; }
.row-amber td { background:#fffbf0; }

.flag { font-size:0.56rem; font-weight:700; padding:0.1rem 0.45rem;
  border-radius:20px; text-transform:uppercase; letter-spacing:0.05em; }
.flag-red { background:#fee2e2; color:#991b1b; border:1px solid #DC2626; }
.flag-amber { background:#fef3c7; color:#92400e; border:1px solid #D97706; }
.flag-green { background:#dcfce7; color:#166534; border:1px solid #16A34A; }

.f-navy{color:#1B2D6B !important;font-weight:600;}
.f-mag{color:#E91E8C !important;font-weight:600;}
.f-ai{color:#2F6FD0 !important;font-weight:700;}
.f-ailog{color:#0891b2 !important;font-weight:600;}
.f-cheap{color:#16A34A !important;font-weight:600;}
.f-exp{color:#DC2626 !important;} .f-sim{color:#D97706 !important;}
.up{color:#DC2626;font-weight:600;} .down{color:#16A34A;font-weight:600;}
.flat{color:#8095bd;}
.lf-g{color:#16A34A;font-weight:600;} .lf-a{color:#D97706;font-weight:600;}
.lf-r{color:#DC2626;font-weight:600;}

.ai-result { background:linear-gradient(135deg,#f4f7ff 0%,#fdf2f9 100%);
  border:1.5px solid #E91E8C; border-radius:11px; padding:0.9rem 1.1rem; }
.ai-badge-ok { display:inline-block; background:#dcfce7; border:1px solid #16A34A;
  color:#15803d; font-size:0.66rem; font-weight:700;
  padding:0.18rem 0.6rem; border-radius:20px; }
.ai-badge-ov { display:inline-block; background:#fef3c7; border:1px solid #D97706;
  color:#b45309; font-size:0.66rem; font-weight:700;
  padding:0.18rem 0.6rem; border-radius:20px; }
.ai-price { font-size:2rem; font-weight:700; color:#1B2D6B;
  font-family:'DM Mono',monospace; line-height:1.1; margin:0.35rem 0; }
.ai-rat { font-size:0.76rem; color:#3a5080; line-height:1.6; padding:0.5rem 0.75rem;
  background:rgba(255,255,255,0.85); border-left:3px solid #E91E8C;
  border-radius:0 6px 6px 0; margin-top:0.45rem; }
.engine-chip { font-size:0.56rem; font-weight:700; padding:0.12rem 0.5rem;
  border-radius:20px; text-transform:uppercase; letter-spacing:0.06em; }

.arith-box { background:#fafbff; border:1px solid #dde3f0; border-radius:9px;
  padding:0.55rem 0.85rem; font-size:0.69rem; color:#3a5080;
  font-family:'DM Mono',monospace; line-height:1.8; }
.bd-row { display:flex; justify-content:space-between;
  border-bottom:1px dashed #e6ebf7; padding:0.03rem 0; }
.bd-row.total { border-top:1px solid #c9d6f0; border-bottom:none;
  font-weight:700; color:#1B2D6B; margin-top:0.2rem; padding-top:0.2rem; }
.bd-head { color:#1B2D6B; font-weight:700; font-size:0.62rem;
  text-transform:uppercase; letter-spacing:0.08em; margin:0.35rem 0 0.1rem; }
.bd-pos{color:#DC2626;} .bd-neg{color:#16A34A;} .bd-neu{color:#8095bd;}

.flt-pill { background:#f2f5fc; border:1px solid #c9d6f0; border-radius:9px;
  padding:0.5rem 0.78rem; font-size:0.72rem; color:#2a4060;
  line-height:1.7; margin-bottom:0.6rem; }
.flt-pill-t { font-size:0.88rem; font-weight:700; color:#1B2D6B;
  font-family:'DM Mono',monospace; }

section[data-testid="stSidebar"] { background:#fff !important; border-right:1px solid #dde3f0; }
section[data-testid="stSidebar"] .block-container { padding:1rem 0.85rem; }
.sb-brand { font-size:0.9rem; font-weight:700; color:#1B2D6B; padding-bottom:0.75rem;
  border-bottom:2px solid #E91E8C; margin-bottom:0.85rem; }

.stSelectbox label, .stDateInput label, .stRadio>label,
.stNumberInput label, .stTextInput label, .stSlider label {
  color:#1B2D6B !important; font-size:0.6rem !important; font-weight:700 !important;
  text-transform:uppercase !important; letter-spacing:0.09em !important; }
.stSelectbox>div>div, .stTextInput>div>div>input { background:#fafbff !important;
  border:1px solid #c9d6f0 !important; color:#1a2740 !important; border-radius:8px !important; }
.stRadio>div { flex-direction:row !important; gap:0.5rem !important; flex-wrap:wrap !important; }
.stRadio>div>label { color:#2a4060 !important; font-size:0.73rem !important;
  text-transform:none !important; letter-spacing:0 !important; font-weight:500 !important;
  background:#fafbff; border:1px solid #c9d6f0; border-radius:6px; padding:0.18rem 0.6rem; }
.stButton>button { background:linear-gradient(120deg,#1B2D6B 0%,#E91E8C 140%);
  color:white; border:none; border-radius:8px; font-family:'DM Sans',sans-serif;
  font-size:0.8rem; font-weight:700; padding:0.45rem 1rem; width:100%;
  box-shadow:0 2px 9px rgba(27,45,107,0.22); }
.stButton>button:hover { background:linear-gradient(120deg,#E91E8C 0%,#1B2D6B 140%); }
div[data-testid="metric-container"] { background:#fff !important;
  border:1px solid #dde3f0 !important; border-radius:10px; padding:0.6rem 0.85rem;
  box-shadow:0 1px 6px rgba(27,45,107,0.05); }
div[data-testid="metric-container"] label { color:#6a80ad !important;
  font-size:0.58rem !important; text-transform:uppercase; letter-spacing:0.07em; }
div[data-testid="metric-container"] [data-testid="metric-value"] { color:#1B2D6B !important;
  font-family:'DM Mono',monospace !important; font-size:1.15rem !important;
  font-weight:700 !important; }
.stDateInput>div>div>input { background:#fafbff !important;
  border:1px solid #c9d6f0 !important; color:#1a2740 !important; border-radius:8px !important; }
.stTabs [data-baseweb="tab-list"] { gap:0.3rem; border-bottom:1px solid #dde3f0; }
.stTabs [data-baseweb="tab"] { font-size:0.78rem; font-weight:600; color:#6a80ad;
  padding:0.4rem 0.9rem; }
.stTabs [aria-selected="true"] { color:#1B2D6B !important;
  border-bottom:2px solid #E91E8C !important; }
.legend-row { font-size:0.58rem; color:#8095bd; margin-top:0.4rem; }
</style>
""", unsafe_allow_html=True)


# ═════════════════════════════════════════════════════════════
# GOOGLE SHEETS I/O
# ═════════════════════════════════════════════════════════════
@st.cache_resource
def get_sheet():
    scope = ["https://spreadsheets.google.com/feeds",
             "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"], scopes=scope)
    return gspread.authorize(creds).open(GOOGLE_SHEET_NAME)


@st.cache_data(ttl=300)
def load_data():
    sh = get_sheet()
    comp = pd.DataFrame(sh.worksheet(COMPETITOR_TAB).get_all_records())
    indi = pd.DataFrame(sh.worksheet(INDIGO_OPS_TAB).get_all_records())
    for df in (comp, indi):
        for c in ("Departure Date", "Scrape Date", "Date"):
            if c in df.columns:
                df[c] = pd.to_datetime(df[c], errors="coerce")
    for c in ("Load Factor", "Seats Sold", "Total Seats", "Days to Departure"):
        if c in indi.columns:
            indi[c] = pd.to_numeric(indi[c], errors="coerce")
    if "Fare (INR)" in comp.columns:
        comp["Fare (INR)"] = pd.to_numeric(comp["Fare (INR)"], errors="coerce")
    return comp, indi


def load_tab(tab, date_cols=()):
    try:
        rows = get_sheet().worksheet(tab).get_all_records()
        df = pd.DataFrame(rows) if rows else pd.DataFrame()
        for c in date_cols:
            if not df.empty and c in df.columns:
                df[c] = pd.to_datetime(df[c], errors="coerce")
        return df
    except Exception:
        return pd.DataFrame()


FEEDBACK_HDRS = ["Timestamp", "Analyst", "Route", "Flight No.", "Departure Time",
                 "Departure Date", "Cabin Class", "Passenger Type", "Trip Type",
                 "Days to Departure", "Load Factor", "Seats At Decision",
                 "Arithmetic Fare", "AI Decision", "AI Suggested Fare",
                 "AI Rationale", "Engine", "Competitor Snapshot",
                 "Manager Decision", "Final Fare Used",
                 "Strategic Direction", "Manager Notes"]

AILOG_HDRS = ["Log Date", "Analyst", "Route", "Flight No.", "Departure Time",
              "Departure Date", "Cabin Class", "Days to Departure", "Load Factor",
              "Seats At Decision", "Arithmetic Fare", "AI Decision",
              "AI Suggested Fare", "AI Rationale", "Engine",
              "Competitor Snapshot", "Strategic Direction",
              "Manager Decision", "Final Fare Used", "Manager Notes"]

STRATEGY_HDRS = ["Route", "Strategic Direction", "Set By", "Set On"]


def _append(tab, hdrs, row):
    """Append a row, growing the grid first if new columns or rows are needed."""
    sh = get_sheet()
    try:
        ws = sh.worksheet(tab)
    except Exception:
        ws = sh.add_worksheet(tab, rows=2000, cols=max(len(hdrs) + 8, 26))
        ws.append_row(hdrs, value_input_option="RAW")

    raw = ws.row_values(1)
    while raw and not str(raw[-1]).strip():
        raw.pop()
    existing = raw
    if not existing:
        ws.append_row(hdrs, value_input_option="RAW")
        existing = list(hdrs)

    missing = [h for h in hdrs if h not in existing]
    if missing:
        needed = len(existing) + len(missing)
        if needed > ws.col_count:
            ws.add_cols(needed - ws.col_count)
        for i, h in enumerate(missing, start=len(existing) + 1):
            ws.update_cell(1, i, h)
        existing = existing + missing

    try:
        if len(ws.col_values(1)) + 2 > ws.row_count:
            ws.add_rows(500)
    except Exception:
        pass

    ws.append_row([row.get(h, "") for h in existing], value_input_option="RAW")


def save_feedback(row): _append(FEEDBACK_TAB, FEEDBACK_HDRS, row)
def save_ai_log(row):   _append(AI_LOG_TAB, AILOG_HDRS, row)


def save_strategy(route, direction, analyst):
    """Standing strategy per route. Overwrites the row if the route exists."""
    sh = get_sheet()
    try:
        ws = sh.worksheet(STRATEGY_TAB)
    except Exception:
        ws = sh.add_worksheet(STRATEGY_TAB, rows=100, cols=10)
        ws.append_row(STRATEGY_HDRS, value_input_option="RAW")
    if not ws.row_values(1):
        ws.append_row(STRATEGY_HDRS, value_input_option="RAW")

    vals = [route, direction, analyst, datetime.now().strftime("%Y-%m-%d %H:%M")]
    routes = ws.col_values(1)
    if route in routes[1:]:
        r = routes.index(route) + 1
        for c, v in enumerate(vals, start=1):
            ws.update_cell(r, c, v)
    else:
        ws.append_row(vals, value_input_option="RAW")


# ═════════════════════════════════════════════════════════════
# SMALL HELPERS
# ═════════════════════════════════════════════════════════════
def dkey(v):
    """Normalise any date-ish value to YYYY-MM-DD so lookups match reliably."""
    try:
        t = pd.to_datetime(v, errors="coerce")
        return str(v)[:10] if pd.isna(t) else t.strftime("%Y-%m-%d")
    except Exception:
        return str(v)[:10]


def inr(v, dash="—"):
    try:
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return dash
        return f"₹{int(round(float(v))):,}"
    except Exception:
        return dash


def pct(v, dp=1):
    try:
        return f"{float(v)*100:.{dp}f}%"
    except Exception:
        return "—"


def deph(t):
    """Departure hour as a float, so 06:30 sorts between 06:00 and 07:00."""
    try:
        s = str(t).split(":")
        h = int(s[0])
        m = int(s[1]) if len(s) > 1 else 0
        return h + m / 60.0
    except Exception:
        return 10.0


def clock_gap(h1, h2):
    """Circular distance between two clock hours, so 23:00 and 01:00 are 2 apart."""
    d = abs(h1 - h2) % 24
    return min(d, 24 - d)


def lf_cls(lf):
    if lf <= 0.70: return "lf-g", "●"
    if lf <= 0.85: return "lf-a", "●"
    return "lf-r", "●"


def lf_kpi(lf):
    if lf <= 0.70: return "k-green"
    if lf <= 0.85: return "k-amber"
    return "k-red"


def gap_cls(v, base):
    try:
        v = float(v)
        if v < base * 0.97: return "f-cheap"
        if v > base * 1.03: return "f-exp"
        return "f-sim"
    except Exception:
        return ""


def style_chart(fig, height=260, legend=True):
    """Central chart styling. Avoids yaxis2/overlaying, barmode, titlefont and
    top-level font_color, all of which break on the Plotly version Streamlit
    Cloud installs."""
    fig.update_layout(
        plot_bgcolor="#ffffff", paper_bgcolor="#ffffff",
        font=dict(color="#2a4060", family="DM Sans", size=11),
        margin=dict(l=10, r=10, t=10, b=10),
        height=height, showlegend=legend,
        legend=dict(bgcolor="rgba(255,255,255,0.9)", bordercolor="#dde3f0",
                    borderwidth=1, font=dict(size=9), orientation="h", y=1.14, x=0),
        hovermode="closest",
    )
    fig.update_xaxes(gridcolor="#f0f3fa", linecolor="#dde3f0", zeroline=False)
    fig.update_yaxes(gridcolor="#f0f3fa", linecolor="#dde3f0", zeroline=False)
    return fig


# ═════════════════════════════════════════════════════════════
# PRICING ENGINE
# ═════════════════════════════════════════════════════════════
def _demand_signals(route, days_to_dep, load_factor, is_holiday,
                    dep_hour, passenger_type, trip_type, pace_delta=None):
    """All non-competition demand adjustments, as a list of
    (label, value) plus a running total."""
    if   days_to_dep <= 3:  adv, adv_l = 0.20, "Last minute (0-3d)"
    elif days_to_dep <= 7:  adv, adv_l = 0.15, "Near date (4-7d)"
    elif days_to_dep <= 14: adv, adv_l = 0.10, "Short advance (8-14d)"
    elif days_to_dep <= 30: adv, adv_l = 0.00, "Normal window (15-30d)"
    elif days_to_dep <= 60: adv, adv_l = -0.05, "Early booking (31-60d)"
    else:                   adv, adv_l = -0.10, "Very early (61d+)"

    if   load_factor <= 0.40: lfa, lf_l = -0.10, "Low demand (<40% full)"
    elif load_factor <= 0.70: lfa, lf_l =  0.00, "Normal demand (40-70%)"
    elif load_factor <= 0.85: lfa, lf_l =  0.15, "High demand (70-85%)"
    else:                     lfa, lf_l =  0.30, "Very high demand (>85%)"

    # Booking pace: ahead of or behind the historical curve for this route
    pace, pace_l = 0.0, "Pace unavailable"
    if pace_delta is not None and not pd.isna(pace_delta):
        if   pace_delta >=  0.10: pace, pace_l = 0.08, "Well ahead of pace"
        elif pace_delta >=  0.05: pace, pace_l = 0.04, "Ahead of pace"
        elif pace_delta <= -0.10: pace, pace_l = -0.08, "Well behind pace"
        elif pace_delta <= -0.05: pace, pace_l = -0.04, "Behind pace"
        else:                     pace, pace_l = 0.00, "On pace"

    h = dep_hour
    if   h < 6:   tim, tim_l = -0.05, "Red-eye"
    elif h < 9:   tim, tim_l =  0.12, "Morning peak"
    elif h < 12:  tim, tim_l =  0.18, "Business peak"
    elif h < 16:  tim, tim_l =  0.00, "Afternoon"
    elif h < 21:  tim, tim_l =  0.15, "Evening peak"
    else:         tim, tim_l = -0.03, "Late night"

    hol   = 0.15 if is_holiday else 0.0
    hol_l = "Festival / holiday" if is_holiday else "No holiday"

    pax  = PASSENGER_ADJ.get(passenger_type, 0.0)
    trip = -0.05 if trip_type == "Round Trip" else 0.0

    items = [
        ("Advance booking", adv,  adv_l),
        ("Load factor",     lfa,  lf_l),
        ("Booking pace",    pace, pace_l),
        ("Time slot",       tim,  tim_l),
        ("Holiday",         hol,  hol_l),
        ("Passenger type",  pax,  passenger_type),
        ("Trip type",       trip, trip_type),
    ]
    return items, sum(v for _, v, _ in items)


def calc_fare(route, cabin, days_to_dep, load_factor, competitor_fare,
              is_holiday, dep_hour, passenger_type="Adult", trip_type="One Way",
              pace_delta=None):
    """Two-pass pricing.

    Pass 1 builds a provisional fare from the product tier and all demand
    signals. Pass 2 compares THAT fare against the matched competitor and
    applies a competitive correction. Comparing the untouched list price
    against a competitor, as the first version did, answered the wrong
    question on any flight where demand had already moved the price.
    """
    route_base = BASE_FARES.get(route, 5000)
    tier_mult  = CABIN_MULT.get(cabin, 1.0)
    cabin_base = route_base * tier_mult          # product tier, before demand

    items, demand_raw = _demand_signals(route, days_to_dep, load_factor,
                                        is_holiday, dep_hour, passenger_type,
                                        trip_type, pace_delta)
    demand_capped = max(DEMAND_CAP_LO, min(DEMAND_CAP_HI, demand_raw))
    provisional   = cabin_base * (1 + demand_capped)

    comp_adj, comp_l = 0.0, "No competitor match"
    if competitor_fare and competitor_fare > 0:
        ratio = provisional / competitor_fare
        if   ratio > 1.15: comp_adj, comp_l = -0.07, f"We are >15% above {inr(competitor_fare)}"
        elif ratio > 1.08: comp_adj, comp_l = -0.04, f"We are 8-15% above {inr(competitor_fare)}"
        elif ratio < 0.85: comp_adj, comp_l =  0.07, f"We are >15% below {inr(competitor_fare)}"
        elif ratio < 0.92: comp_adj, comp_l =  0.04, f"We are 8-15% below {inr(competitor_fare)}"
        else:              comp_adj, comp_l =  0.00, f"Within 8% of {inr(competitor_fare)}"

    # Never discount a nearly full aircraft, whatever the competitor is doing
    if load_factor > 0.85 and comp_adj < 0:
        comp_adj, comp_l = 0.0, "Discount blocked: load factor above 85%"

    total_demand = max(DEMAND_CAP_LO, min(DEMAND_CAP_HI, demand_capped + comp_adj))
    final = int(round(cabin_base * (1 + total_demand)))

    breakdown = {
        "route_base":   route_base,
        "tier_mult":    tier_mult,
        "cabin_base":   cabin_base,
        "items":        items,
        "demand_raw":   demand_raw,
        "comp_adj":     comp_adj,
        "comp_label":   comp_l,
        "total_demand": total_demand,
        "capped":       abs(demand_capped + comp_adj - total_demand) > 1e-9,
        "final":        final,
    }
    return final, breakdown


def seat_cost(route, cabin):
    """Variable cost per seat, scaled by the floor space the seat occupies."""
    return COST_PER_SEAT.get(route, 3000) * CABIN_COST_MULT.get(cabin, 1.0)


def match_competitor(comp_rows, target_hour):
    """Pick the competitor flight closest in departure time.

    The first version took .iloc[0], which after the groupby was always the
    lowest flight number, so an 18:00 evening departure was routinely compared
    against a 09:00 morning flight. Time-slot matching is the whole point of
    tracking individual flight numbers.
    """
    best = None
    for _, r in comp_rows.iterrows():
        fare = r.get("Fare (INR)")
        if pd.isna(fare):
            continue
        gap = clock_gap(deph(r.get("Departure Time", "10:00")), target_hour)
        if best is None or gap < best["gap"]:
            best = {"gap": gap, "airline": str(r.get("Airline", "")),
                    "flight": str(r.get("Flight No.", "")),
                    "time": str(r.get("Departure Time", "")),
                    "fare": int(fare)}
    return best


@st.cache_data(ttl=600)
def booking_pace_curve(indigo_df):
    """Empirical booking curve: average load factor by days-to-departure,
    per route and cabin, learned from the history already in the sheet.

    A load factor means nothing without a target. 60% at 45 days out could be
    strong or weak; only a reference curve tells you which.
    """
    if indigo_df.empty:
        return {}
    d = indigo_df.dropna(subset=["Load Factor", "Days to Departure"]).copy()
    if d.empty:
        return {}
    d["dbin"] = pd.to_numeric(d["Days to Departure"], errors="coerce").round()
    g = (d.groupby(["Route", "Cabin Class", "dbin"])["Load Factor"]
           .mean().reset_index())
    curve = {}
    for _, r in g.iterrows():
        curve[(r["Route"], r["Cabin Class"], int(r["dbin"]))] = float(r["Load Factor"])
    return curve


def pace_delta_for(curve, route, cabin, days_out, actual_lf):
    """Actual load factor minus the historical average at the same point."""
    if not curve:
        return None
    target = curve.get((route, cabin, int(days_out)))
    if target is None:
        near = [v for (r, c, d), v in curve.items()
                if r == route and c == cabin and abs(d - days_out) <= 3]
        if not near:
            return None
        target = sum(near) / len(near)
    return actual_lf - target
# ═════════════════════════════════════════════════════════════
# LLM LAYER  (Groq / Gemini / rules-based fallback)
# ═════════════════════════════════════════════════════════════
def build_prompt(route, flight_no, dep_time, cabin, dep_date, days_to_dep,
                 load_factor, pace_delta, arithmetic_fare, bd, comp_match,
                 comp_all, strategy, history, pax_type, trip_type):
    comp_lines = "\n".join(
        f"  {a} ({fn} {ft}): Rs {fare:,}" for a, fn, ft, fare in comp_all
    ) or "  No competitor data available"

    match_line = ("None found"
                  if not comp_match else
                  f"{comp_match['airline']} {comp_match['flight']} at "
                  f"{comp_match['time']} — Rs {comp_match['fare']:,} "
                  f"({comp_match['gap']:.1f}h from our departure)")

    pace_line = "no historical curve available"
    if pace_delta is not None and not pd.isna(pace_delta):
        pace_line = (f"{abs(pace_delta)*100:.0f} points "
                     f"{'AHEAD of' if pace_delta >= 0 else 'BEHIND'} "
                     "the typical booking curve for this route at this point")

    strat_text = ""
    if strategy and "None" not in strategy:
        strat_text = (f"\nSTANDING STRATEGIC DIRECTION FOR THIS ROUTE: {strategy}\n"
                      "This must strongly influence your recommendation.\n")

    hist_text = ""
    if history:
        hist_text = "\nRecent decisions on this route (your feedback loop):\n"
        for h in history[-3:]:
            rat = str(h.get("AI Rationale", ""))[:120]
            hist_text += (f"  - {h.get('Departure Date','')}: AI said "
                          f"Rs {h.get('AI Suggested Fare','')} "
                          f"(\"{rat}\"), manager {h.get('Manager Decision','')}, "
                          f"final Rs {h.get('Final Fare Used','')}\n")

    return f"""You are a senior revenue management analyst at IndiGo Airlines.

Flight: {flight_no} | Route: {route} | Departs {dep_time} on {dep_date}
Cabin: {cabin} | Passenger: {pax_type} | Trip: {trip_type}
Days to departure: {days_to_dep}
Load factor: {round(load_factor*100,1)}% — {pace_line}

Our pricing engine's calculation:
  Cabin base (route base x {bd['tier_mult']:.2f} product tier): Rs {int(bd['cabin_base']):,}
  Net demand adjustment applied: {bd['total_demand']*100:+.1f}%
  Resulting fare: Rs {arithmetic_fare:,}

Nearest competitor by departure time: {match_line}

All competing flights on this route and date:
{comp_lines}
{strat_text}{hist_text}
Rules you must follow:
- If load factor is above 85%, never recommend a discount.
- Judge competitiveness against the time-matched competitor above, not the
  cheapest flight of the day.
- A flight well behind its booking curve deserves sharper pricing; one well
  ahead can hold or push price.
- Give one precise fare in whole rupees, never a range.

Reply in exactly this format and nothing else:
Decision: Approve OR Override
Suggested Fare: Rs [number]
Rationale: [2-3 plain English sentences naming the decisive factors]"""


def parse_reply(text, fallback_fare):
    decision, fare, rationale = "Approve", fallback_fare, ""
    for line in text.split("\n"):
        ls = line.strip().lstrip("*# ").strip()
        low = ls.lower()
        if low.startswith("decision:"):
            decision = ls.split(":", 1)[1].strip()
        elif low.startswith("suggested fare:"):
            digits = "".join(c for c in ls.split(":", 1)[1] if c.isdigit())
            if digits:
                fare = int(digits)
        elif low.startswith("rationale:"):
            rationale = ls.split(":", 1)[1].strip()
    if not rationale:
        rationale = text.strip()[:320]
    return decision, fare, rationale


def call_gemini(prompt, fallback_fare):
    if not GEMINI_API_KEY:
        return None, None, None, "No GEMINI_API_KEY in Secrets."
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}")
    try:
        r = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]},
                          timeout=40)
    except Exception as e:
        return None, None, None, f"Network error reaching Gemini: {e}"
    if r.status_code != 200:
        return None, None, None, f"Gemini HTTP {r.status_code}: {r.text[:300]}"
    try:
        text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
    except Exception:
        return None, None, None, f"Unexpected Gemini reply: {r.text[:250]}"
    d, f, rat = parse_reply(text, fallback_fare)
    return d, f, rat, None


def call_groq(prompt, fallback_fare):
    if not GROQ_API_KEY:
        return None, None, None, "No GROQ_API_KEY in Secrets."
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}",
                     "Content-Type": "application/json"},
            json={"model": GROQ_MODEL,
                  "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0.3, "max_tokens": 400},
            timeout=40)
    except Exception as e:
        return None, None, None, f"Network error reaching Groq: {e}"
    if r.status_code != 200:
        return None, None, None, f"Groq HTTP {r.status_code}: {r.text[:300]}"
    try:
        text = r.json()["choices"][0]["message"]["content"]
    except Exception:
        return None, None, None, f"Unexpected Groq reply: {r.text[:250]}"
    d, f, rat = parse_reply(text, fallback_fare)
    return d, f, rat, None


def fallback_rationale(bd, fare, comp_match, load_factor):
    """Plain-English explanation built purely from the pricing rules.
    Used only when no AI engine is reachable, and always labelled as such."""
    moved = sorted([i for i in bd["items"] if abs(i[1]) > 1e-9],
                   key=lambda x: -abs(x[1]))
    ups   = [f"{lbl} (+{v*100:.0f}%)" for _, v, lbl in moved if v > 0][:3]
    downs = [f"{lbl} ({v*100:.0f}%)" for _, v, lbl in moved if v < 0][:2]

    parts = [f"Rules-based pricing: {inr(fare)} = {inr(bd['cabin_base'])} cabin "
             f"base {bd['total_demand']*100:+.0f}% net demand adjustment."]
    if ups:
        parts.append("Upward pressure from " + ", ".join(ups) + ".")
    if downs:
        parts.append("Offset by " + ", ".join(downs) + ".")
    if bd["comp_adj"]:
        parts.append(f"Competitive correction {bd['comp_adj']*100:+.0f}%: "
                     f"{bd['comp_label']}.")
    elif comp_match:
        parts.append(f"Time-matched competitor {comp_match['airline']} at "
                     f"{inr(comp_match['fare'])}; no correction needed.")
    if load_factor > 0.85:
        parts.append("Load factor above 85%, so discounting is blocked.")
    if bd.get("capped"):
        parts.append("Net adjustment hit the cap.")
    return " ".join(parts)


def call_llm(prompt_args, bd, arithmetic_fare, comp_match, load_factor):
    """Tries the configured provider, then the other, then the rules fallback.
    Returns (decision, fare, rationale, engine_name, diagnostics)."""
    prompt = build_prompt(**prompt_args)
    order  = ["groq", "gemini"] if LLM_PROVIDER == "groq" else ["gemini", "groq"]
    names  = {"groq": "Groq", "gemini": "Gemini"}
    errors = []
    for prov in order:
        fn = call_groq if prov == "groq" else call_gemini
        d, f, rat, err = fn(prompt, arithmetic_fare)
        if err is None:
            return d, f, rat, names[prov], ("; ".join(errors) if errors else None)
        errors.append(f"{names[prov]} — {err}")
    rat = fallback_rationale(bd, arithmetic_fare, comp_match, load_factor)
    return "Approve", arithmetic_fare, rat, "Rules-based fallback", "; ".join(errors)

# ═════════════════════════════════════════════════════════════
# DISPLAY HELPERS — dates always dd-mm-yyyy, no jargon anywhere
# ═════════════════════════════════════════════════════════════
def dfmt(v, style="short"):
    """Every date shown anywhere goes through here. dd-mm-yyyy, always."""
    try:
        t = pd.to_datetime(v, errors="coerce")
        if pd.isna(t):
            return str(v)[:10]
        if style == "long":  return t.strftime("%A, %d-%m-%Y")
        if style == "day":   return t.strftime("%d-%m")
        if style == "stamp": return t.strftime("%d-%m-%Y %H:%M")
        return t.strftime("%d-%m-%Y")
    except Exception:
        return str(v)[:10]


def fno_disp(v, dep_time=""):
    """Flight number for display. Some rows carry a blank, numeric or
    scientific-notation flight number; fall back to the departure time."""
    s = str(v).strip()
    if s.endswith(".0"):
        s = s[:-2]
    if s in ("", "nan", "None", "0") or s.replace(".", "").replace("e+", "").isdigit():
        return f"dep {dep_time}" if dep_time else "—"
    return s


def cabin_short(c):
    return {"Premium Economy": "Prem Econ"}.get(str(c), str(c))


def fill_speed_words(delta):
    """Plain English for how fast a flight is filling versus its own norm."""
    if delta is None or (isinstance(delta, float) and pd.isna(delta)):
        return "No history yet", "sp-none"
    if delta >= 0.10:  return "Much faster than usual", "sp-fast"
    if delta >= 0.05:  return "Faster than usual", "sp-fast"
    if delta <= -0.10: return "Much slower than usual", "sp-slow"
    if delta <= -0.05: return "Slower than usual", "sp-slow"
    return "About normal", "sp-norm"


def sku_key(flight_raw, cabin, dep_date):
    return f"{str(flight_raw)}|{str(cabin)}|{dkey(dep_date)}"


def advice_line(t):
    """One sentence saying what this row means and what to do about it."""
    gap, mv, dout, seats, spd = (t["gap"], t["move_pc"], t["dout"],
                                 t["remaining"], t["pace"])
    if gap is None:
        return "No competitor flight at a similar time — price on demand alone."
    if   gap >  0.15: head = f"We are {gap*100:.0f}% dearer than the closest rival flight"
    elif gap >  0.08: head = f"We are {gap*100:.0f}% above the closest rival flight"
    elif gap < -0.15: head = f"We are {abs(gap)*100:.0f}% cheaper than the closest rival flight"
    elif gap < -0.08: head = f"We are {abs(gap)*100:.0f}% below the closest rival flight"
    else:             head = "Priced in line with the closest rival flight"
    parts = [head]
    if mv is not None and abs(mv) > 0.05:
        parts.append(f"who changed price {abs(mv)*100:.0f}% since yesterday")
    if spd is not None and spd <= -0.05:
        parts.append(f"and this flight is selling slower than usual — {seats} "
                     f"seats still unsold with {dout} days left")
    elif spd is not None and spd >= 0.05:
        parts.append(f"and it is selling faster than usual — only {seats} seats left")
    else:
        parts.append(f"with {seats} seats unsold and {dout} days left")
    s = ", ".join(parts) + "."
    if t["flag"] == "red":
        if   gap >  0.08: s += " Cut the fare to win back bookings."
        elif gap < -0.08: s += " Room to raise the fare and still lead on price."
        else:             s += " Review this one today."
    elif t["flag"] == "amber":
        s += " Keep an eye on it."
    return s


def commit_decision(p, kind, final_fare, notes=""):
    """Single place where a manager decision is written to both sheets."""
    base = {"Analyst": p.get("analyst") or "Unknown", "Route": p["route"],
            "Flight No.": p["flight_raw"], "Departure Time": p["time"],
            "Departure Date": p["date"], "Cabin Class": p["cabin"],
            "Days to Departure": p["days"],
            "Load Factor": round(p["lf"] * 100, 1),
            "Seats At Decision": p["sold"], "Arithmetic Fare": p["arith"],
            "AI Decision": p["dec"], "AI Suggested Fare": p["fare"],
            "AI Rationale": p["rat"], "Engine": p.get("engine", ""),
            "Competitor Snapshot": p.get("snapshot", ""),
            "Strategic Direction": p.get("strategy", ""),
            "Manager Decision": kind, "Final Fare Used": final_fare}
    save_feedback({**base,
                   "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
                   "Passenger Type": p.get("pax", "Adult"),
                   "Trip Type": p.get("trip", "One Way"),
                   "Manager Notes": notes})
    save_ai_log({**base, "Log Date": datetime.now().strftime("%Y-%m-%d %H:%M"),
                 "Manager Notes": notes})


st.markdown("""
<style>
.stTabs [data-baseweb="tab-list"] { gap:0.35rem; border-bottom:2px solid #dde3f0; }
.stTabs [data-baseweb="tab"] { height:2.6rem; padding:0 1.15rem; background:#eef2fa;
  border-radius:9px 9px 0 0; font-size:0.82rem; font-weight:600; color:#5a6f9c; }
.stTabs [aria-selected="true"] { background:#1B2D6B !important; color:#fff !important; }

table.wrap { width:100%; border-collapse:separate; border-spacing:0; font-size:0.75rem;
  border:1px solid #dde3f0; border-radius:10px; overflow:hidden; background:#fff; }
table.wrap thead tr { background:#f2f5fc; }
table.wrap th { padding:0.55rem 0.6rem; font-size:0.57rem; font-weight:700;
  letter-spacing:0.07em; text-transform:uppercase; color:#1B2D6B;
  border-bottom:2px solid #dde3f0; text-align:left; }
table.wrap td { padding:0.55rem 0.6rem; border-bottom:1px solid #f2f5fc;
  color:#2a4060; font-size:0.74rem; vertical-align:top; white-space:normal; }
table.wrap tr:last-child td { border-bottom:none; }
table.wrap tbody tr:hover td { background:#fafbff; }
td.num { font-family:'DM Mono',monospace; white-space:nowrap; }
td.advice { color:#3a5080; font-size:0.72rem; line-height:1.5; }
.sp-fast { color:#16A34A; font-weight:600; }
.sp-slow { color:#DC2626; font-weight:600; }
.sp-norm { color:#8095bd; }
.sp-none { color:#c3cde3; font-style:italic; }
.insight { background:#f4f7ff; border-left:4px solid #E91E8C;
  border-radius:0 9px 9px 0; padding:0.8rem 1.05rem; font-size:0.86rem;
  color:#26365e; line-height:1.7; margin-bottom:0.9rem; }
.insight b { color:#1B2D6B; }
.insight .big { font-size:1.02rem; font-weight:700; color:#E91E8C; }
.tab-intro { font-size:0.74rem; color:#7c8db5; margin:-0.2rem 0 0.9rem 0; }
.kpi-strip.six { grid-template-columns:repeat(6,minmax(0,1fr)); }
.dl-up { color:#DC2626; font-weight:700; }
.dl-dn { color:#16A34A; font-weight:700; }
.dl-fl { color:#8095bd; }
tr.date-sep td { background:#eaf0fb !important; color:#1B2D6B !important;
  font-weight:700 !important; font-size:0.72rem !important;
  padding:0.35rem 0.6rem !important; border-top:2px solid #c9d6f0 !important; }
.scope-note { background:#fff8e6; border:1px solid #f0d9a0; border-radius:8px;
  padding:0.55rem 0.85rem; font-size:0.73rem; color:#7a5c14; margin-bottom:0.8rem; }
</style>
""", unsafe_allow_html=True)


# ═════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════
def main():
    try:
        comp_df, indigo_df = load_data()
    except Exception as e:
        st.error(f"Could not connect to Google Sheets: {e}")
        st.info("Check GOOGLE_SHEET_NAME and [gcp_service_account] in Streamlit "
                "Secrets, and that the sheet is shared with the service account.")
        return

    feedback_df = load_tab(FEEDBACK_TAB, ("Timestamp",))
    ai_log_df   = load_tab(AI_LOG_TAB, ("Log Date",))
    strategy_df = load_tab(STRATEGY_TAB)

    today = pd.Timestamp.today().normalize()
    dmin, dmax = today - timedelta(days=30), today + timedelta(days=90)
    dcol = "Date" if "Date" in indigo_df.columns else "Scrape Date"
    pace_curve = booking_pace_curve(indigo_df)

    standing = {}
    if not strategy_df.empty and "Route" in strategy_df.columns:
        for _, s in strategy_df.iterrows():
            standing[str(s.get("Route", ""))] = str(s.get("Strategic Direction", ""))

    if "jump_route" in st.session_state:
        st.session_state["route_sel"] = st.session_state.pop("jump_route")
        st.session_state["cabin_sel"] = st.session_state.pop("jump_cabin")
        st.session_state.pop("lfpick", None)

    # ── SIDEBAR ──────────────────────────────────────────────
    with st.sidebar:
        st.markdown('<div class="sb-brand">'
                    '<span style="color:#E91E8C;font-weight:800">6E</span>'
                    '&nbsp; IndiGo · Pricing Intelligence</div>', unsafe_allow_html=True)

        analyst = st.text_input("Analyst name",
                                value=st.session_state.get("analyst", ""),
                                placeholder="Who is making decisions?")
        st.session_state["analyst"] = analyst

        routes    = sorted(indigo_df["Route"].dropna().unique().tolist())
        sel_route = st.selectbox("Route", routes, key="route_sel")
        cabins    = sorted(indigo_df["Cabin Class"].dropna().unique().tolist())
        sel_cabin = st.selectbox("Cabin Class", cabins, key="cabin_sel")
        trip_type = st.radio("Trip Type", ["One Way", "Round Trip"], index=0)
        pax_type  = st.selectbox("Passenger Type",
                                 ["Adult", "Corporate", "Student",
                                  "Senior Citizen", "Child"])

        times = []
        if "Departure Time" in indigo_df.columns:
            times = sorted(indigo_df[indigo_df["Route"] == sel_route]["Departure Time"]
                           .dropna().astype(str).unique().tolist())
        sel_time = st.selectbox("Flight Time", ["All Times"] + times)

        st.markdown('<p style="color:#1B2D6B;font-size:0.62rem;font-weight:700;'
                    'text-transform:uppercase;letter-spacing:0.09em;'
                    'margin:0.6rem 0 0.15rem">Departure Date</p>',
                    unsafe_allow_html=True)
        d1 = st.date_input("Departure", value=today.date(),
                           min_value=dmin.date(), max_value=dmax.date(),
                           label_visibility="collapsed", key="d1",
                           format="DD-MM-YYYY")
        if trip_type == "Round Trip":
            st.markdown('<p style="color:#1B2D6B;font-size:0.62rem;font-weight:700;'
                        'text-transform:uppercase;letter-spacing:0.09em;'
                        'margin:0.6rem 0 0.15rem">Return Date</p>',
                        unsafe_allow_html=True)
            d2 = st.date_input("Return", value=(today + timedelta(days=7)).date(),
                               min_value=dmin.date(), max_value=dmax.date(),
                               label_visibility="collapsed", key="d2",
                               format="DD-MM-YYYY")
            sel_dates = sorted({pd.Timestamp(d1), pd.Timestamp(d2)})
        else:
            sel_dates = [pd.Timestamp(d1)]

        st.markdown("---")
        if st.button("✈  Check Price"):
            st.cache_data.clear()
            st.rerun()
        st.caption(f"Data refreshed {datetime.now().strftime('%H:%M:%S')}")

        active  = "Groq" if LLM_PROVIDER == "groq" else "Gemini"
        has_key = bool(GROQ_API_KEY) if LLM_PROVIDER == "groq" else bool(GEMINI_API_KEY)
        if has_key:
            st.caption(f"AI engine: {active}")
        else:
            st.warning(f"{active} key missing in Secrets. "
                       "Pricing will use the arithmetic rules only.")

    # ── LATEST STATE PER SKU, SELECTED DATES ─────────────────
    snap_all = indigo_df[indigo_df["Departure Date"].isin(sel_dates)].copy()
    if not snap_all.empty and dcol in snap_all.columns:
        snap_all = (snap_all.sort_values(dcol)
                    .groupby(["Route", "Flight No.", "Cabin Class", "Departure Date"],
                             as_index=False).last())

    gk = ["Airline", "Flight No.", "Route", "Cabin Class", "Departure Date"]

    # Latest competitor fare for EVERY future date, used by the matrix
    comp_future = comp_df[(comp_df["Departure Date"] >= today) &
                          (comp_df["Departure Date"] <= today + timedelta(days=31))].copy()
    comp_latest_all = pd.DataFrame()
    if not comp_future.empty and "Scrape Date" in comp_future.columns:
        comp_latest_all = (comp_future.sort_values("Scrape Date")
                           .groupby(gk, as_index=False).last())

    comp_all = comp_df[comp_df["Departure Date"].isin(sel_dates)].copy()
    comp_latest, comp_prev = pd.DataFrame(), pd.DataFrame()
    if not comp_all.empty and "Scrape Date" in comp_all.columns:
        comp_latest = (comp_all.sort_values("Scrape Date")
                       .groupby(gk, as_index=False).last())
        pool = comp_all.merge(
            comp_latest[gk + ["Scrape Date"]].rename(
                columns={"Scrape Date": "_latest"}), on=gk, how="left")
        pool = pool[pool["Scrape Date"] < pool["_latest"]]
        if not pool.empty:
            comp_prev = (pool.sort_values("Scrape Date")
                         .groupby(gk, as_index=False).last())

    def overnight_move(airline, flight, route, cabin, dep_date):
        if comp_latest.empty or comp_prev.empty:
            return None
        def pick(df):
            return df[(df["Airline"] == airline) &
                      (df["Flight No."].astype(str) == str(flight)) &
                      (df["Route"] == route) & (df["Cabin Class"] == cabin) &
                      (df["Departure Date"] == dep_date)]
        cur, prv = pick(comp_latest), pick(comp_prev)
        if cur.empty or prv.empty:
            return None
        try:
            return float(cur.iloc[0]["Fare (INR)"]) - float(prv.iloc[0]["Fare (INR)"])
        except Exception:
            return None

    # ── SKUs already decided today drop out of the action list ──
    decided_today = set()
    if not feedback_df.empty and "Timestamp" in feedback_df.columns:
        fbt = feedback_df.copy()
        fbt["_ts"] = pd.to_datetime(fbt["Timestamp"], errors="coerce")
        fbt = fbt[(fbt["_ts"] >= today) &
                  (fbt.get("Manager Decision", pd.Series(dtype=str))
                   .isin(["Accepted", "Overridden"]))]
        for _, x in fbt.iterrows():
            decided_today.add(sku_key(x.get("Flight No.", ""),
                                      x.get("Cabin Class", ""),
                                      x.get("Departure Date", "")))

    # ── TRIAGE ROWS ──────────────────────────────────────────
    triage_rows = []
    for _, r in snap_all.iterrows():
        rt, cb, dd = str(r["Route"]), str(r["Cabin Class"]), r["Departure Date"]
        ftm  = str(r.get("Departure Time", ""))
        raw  = str(r.get("Flight No.", ""))
        fno  = fno_disp(raw, ftm)
        lf   = float(r.get("Load Factor", 0) or 0)
        tot  = int(r.get("Total Seats", TOTAL_SEATS_MAP.get(rt, 180)) or 180)
        sold = int(r.get("Seats Sold", 0) or 0)
        if sold <= 0 and lf > 0:
            sold = int(round(lf * tot))
        remaining = max(tot - sold, 0)
        dout = int(r.get("Days to Departure", 30) or 30)
        hol  = str(r.get("Holiday / Festival", "No")) == "Yes"

        cm = comp_latest[(comp_latest["Route"] == rt) &
                         (comp_latest["Cabin Class"] == cb) &
                         (comp_latest["Departure Date"] == dd)] \
             if not comp_latest.empty else pd.DataFrame()
        match = match_competitor(cm, deph(ftm))
        pdlt = pace_delta_for(pace_curve, rt, cb, dout, lf)
        fare, bdx = calc_fare(rt, cb, dout, lf, match["fare"] if match else 0,
                              hol, deph(ftm), pax_type, trip_type, pace_delta=pdlt)

        gap  = (fare - match["fare"]) / match["fare"] if (match and match["fare"]) else None
        risk = abs(fare - match["fare"]) * remaining if match else 0
        move = overnight_move(match["airline"], match["flight"], rt, cb, dd) \
               if match else None
        move_pc = (move / match["fare"]) if (move is not None and match
                                             and match["fare"]) else None

        flag = "green"
        if gap is not None:
            if (abs(gap) > 0.15 and dout <= 7) or \
               (move_pc is not None and abs(move_pc) > 0.10):
                flag = "red"
            elif abs(gap) > 0.08 or (move_pc is not None and abs(move_pc) > 0.05):
                flag = "amber"

        key = sku_key(raw, cb, dd)
        triage_rows.append({
            "key": key, "route": rt, "raw": raw, "flight": fno, "time": ftm,
            "cabin": cb, "dep": dd, "dout": dout, "lf": lf, "sold": sold,
            "total": tot, "remaining": remaining, "fare": fare, "bd": bdx,
            "comp": match, "comp_rows": cm, "gap": gap, "move": move,
            "move_pc": move_pc, "risk": risk, "flag": flag, "pace": pdlt,
            "holiday": hol, "settled": key in decided_today})
    triage_rows.sort(key=lambda x: -x["risk"])

    ctx = dict(comp_df=comp_df, indigo_df=indigo_df, feedback_df=feedback_df,
               ai_log_df=ai_log_df, snap_all=snap_all, comp_latest=comp_latest,
               comp_latest_all=comp_latest_all, pace_curve=pace_curve,
               standing=standing, today=today, dmin=dmin, dmax=dmax, dcol=dcol,
               sel_route=sel_route, sel_cabin=sel_cabin, sel_time=sel_time,
               sel_dates=sel_dates, pax_type=pax_type, trip_type=trip_type,
               analyst=analyst, triage_rows=triage_rows,
               decided_today=decided_today, routes=routes, cabins=cabins)

    # ── HEADER ───────────────────────────────────────────────
    date_disp = " & ".join(dfmt(d) for d in sel_dates)
    n_today = 0
    if not ai_log_df.empty and "Log Date" in ai_log_df.columns:
        n_today = int((pd.to_datetime(ai_log_df["Log Date"], errors="coerce")
                       >= today).sum())
    sh, sm = divmod(n_today * MANUAL_MINUTES_PER_SKU, 60)
    n_open = sum(1 for t in triage_rows if t["flag"] != "green" and not t["settled"])

    st.markdown(f"""
    <div class="pid-hdr">
      <div>
        <div class="pid-title">
          <span style="color:#ffd9ee;font-weight:800">6E</span>
          &nbsp;IndiGo Pricing Intelligence</div>
        <div class="pid-sub">Competitor fare monitor · AI pricing adviser · ISB ALP 2026</div>
      </div>
      <div class="pid-ctx">
        <div><div class="pid-ctx-val">{date_disp}</div>
             <div class="pid-ctx-lbl">Flights departing</div></div>
        <div class="pid-div"></div>
        <div><div class="pid-ctx-val">{len(snap_all)}</div>
             <div class="pid-ctx-lbl">Fares tracked</div></div>
        <div class="pid-div"></div>
        <div><div class="pid-ctx-val" style="color:{'#ffd9ee' if n_open else '#8affc0'}">{n_open}</div>
             <div class="pid-ctx-lbl">Still need attention</div></div>
        <div class="pid-div"></div>
        <div><div class="pid-ctx-val">{sh}h {sm}m</div>
             <div class="pid-ctx-lbl">Analyst time saved</div></div>
        <div class="live-pill"><div class="live-dot"></div>LIVE</div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    if snap_all.empty:
        st.info("No flights found for the selected departure date. "
                "Pick a date covered by your data.")
        return

    t1, t2, t3, t4 = st.tabs([
        "📊  Flight dashboard",
        f"🚩  What needs attention ({n_open})",
        "🕘  Decision history",
        "💰  Business case"])
    with t1: render_dashboard(ctx)
    with t2: render_action_list(ctx)
    with t3: render_history(ctx)
    with t4: render_business_case(ctx)


# ═════════════════════════════════════════════════════════════
# TAB 1 — FLIGHT DASHBOARD
# ═════════════════════════════════════════════════════════════
def render_dashboard(C):
    indigo_df, comp_df = C["indigo_df"], C["comp_df"]
    feedback_df, ai_log_df = C["feedback_df"], C["ai_log_df"]
    sel_route, sel_cabin, sel_time = C["sel_route"], C["sel_cabin"], C["sel_time"]
    sel_dates, pax_type, trip_type = C["sel_dates"], C["pax_type"], C["trip_type"]
    pace_curve, dcol, today = C["pace_curve"], C["dcol"], C["today"]
    analyst, standing = C["analyst"], C["standing"]

    st.markdown(f'<div class="tab-intro">Everything about <b>{sel_route}</b> · '
                f'{sel_cabin}. Change route or cabin in the sidebar.</div>',
                unsafe_allow_html=True)

    indigo_f = C["snap_all"][(C["snap_all"]["Route"] == sel_route) &
                             (C["snap_all"]["Cabin Class"] == sel_cabin)].copy()
    if sel_time != "All Times" and "Departure Time" in indigo_f.columns:
        indigo_f = indigo_f[indigo_f["Departure Time"].astype(str) == sel_time]
    comp_f = C["comp_latest"][(C["comp_latest"]["Route"] == sel_route) &
                              (C["comp_latest"]["Cabin Class"] == sel_cabin)].copy() \
             if not C["comp_latest"].empty else pd.DataFrame()

    if indigo_f.empty:
        st.info("No IndiGo flights for this route, cabin, time and date. "
                "Widen the filters in the sidebar.")
        return

    fopts = (indigo_f[["Flight No.", "Departure Time",
                       "Departure Date", "Days to Departure"]]
             .drop_duplicates().sort_values(["Departure Date", "Departure Time"]))
    labels = [f'{fno_disp(r["Flight No."], r["Departure Time"])}  ·  '
              f'{r["Departure Time"]}  ·  {dfmt(r["Departure Date"])}  '
              f'({int(r["Days to Departure"])} days to departure)'
              for _, r in fopts.iterrows()]
    sel_label = st.selectbox("Which flight?", labels)
    fr = fopts.iloc[labels.index(sel_label)]

    f_raw_no = str(fr["Flight No."])
    f_no     = fno_disp(f_raw_no, str(fr["Departure Time"]))
    f_time   = str(fr["Departure Time"])
    f_date   = fr["Departure Date"]
    f_days   = int(fr["Days to Departure"])

    frow    = indigo_f[(indigo_f["Flight No."].astype(str) == f_raw_no) &
                       (indigo_f["Departure Date"] == f_date)]
    f_lf    = float(frow["Load Factor"].iloc[0]) if not frow.empty else 0.6
    f_total = int(frow["Total Seats"].iloc[0]) if not frow.empty else 180
    _raw    = frow["Seats Sold"].iloc[0] if not frow.empty else 0
    f_sold  = 0 if pd.isna(_raw) else int(_raw)
    if f_sold <= 0 and f_lf > 0:
        f_sold = int(round(f_lf * f_total))
    f_hol  = str(frow["Holiday / Festival"].iloc[0]) if not frow.empty else "No"
    f_slot = str(frow["Time Slot"].iloc[0]) if not frow.empty else ""

    comp_same = comp_f[comp_f["Departure Date"] == f_date] \
                if not comp_f.empty else pd.DataFrame()
    f_match = match_competitor(comp_same, deph(f_time))
    comp_list = [(str(c["Airline"]), str(c["Flight No."]), str(c["Departure Time"]),
                  int(c["Fare (INR)"])) for _, c in comp_same.iterrows()
                 if pd.notna(c.get("Fare (INR)"))]

    f_pace = pace_delta_for(pace_curve, sel_route, sel_cabin, f_days, f_lf)
    arith, bd = calc_fare(sel_route, sel_cabin, f_days, f_lf,
                          f_match["fare"] if f_match else 0,
                          f_hol == "Yes", deph(f_time),
                          pax_type, trip_type, pace_delta=f_pace)

    spd_txt, spd_cls = fill_speed_words(f_pace)
    if f_match:
        gap = (arith - f_match["fare"]) / f_match["fare"]
        cmp_sentence = (f'Against {f_match["airline"]} departing '
                        f'{f_match["time"]} at {inr(f_match["fare"])}, we are '
                        f'<b>{abs(gap)*100:.0f}% '
                        f'{"higher" if gap > 0 else "lower"}</b>.')
    else:
        cmp_sentence = "No rival flight departs at a similar time on this date."
    st.markdown(
        f'<div class="insight"><b>{f_no}</b> departs {f_time} on '
        f'<b>{dfmt(f_date, "long")}</b>, {f_days} days from now. It is '
        f'<b>{round(f_lf*100)}% full</b> ({f_sold} of {f_total} seats sold, '
        f'{f_total - f_sold} left) and is <b>{spd_txt.lower()}</b> for this route '
        f'at this stage. Our rules put the fare at '
        f'<span class="big">{inr(arith)}</span>. {cmp_sentence}</div>',
        unsafe_allow_html=True)

    ai_today, mgr_today = "—", "Not yet reviewed"
    if not ai_log_df.empty and "Flight No." in ai_log_df.columns:
        _l = ai_log_df.copy()
        _l["_dk"] = _l["Departure Date"].map(dkey)
        tl = _l[(_l["Flight No."].astype(str) == f_raw_no) &
                (_l.get("Cabin Class", pd.Series(dtype=str)).astype(str) == sel_cabin) &
                (_l["_dk"] == dkey(f_date))]
        if not tl.empty:
            ai_today  = inr(tl.iloc[-1].get("AI Suggested Fare", ""))
            mgr_today = str(tl.iloc[-1].get("Manager Decision", "Pending") or "Pending")

    ov_today = "—"
    if not feedback_df.empty and "Timestamp" in feedback_df.columns:
        fb = feedback_df.copy()
        fb["_ts"] = pd.to_datetime(fb["Timestamp"], errors="coerce")
        fb["_dk"] = fb.get("Departure Date", pd.Series(dtype=str)).map(dkey)
        m = fb[(fb.get("Flight No.", pd.Series(dtype=str)).astype(str) == f_raw_no) &
               (fb["_dk"] == dkey(f_date)) & (fb["_ts"] >= today) &
               (fb.get("Manager Decision", pd.Series(dtype=str)) == "Overridden")]
        if not m.empty:
            ov_today = inr(m.iloc[-1].get("Final Fare Used", ""))

    cseat  = seat_cost(sel_route, sel_cabin)
    p_seat = arith - cseat
    f_prof = p_seat * f_sold

    st.markdown(f"""
    <div class="kpi-strip six">
      <div class="kpi-card">
        <div class="kpi-val {lf_kpi(f_lf)}">{round(f_lf*100)}%</div>
        <div class="kpi-lbl">How full</div>
        <div class="kpi-sub">{f_sold} of {f_total} seats sold</div></div>
      <div class="kpi-card">
        <div class="kpi-val" style="font-size:0.95rem;padding-top:0.3rem">{spd_txt}</div>
        <div class="kpi-lbl">Selling speed</div>
        <div class="kpi-sub">vs normal for this route</div></div>
      <div class="kpi-card accent">
        <div class="kpi-val k-mag">{inr(arith)}</div>
        <div class="kpi-lbl">Our fare (rules)</div>
        <div class="kpi-sub">{cabin_short(sel_cabin)} base {inr(bd['cabin_base'])}</div></div>
      <div class="kpi-card">
        <div class="kpi-val k-navy">{ai_today}</div>
        <div class="kpi-lbl">AI suggested</div>
        <div class="kpi-sub">{mgr_today}</div></div>
      <div class="kpi-card">
        <div class="kpi-val k-amber">{ov_today}</div>
        <div class="kpi-lbl">Manager's own price</div>
        <div class="kpi-sub">Today only</div></div>
      <div class="kpi-card">
        <div class="kpi-val {'k-green' if f_prof > 0 else 'k-red'}">{inr(f_prof)}</div>
        <div class="kpi-lbl">Profit if all sold at this fare</div>
        <div class="kpi-sub">{inr(p_seat)}/seat · cost {inr(cseat)}</div></div>
    </div>
    """, unsafe_allow_html=True)

    # ══════════ AI PANEL ══════════
    st.markdown('<div class="sec-hd">Ask the AI to review this fare</div>',
                unsafe_allow_html=True)
    a_left, a_right = st.columns([1, 1.25], gap="large")

    with a_left:
        st.markdown(f'<div style="font-size:0.6rem;font-weight:700;color:#1B2D6B;'
                    f'text-transform:uppercase;letter-spacing:0.09em;'
                    f'margin-bottom:0.35rem;">How we got to {inr(arith)}</div>',
                    unsafe_allow_html=True)
        rows = [
            f'<div class="bd-row"><span>Standard economy fare, this route</span>'
            f'<span>{inr(bd["route_base"])}</span></div>',
            f'<div class="bd-row"><span class="bd-neu">{cabin_short(sel_cabin)} '
            f'cabin, {bd["tier_mult"]:.2f}× that</span>'
            f'<span>{inr(bd["cabin_base"])}</span></div>',
            '<div class="bd-row"><span style="color:#1B2D6B;font-weight:600">'
            'Then adjusted for:</span><span></span></div>']
        for name, v, lbl in bd["items"]:
            cls  = "bd-pos" if v > 0 else ("bd-neg" if v < 0 else "bd-neu")
            sign = "+" if v > 0 else ""
            rows.append(f'<div class="bd-row"><span class="bd-neu">&nbsp;&nbsp;'
                        f'{lbl}</span><span class="{cls}">{sign}{v*100:.0f}%</span></div>')
        ccls = ("bd-pos" if bd["comp_adj"] > 0 else
                "bd-neg" if bd["comp_adj"] < 0 else "bd-neu")
        rows.append(f'<div class="bd-row"><span class="bd-neu">&nbsp;&nbsp;'
                    f'{bd["comp_label"]}</span>'
                    f'<span class="{ccls}">{bd["comp_adj"]*100:+.0f}%</span></div>')
        cap = " (limit reached)" if bd["capped"] else ""
        rows.append(f'<div class="bd-row"><span>Total change{cap}</span>'
                    f'<span>{bd["total_demand"]*100:+.1f}%</span></div>')
        rows.append(f'<div class="bd-row"><span>Our fare</span>'
                    f'<span style="color:#E91E8C">{inr(bd["final"])}</span></div>')
        st.markdown("<div class='arith-box'>" + "".join(rows) + "</div>",
                    unsafe_allow_html=True)
        st.caption(f"The cabin multiplier applies first, then demand adjustments "
                   f"move the price within a {DEMAND_CAP_LO*100:.0f}% to "
                   f"+{DEMAND_CAP_HI*100:.0f}% band.")

    with a_right:
        route_default = standing.get(sel_route, STRATEGIC_OPTIONS[0])
        idx = STRATEGIC_OPTIONS.index(route_default) \
              if route_default in STRATEGIC_OPTIONS else 0
        strategy = st.selectbox(
            f"Pricing goal for {sel_route} (remembered for this route)",
            STRATEGIC_OPTIONS, index=idx)
        if strategy != route_default and st.button("📌  Save as this route's goal"):
            try:
                save_strategy(sel_route, strategy, analyst or "Unknown")
                st.success(f"Saved for {sel_route}.")
                st.cache_data.clear()
            except Exception as e:
                st.warning(f"Could not save: {e}")

        if st.button("🤖  Get AI recommendation"):
            if not (analyst or "").strip():
                st.error("Enter your name in the sidebar first — every decision "
                         "is recorded against whoever made it.")
            else:
                hist = []
                if not ai_log_df.empty and "Route" in ai_log_df.columns:
                    _h = ai_log_df[(ai_log_df["Route"] == sel_route) &
                                   (ai_log_df.get("Manager Decision", "")
                                    .isin(["Accepted", "Overridden"]))]
                    hist = _h.to_dict("records")
                snapshot = "; ".join(f"{a} {fn} {ft} {inr(fare)}"
                                     for a, fn, ft, fare in comp_list) or "none"
                prompt_args = dict(
                    route=sel_route, flight_no=f_no, dep_time=f_time,
                    cabin=sel_cabin, dep_date=dfmt(f_date), days_to_dep=f_days,
                    load_factor=f_lf, pace_delta=f_pace, arithmetic_fare=arith,
                    bd=bd, comp_match=f_match, comp_all=comp_list,
                    strategy=strategy, history=hist, pax_type=pax_type,
                    trip_type=trip_type)
                with st.spinner("Asking the AI pricing analyst..."):
                    dec, fare, rat, engine, note = call_llm(
                        prompt_args, bd, arith, f_match, f_lf)
                try:
                    save_ai_log({"Log Date": datetime.now().strftime("%Y-%m-%d %H:%M"),
                                 "Analyst": analyst, "Route": sel_route,
                                 "Flight No.": f_raw_no, "Departure Time": f_time,
                                 "Departure Date": dkey(f_date),
                                 "Cabin Class": sel_cabin,
                                 "Days to Departure": f_days,
                                 "Load Factor": round(f_lf * 100, 1),
                                 "Seats At Decision": f_sold,
                                 "Arithmetic Fare": arith, "AI Decision": dec,
                                 "AI Suggested Fare": fare, "AI Rationale": rat,
                                 "Engine": engine, "Competitor Snapshot": snapshot,
                                 "Strategic Direction": strategy,
                                 "Manager Decision": "Pending",
                                 "Final Fare Used": ""})
                except Exception as e:
                    st.warning(f"Recommendation received but not logged: {e}")
                st.session_state["ai"] = {
                    "dec": dec, "fare": fare, "rat": rat, "arith": arith,
                    "route": sel_route, "flight_raw": f_raw_no, "disp": f_no,
                    "time": f_time, "date": dkey(f_date), "cabin": sel_cabin,
                    "days": f_days, "lf": f_lf, "sold": f_sold,
                    "strategy": strategy, "engine": engine, "note": note,
                    "snapshot": snapshot, "analyst": analyst,
                    "pax": pax_type, "trip": trip_type}
                st.rerun()

        if "ai" in st.session_state:
            r  = st.session_state["ai"]
            ok = str(r["dec"]).lower().startswith("approve")
            badge = "ai-badge-ok" if ok else "ai-badge-ov"
            btxt  = "✔ Agrees with our fare" if ok else "⚡ Suggests a different fare"
            eng   = r.get("engine", "")
            is_fb = eng.startswith("Rules")
            hdr   = "Rules-based suggestion" if is_fb else "AI recommendation"
            c_bg  = "#fef3c7" if is_fb else "#e8f0fe"
            c_bd  = "#D97706" if is_fb else "#2F6FD0"
            c_tx  = "#b45309" if is_fb else "#1B2D6B"
            delta = r["fare"] - r["arith"]
            dtxt  = ("same as our rules" if delta == 0 else
                     f'{inr(abs(delta))} {"higher" if delta > 0 else "lower"} '
                     f'than our rules')

            st.markdown(f"""
            <div class="ai-result">
              <div style="display:flex;align-items:center;justify-content:space-between;">
                <span style="font-size:0.6rem;font-weight:700;color:#1B2D6B;
                      text-transform:uppercase;letter-spacing:0.1em;">{hdr}</span>
                <span class="{badge}">{btxt}</span></div>
              <div class="ai-price">{inr(r['fare'])}</div>
              <div style="font-size:0.72rem;color:#7c8db5;margin-top:-0.2rem;">{dtxt}</div>
              <div class="ai-rat">{r['rat']}</div>
              <div style="margin-top:0.5rem;">
                <span class="engine-chip" style="background:{c_bg};
                      border:1px solid {c_bd};color:{c_tx};">Engine: {eng}</span></div>
            </div>""", unsafe_allow_html=True)

            if is_fb:
                st.caption("No AI engine could be reached, so this came from the "
                           "pricing rules alone. It is not an AI recommendation.")
            if r.get("note"):
                with st.expander("Engine diagnostics"):
                    st.code(r["note"])

            st.markdown(f"**Your decision** — applies to today only, recorded as "
                        f"**{analyst or 'Unknown'}**")
            m1, m2 = st.columns(2)
            with m1:
                if st.button("✔  Use this fare"):
                    try:
                        commit_decision(r, "Accepted", r["fare"], "")
                        st.success(f"Set at {inr(r['fare'])}.")
                        del st.session_state["ai"]
                        st.cache_data.clear(); st.rerun()
                    except Exception as e:
                        st.error(f"Could not save: {e}")
            with m2:
                ov = st.number_input("Set my own fare (₹)", min_value=500,
                                     max_value=500000, value=int(r["fare"]),
                                     step=100, key="ovval")
                why = st.text_input("Why are you changing it?",
                                    placeholder="e.g. corporate block booking expected",
                                    key="ovwhy")
                if st.button("✏  Use my fare instead"):
                    if not why.strip():
                        st.error("Please give a short reason — it is stored with "
                                 "the decision and helps the AI learn.")
                    else:
                        try:
                            commit_decision(r, "Overridden", int(ov), why.strip())
                            st.success(f"Set at {inr(ov)}. Reason recorded.")
                            del st.session_state["ai"]
                            st.cache_data.clear(); st.rerun()
                        except Exception as e:
                            st.error(f"Could not save: {e}")

    st.markdown("<br>", unsafe_allow_html=True)

    # ══════════ ROUTE × DEPARTURE-DATE MATRIX ══════════
    st.markdown('<div class="sec-hd">Route by departure date — the whole '
                'network on one screen</div>', unsafe_allow_html=True)

    mc1, mc2, mc3 = st.columns([1.5, 1, 1])
    with mc1:
        metric = st.selectbox(
            "Colour the squares by",
            ["How full each flight is",
             "Selling speed vs normal",
             "Our fare",
             "Gap against closest rival"], key="mx_metric")
    with mc2:
        mx_cabin = st.selectbox("Cabin", C["cabins"],
                                index=C["cabins"].index(sel_cabin)
                                if sel_cabin in C["cabins"] else 0, key="mx_cabin")
    with mc3:
        mx_days = st.selectbox("How far ahead", [14, 30, 45], index=1,
                               key="mx_days")

    mx_end = today + timedelta(days=int(mx_days))
    mx_src = indigo_df[(indigo_df["Cabin Class"] == mx_cabin) &
                       (indigo_df["Departure Date"] >= today) &
                       (indigo_df["Departure Date"] <= mx_end)].copy()

    if mx_src.empty:
        st.info("No flights in this window for the chosen cabin.")
    else:
        mx_src = (mx_src.sort_values(dcol)
                  .groupby(["Route", "Flight No.", "Departure Date"],
                           as_index=False).last())
        cla   = C["comp_latest_all"]
        dates = sorted(pd.to_datetime(mx_src["Departure Date"].unique()))
        rts   = sorted(mx_src["Route"].unique())

        Z, T, H = [], [], []
        stats = {"full": [], "gap": [], "pace": []}
        worst = {"gap": None, "pace": None, "full": None}

        for rt in rts:
            zr, tr, hr = [], [], []
            for dd in dates:
                sub = mx_src[(mx_src["Route"] == rt) &
                             (mx_src["Departure Date"] == dd)]
                if sub.empty:
                    zr.append(None); tr.append(""); hr.append("No flight")
                    continue
                lfs, fares, gaps, paces = [], [], [], []
                for _, g in sub.iterrows():
                    ftm  = str(g.get("Departure Time", ""))
                    lf   = float(g.get("Load Factor", 0) or 0)
                    dout = int(g.get("Days to Departure", 30) or 30)
                    hol  = str(g.get("Holiday / Festival", "No")) == "Yes"
                    cm = cla[(cla["Route"] == rt) &
                             (cla["Cabin Class"] == mx_cabin) &
                             (cla["Departure Date"] == dd)] \
                         if not cla.empty else pd.DataFrame()
                    mt = match_competitor(cm, deph(ftm))
                    pl = pace_delta_for(pace_curve, rt, mx_cabin, dout, lf)
                    fv, _ = calc_fare(rt, mx_cabin, dout, lf,
                                      mt["fare"] if mt else 0, hol, deph(ftm),
                                      pace_delta=pl)
                    lfs.append(lf); fares.append(fv)
                    if pl is not None:
                        paces.append(pl)
                    if mt and mt["fare"]:
                        gaps.append((fv - mt["fare"]) / mt["fare"])

                lf_m   = float(np.mean(lfs))   if lfs   else None
                fare_m = float(np.mean(fares)) if fares else None
                gap_m  = float(np.mean(gaps))  if gaps  else None
                pace_m = float(np.mean(paces)) if paces else None

                if lf_m   is not None: stats["full"].append(lf_m)
                if gap_m  is not None: stats["gap"].append(abs(gap_m))
                if pace_m is not None: stats["pace"].append(pace_m)
                if gap_m is not None and (worst["gap"] is None
                                          or abs(gap_m) > abs(worst["gap"][2])):
                    worst["gap"] = (rt, dd, gap_m)
                if pace_m is not None and (worst["pace"] is None
                                           or pace_m < worst["pace"][2]):
                    worst["pace"] = (rt, dd, pace_m)
                if lf_m is not None and (worst["full"] is None
                                         or lf_m > worst["full"][2]):
                    worst["full"] = (rt, dd, lf_m)

                if metric == "How full each flight is":
                    zr.append(round(lf_m * 100, 1) if lf_m is not None else None)
                    tr.append(f"{lf_m*100:.0f}%" if lf_m is not None else "")
                elif metric == "Selling speed vs normal":
                    zr.append(round(pace_m * 100, 1) if pace_m is not None else None)
                    tr.append(f"{pace_m*100:+.0f}" if pace_m is not None else "")
                elif metric == "Our fare":
                    zr.append(round(fare_m) if fare_m is not None else None)
                    tr.append(f"{fare_m/1000:.1f}k" if fare_m is not None else "")
                else:
                    zr.append(round(gap_m * 100, 1) if gap_m is not None else None)
                    tr.append(f"{gap_m*100:+.0f}%" if gap_m is not None else "")

                hr.append(
                    f"<b>{rt}</b><br>Departing {dfmt(dd, 'long')}<br>"
                    f"{len(sub)} flight{'s' if len(sub) != 1 else ''} · {mx_cabin}"
                    f"<br>─────────────<br>"
                    f"{'—' if lf_m is None else f'{lf_m*100:.0f}% full'}<br>"
                    f"Our fare {inr(fare_m)}<br>"
                    f"{'No rival flight' if gap_m is None else f'{gap_m*100:+.0f}% vs closest rival'}"
                    f"<br>{fill_speed_words(pace_m)[0]}")
            Z.append(zr); T.append(tr); H.append(hr)

        # ── Insight sentence before the picture ──
        bits = []
        if worst["full"]:
            rt, dd, v = worst["full"]
            bits.append(f'the fullest is <b>{rt}</b> on <b>{dfmt(dd)}</b> at '
                        f'{v*100:.0f}%')
        if worst["gap"]:
            rt, dd, v = worst["gap"]
            bits.append(f'the biggest price gap is <b>{rt}</b> on '
                        f'<b>{dfmt(dd)}</b>, where we sit {abs(v)*100:.0f}% '
                        f'{"above" if v > 0 else "below"} the closest rival')
        if worst["pace"] and worst["pace"][2] < -0.03:
            rt, dd, v = worst["pace"]
            bits.append(f'the slowest seller is <b>{rt}</b> on <b>{dfmt(dd)}</b>, '
                        f'{abs(v)*100:.0f} points behind normal')
        avg_full = (f'{np.mean(stats["full"])*100:.0f}%'
                    if stats["full"] else "n/a")
        st.markdown(
            f'<div class="insight">Looking at <b>{mx_cabin}</b> across '
            f'<b>{len(rts)} routes</b> and the next <b>{mx_days} days</b>, '
            f'flights are averaging <b>{avg_full} full</b>. Of these, '
            + ("; ".join(bits) if bits else "nothing stands out") +
            '.</div>', unsafe_allow_html=True)

        with st.expander("How to read this grid"):
            st.markdown(
                "- **Each row is one route.** Each **column is one departure "
                "date**, running from today rightwards.\n"
                "- **Each square is therefore one route on one day** — for "
                "example, Mumbai → Delhi departing 09-08-2026 — averaged "
                f"across every {mx_cabin} flight IndiGo operates on that "
                "route that day.\n"
                "- **The number inside** the square is the value you picked in "
                "*Colour the squares by*; the **colour** is the same value, so "
                "you can spot patterns without reading every figure.\n"
                "- **A blank square** means we have no flight on that route "
                "that day.\n"
                "- **Hover any square** for the full picture: how full, our "
                "fare, the gap against the closest rival, and how fast it is "
                "selling.\n\n"
                "Read **across a row** to see how one route behaves as its "
                "departure date approaches. Read **down a column** to compare "
                "every route on the same day.")

        if metric == "How full each flight is":
            scale, zmid, cbar = "RdYlGn_r", None, "% full"
        elif metric == "Selling speed vs normal":
            scale, zmid, cbar = "RdYlGn", 0, "points vs normal"
        elif metric == "Our fare":
            scale, zmid, cbar = "Blues", None, "fare ₹"
        else:
            scale, zmid, cbar = "RdBu_r", 0, "% vs rival"

        xlabels = [d.strftime("%d %b") for d in dates]
        fig_m = go.Figure(go.Heatmap(
            z=Z, x=xlabels,
            y=[r.replace(" to ", " → ") for r in rts],
            text=T, texttemplate="%{text}", textfont=dict(size=10),
            customdata=H, hovertemplate="%{customdata}<extra></extra>",
            colorscale=scale, zmid=zmid, hoverongaps=False,
            colorbar=dict(title=dict(text=cbar, font=dict(size=9)),
                          thickness=11, len=0.85),
            xgap=2, ygap=2))
        # Plain category axis — a date axis reads "26-07" as the year 2026
        fig_m.update_xaxes(type="category", side="top", tickangle=-60,
                           tickfont=dict(size=9), title_text="")
        fig_m.update_yaxes(type="category", tickfont=dict(size=10),
                           autorange="reversed", title_text="")
        style_chart(fig_m, height=130 + 52 * len(rts), legend=False)
        fig_m.update_layout(margin=dict(l=10, r=10, t=52, b=10))
        st.plotly_chart(fig_m, use_container_width=True)

        note = {
            "How full each flight is":
                "Red squares are nearly full flights — candidates for a higher "
                "fare. Green squares have plenty of empty seats.",
            "Selling speed vs normal":
                "Green means selling faster than this route usually does by "
                "this point. Red means slower, and may need a sharper price. "
                "The number is percentage points above or below normal.",
            "Our fare":
                "Darker blue is a higher fare, shown in thousands of rupees. "
                "Look for jumps between neighbouring days you cannot explain.",
            "Gap against closest rival":
                "Red means we are dearer than the nearest rival departure, "
                "blue means cheaper, white roughly level.",
        }[metric]
        st.caption(note)

    st.markdown("<br>", unsafe_allow_html=True)

    # ══════════ FARE TABLE ══════════
    st.markdown('<div class="sec-hd">Every flight on this route, '
                'against its closest rival</div>', unsafe_allow_html=True)

    acc_lookup, log_lookup = {}, {}
    if not feedback_df.empty and "Manager Decision" in feedback_df.columns:
        for _, x in feedback_df[feedback_df["Manager Decision"]
                                .isin(["Accepted", "Overridden"])].iterrows():
            k = (str(x.get("Flight No.", "")), str(x.get("Cabin Class", "")),
                 dkey(x.get("Departure Date", "")))
            try:    acc_lookup[k] = int(x.get("Final Fare Used", 0))
            except Exception: pass
    if not ai_log_df.empty and "Flight No." in ai_log_df.columns:
        for _, x in ai_log_df.iterrows():
            k = (str(x.get("Flight No.", "")), str(x.get("Cabin Class", "")),
                 dkey(x.get("Departure Date", "")))
            try:    log_lookup[k] = int(x.get("AI Suggested Fare", 0))
            except Exception: pass

    cabin_base_disp = int(BASE_FARES.get(sel_route, 5000)
                          * CABIN_MULT.get(sel_cabin, 1.0))
    html = ("""<table class="wrap"><colgroup>
    <col style="width:14%"><col style="width:11%"><col style="width:9%">
    <col style="width:13%"><col style="width:10%"><col style="width:11%">
    <col style="width:10%"><col style="width:14%"><col style="width:8%">
    </colgroup><thead><tr>
      <th>IndiGo flight</th><th>Time of day</th><th>How full</th>
      <th>Selling speed</th><th>Cabin base</th><th>Our fare</th>
      <th>AI / final</th><th>Closest rival</th><th>Difference</th>
    </tr></thead><tbody>""")

    cur_date = None
    for _, row in indigo_f.sort_values(["Departure Date", "Departure Time"]).iterrows():
        dd   = row["Departure Date"]
        raw  = str(row.get("Flight No.", ""))
        ftm  = str(row.get("Departure Time", ""))
        fno  = fno_disp(raw, ftm)
        slot = str(row.get("Time Slot", ""))
        dout = int(row.get("Days to Departure", 30) or 30)
        lf   = float(row.get("Load Factor", 0) or 0)
        tot  = int(row.get("Total Seats", 180) or 180)
        sold = int(row.get("Seats Sold", 0) or 0)
        if sold <= 0 and lf > 0:
            sold = int(round(lf * tot))
        hol = str(row.get("Holiday / Festival", "No"))
        c, dot = lf_cls(lf)

        if cur_date is None or dd != cur_date:
            cur_date = dd
            html += (f'<tr class="date-sep"><td colspan="9">✈ {dfmt(dd, "long")}'
                     f' &nbsp;—&nbsp; {dout} days to departure</td></tr>')

        rows_c = comp_f[comp_f["Departure Date"] == dd] \
                 if not comp_f.empty else pd.DataFrame()
        mt  = match_competitor(rows_c, deph(ftm))
        pdl = pace_delta_for(pace_curve, sel_route, sel_cabin, dout, lf)
        ar, _b = calc_fare(sel_route, sel_cabin, dout, lf,
                           mt["fare"] if mt else 0, hol == "Yes", deph(ftm),
                           pax_type, trip_type, pace_delta=pdl)
        dk  = dkey(dd)
        acc = acc_lookup.get((raw, sel_cabin, dk))
        rec = acc or log_lookup.get((raw, sel_cabin, dk))
        rcls = "f-ai" if acc else "f-ailog"
        rsub = "manager set" if acc else ("AI suggested" if rec else "")

        if mt:
            comp_s = (f'{mt["airline"]}<br><span style="color:{GREY};'
                      f'font-size:0.67rem">{mt["time"]} · {inr(mt["fare"])}</span>')
            g = (ar - mt["fare"]) / mt["fare"]
            gap_s = f'{"+" if g > 0 else ""}{g*100:.0f}%'
            gcls = "f-exp" if g > 0.03 else ("f-cheap" if g < -0.03 else "f-sim")
        else:
            comp_s, gap_s, gcls = "—", "—", ""
        spd_t, spd_c = fill_speed_words(pdl)

        html += f"""<tr>
          <td class="f-navy"><b>{fno}</b><br>
              <span style="color:{GREY};font-size:0.67rem">{ftm}</span></td>
          <td style="color:{GREY};font-size:0.71rem">{slot}</td>
          <td class="num"><span class="{c}">{dot} {round(lf*100)}%</span><br>
              <span style="color:{GREY};font-size:0.66rem">{sold}/{tot}</span></td>
          <td><span class="{spd_c}" style="font-size:0.7rem">{spd_t}</span></td>
          <td class="num f-navy">{inr(cabin_base_disp)}</td>
          <td class="num f-mag"><b>{inr(ar)}</b></td>
          <td class="num"><span class="{rcls}">{inr(rec) if rec else '—'}</span><br>
              <span style="color:{GREY};font-size:0.63rem">{rsub}</span></td>
          <td style="font-size:0.71rem">{comp_s}</td>
          <td class="num"><span class="{gcls}">{gap_s}</span></td>
        </tr>"""
    html += "</tbody></table>"
    st.markdown(html, unsafe_allow_html=True)
    st.markdown(f"""<div class="legend-row">
      <b>Cabin base</b> is the standard {cabin_short(sel_cabin)} fare for this
      route before adjustment ({CABIN_MULT.get(sel_cabin, 1.0):.2f}× economy)
      &nbsp;·&nbsp; <b>Closest rival</b> is the competitor departing nearest in
      time, not the cheapest of the day
    </div>""", unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ══════════ CHARTS ══════════
    win = st.selectbox("Chart period", ["Last 7 days", "Last 14 days",
                                        "Last 30 days", "Last 60 days"],
                       index=2, key="chartwin")
    win_days = {"Last 7 days": 7, "Last 14 days": 14,
                "Last 30 days": 30, "Last 60 days": 60}[win]
    win_from = today - timedelta(days=win_days)

    c_left, c_right = st.columns([1, 1.15], gap="large")

    with c_left:
        st.markdown('<div class="sec-hd">How this flight has been filling up</div>',
                    unsafe_allow_html=True)
        flights, subr = [], indigo_df[indigo_df["Route"] == sel_route]
        if not subr.empty:
            flights = sorted(subr.apply(
                lambda r: f'{r["Flight No."]}|{r["Departure Time"]}',
                axis=1).dropna().unique().tolist())
        if not flights:
            st.info("No flights on this route.")
        else:
            disp = [f'{fno_disp(x.split("|")[0], x.split("|")[1])} · {x.split("|")[1]}'
                    for x in flights]
            cur = f'{f_raw_no}|{f_time}'
            ix = flights.index(cur) if cur in flights else 0
            pick_d = st.selectbox("Flight", disp, index=ix, key="lfpick")
            pno = flights[disp.index(pick_d)].split("|")[0]

            hist = indigo_df[(indigo_df["Route"] == sel_route) &
                             (indigo_df["Cabin Class"] == sel_cabin) &
                             (indigo_df["Flight No."].astype(str) == pno) &
                             (indigo_df["Departure Date"].isin(sel_dates))].copy()
            if not hist.empty and dcol in hist.columns:
                hist = hist[hist[dcol] >= win_from]
            if hist.empty or dcol not in hist.columns:
                st.info("No booking history in this period for this flight.")
            else:
                hist = hist.dropna(subset=[dcol]).sort_values(dcol)
                hist = hist.groupby(dcol, as_index=False).last()
                hist["Full%"] = (pd.to_numeric(hist["Load Factor"],
                                               errors="coerce") * 100).round(1)
                seats = pd.to_numeric(hist["Seats Sold"], errors="coerce")
                hist["Booked"] = seats.diff().fillna(seats).clip(lower=0)
                hist["dout"] = pd.to_numeric(hist["Days to Departure"], errors="coerce")
                hist["Usual%"] = hist["dout"].map(
                    lambda d: pace_curve.get((sel_route, sel_cabin, int(d)))
                    if pd.notna(d) else None)
                hist["Usual%"] = (pd.to_numeric(hist["Usual%"], errors="coerce")
                                  * 100).round(1)

                if hist["Full%"].notna().any() and hist["Usual%"].notna().any():
                    dn = hist["Full%"].iloc[-1] - hist["Usual%"].iloc[-1]
                    verdict = ("ahead of" if dn > 2 else
                               "behind" if dn < -2 else "in line with")
                    st.markdown(f'<div class="insight" style="font-size:0.8rem">'
                                f'Currently <b>{verdict}</b> where this route '
                                f'normally is at this point '
                                f'({hist["Full%"].iloc[-1]:.0f}% full versus a '
                                f'typical {hist["Usual%"].iloc[-1]:.0f}%).</div>',
                                unsafe_allow_html=True)

                fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                                    vertical_spacing=0.09, row_heights=[0.4, 0.6])
                fig.add_trace(go.Bar(
                    x=hist[dcol], y=hist["Booked"], name="Seats booked that day",
                    marker_color="rgba(233,30,140,0.55)",
                    hovertemplate="%{y:.0f} seats<extra></extra>"), row=1, col=1)
                fig.add_trace(go.Scatter(
                    x=hist[dcol], y=hist["Full%"], name="This flight",
                    mode="lines+markers", line=dict(color=NAVY, width=2.5),
                    marker=dict(size=5, color=NAVY), fill="tozeroy",
                    fillcolor="rgba(27,45,107,0.07)",
                    hovertemplate="%{y:.1f}% full<extra></extra>"), row=2, col=1)
                if hist["Usual%"].notna().any():
                    fig.add_trace(go.Scatter(
                        x=hist[dcol], y=hist["Usual%"], name="Normal for this route",
                        mode="lines", line=dict(color=AMBER, width=2, dash="dot"),
                        hovertemplate="%{y:.1f}% typical<extra></extra>"),
                        row=2, col=1)
                fig.update_yaxes(title_text="Seats", row=1, col=1)
                fig.update_yaxes(title_text="% full", range=[0, 105], row=2, col=1)
                fig.update_xaxes(type="date", tickformat="%d-%m",
                                 range=[win_from, today], row=1, col=1)
                fig.update_xaxes(type="date", tickformat="%d-%m", dtick=86400000 * 2,
                                 range=[win_from, today], title_text="", row=2, col=1)
                style_chart(fig, height=340)
                st.plotly_chart(fig, use_container_width=True)
                st.caption("Pink bars are seats sold each day. Navy is how full the "
                           "flight is; dotted amber is how full this route usually "
                           "is by the same point.")

    with c_right:
        st.markdown('<div class="sec-hd">How prices have moved</div>',
                    unsafe_allow_html=True)
        frames = []
        ch = comp_df[(comp_df["Route"] == sel_route) &
                     (comp_df["Cabin Class"] == sel_cabin) &
                     (comp_df["Departure Date"].isin(sel_dates)) &
                     (comp_df["Scrape Date"] >= win_from)].copy() \
             if not comp_df.empty and "Scrape Date" in comp_df.columns \
             else pd.DataFrame()
        if not ch.empty:
            ch["_gap"] = ch["Departure Time"].map(
                lambda t: clock_gap(deph(t), deph(f_time)))
            near = ch[ch["_gap"] <= 3.0]
            if near.empty:
                near = ch
            g = near.groupby(["Scrape Date", "Airline"])["Fare (INR)"].mean().reset_index()
            g.columns = ["Date", "Series", "Fare"]
            frames.append(g)

        ih = indigo_df[(indigo_df["Route"] == sel_route) &
                       (indigo_df["Cabin Class"] == sel_cabin) &
                       (indigo_df["Flight No."].astype(str) == f_raw_no) &
                       (indigo_df["Departure Date"].isin(sel_dates))].copy()
        if not ih.empty and dcol in ih.columns:
            ih = ih.dropna(subset=[dcol])
            ih = ih[ih[dcol] >= win_from].sort_values(dcol)
            rws = []
            for _, g2 in ih.iterrows():
                d = g2[dcol]
                dayc = ch[ch["Scrape Date"] == d] if not ch.empty else pd.DataFrame()
                mtd = match_competitor(dayc, deph(f_time)) if not dayc.empty else None
                dl = pace_delta_for(pace_curve, sel_route, sel_cabin,
                                    int(g2.get("Days to Departure", 30) or 30),
                                    float(g2.get("Load Factor", 0.6) or 0.6))
                v, _ = calc_fare(sel_route, sel_cabin,
                                 int(g2.get("Days to Departure", 30) or 30),
                                 float(g2.get("Load Factor", 0.6) or 0.6),
                                 mtd["fare"] if mtd else 0,
                                 str(g2.get("Holiday / Festival", "No")) == "Yes",
                                 deph(g2.get("Departure Time", "10:00")),
                                 pax_type, trip_type, pace_delta=dl)
                rws.append({"Date": d, "Series": "IndiGo (our rules)", "Fare": v})
            if rws:
                frames.append(pd.DataFrame(rws))

        if not ai_log_df.empty and "Log Date" in ai_log_df.columns:
            al = ai_log_df.copy()
            al["_dk"] = al.get("Departure Date", pd.Series(dtype=str)).map(dkey)
            al = al[(al.get("Route", "") == sel_route) &
                    (al.get("Cabin Class", "") == sel_cabin) &
                    (al.get("Flight No.", pd.Series(dtype=str)).astype(str) == f_raw_no) &
                    (al["_dk"] == dkey(f_date))]
            if not al.empty and "AI Suggested Fare" in al.columns:
                al["AI Suggested Fare"] = pd.to_numeric(al["AI Suggested Fare"],
                                                        errors="coerce")
                al["_d"] = pd.to_datetime(al["Log Date"], errors="coerce").dt.normalize()
                al = al.dropna(subset=["_d", "AI Suggested Fare"])
                al = al[al["_d"] >= win_from]
                if not al.empty:
                    g = al.groupby("_d")["AI Suggested Fare"].mean().reset_index()
                    g.columns = ["Date", "Fare"]
                    g["Series"] = "IndiGo (AI suggested)"
                    frames.append(g[["Date", "Series", "Fare"]])

        if frames:
            allt = pd.concat(frames, ignore_index=True).dropna(subset=["Fare"])
            allt["Date"] = pd.to_datetime(allt["Date"], errors="coerce")
            allt = allt.dropna(subset=["Date"]).sort_values("Date")
            fig2 = px.line(allt, x="Date", y="Fare", color="Series", markers=True,
                           color_discrete_map={"Air India": SKY, "Akasa Air": RED,
                                               "IndiGo (our rules)": MAGENTA,
                                               "IndiGo (AI suggested)": NAVY})
            for tr in fig2.data:
                if tr.name.startswith("IndiGo"):
                    tr.line.dash = "dash"; tr.line.width = 2.5
            fig2.update_yaxes(title_text="Fare (₹)")
            fig2.update_xaxes(type="date", tickformat="%d-%m", title_text="",
                              range=[win_from, today])
            style_chart(fig2, height=340)
            st.plotly_chart(fig2, use_container_width=True)
            st.caption(f"{win.lower()}, for this flight and cabin only. Rivals are "
                       "limited to departures within three hours of ours. "
                       "Dashed lines are IndiGo's own prices.")
        else:
            st.info("No price history in this period for this flight.")

    st.markdown('<div class="sec-hd">Price against how full the flight gets</div>',
                unsafe_allow_html=True)
    sc = indigo_df[(indigo_df["Route"] == sel_route) &
                   (indigo_df["Cabin Class"] == sel_cabin)].copy()
    if not sc.empty and dcol in sc.columns:
        sc = sc[sc[dcol] >= win_from]
    if sc.empty or dcol not in sc.columns:
        st.info("Not enough history in this period.")
    else:
        sc = sc.dropna(subset=["Load Factor", "Days to Departure"])
        pts = []
        for _, g2 in sc.iterrows():
            dl = pace_delta_for(pace_curve, sel_route, sel_cabin,
                                int(g2["Days to Departure"]), float(g2["Load Factor"]))
            v, _ = calc_fare(sel_route, sel_cabin, int(g2["Days to Departure"]),
                             float(g2["Load Factor"]), 0,
                             str(g2.get("Holiday / Festival", "No")) == "Yes",
                             deph(g2.get("Departure Time", "10:00")), pace_delta=dl)
            pts.append({"Fare": v, "Full": float(g2["Load Factor"]) * 100,
                        "Days before departure": int(g2["Days to Departure"]),
                        "Flight": fno_disp(g2["Flight No."], g2["Departure Time"])})
        spd = pd.DataFrame(pts)
        fig4 = px.scatter(spd, x="Fare", y="Full", color="Flight",
                          size="Days before departure", size_max=13, opacity=0.65,
                          color_discrete_sequence=[NAVY, MAGENTA, SKY, AMBER])
        fig4.update_yaxes(title_text="% full", range=[0, 105])
        fig4.update_xaxes(title_text="Fare our rules would charge (₹)")
        style_chart(fig4, height=300)
        st.plotly_chart(fig4, use_container_width=True)
        st.caption("Each dot is one day's reading for one flight over the "
                   f"{win.lower()}. Larger dots are further from departure.")


# ═════════════════════════════════════════════════════════════
# TAB 2 — WHAT NEEDS ATTENTION  (priced inline, no tab switching)
# ═════════════════════════════════════════════════════════════
def render_action_list(C):
    rows = C["triage_rows"]
    ai_log_df, analyst = C["ai_log_df"], C["analyst"]
    pax_type, trip_type = C["pax_type"], C["trip_type"]
    standing = C["standing"]

    st.markdown('<div class="tab-intro">Fares that are out of line for the '
                'selected departure date, biggest exposure first. Price them '
                'here — once you set a fare, the row clears.</div>',
                unsafe_allow_html=True)

    open_rows = [t for t in rows if t["flag"] != "green" and not t["settled"]]
    settled   = [t for t in rows if t["settled"]]
    green     = [t for t in rows if t["flag"] == "green" and not t["settled"]]

    if not open_rows:
        st.success(f"Nothing outstanding. {len(green)} fares are in line with "
                   f"their closest rival"
                   + (f" and {len(settled)} were priced today." if settled
                      else "."))
        if settled:
            with st.expander(f"Cleared today ({len(settled)})"):
                for t in settled:
                    st.markdown(f"- **{t['route']}** {t['flight']} {t['time']} · "
                                f"{cabin_short(t['cabin'])} — priced today")
        return

    red   = [t for t in open_rows if t["flag"] == "red"]
    amber = [t for t in open_rows if t["flag"] == "amber"]
    top   = open_rows[0]

    if top["comp"]:
        direction = "more expensive than" if (top["gap"] or 0) > 0 else "cheaper than"
        head = (f'Start with <b>{top["route"]}</b>, {top["flight"]} at '
                f'{top["time"]}, {cabin_short(top["cabin"])}. Our fare of '
                f'<b>{inr(top["fare"])}</b> is <b>{abs(top["gap"] or 0)*100:.0f}% '
                f'{direction}</b> {top["comp"]["airline"]} at '
                f'{top["comp"]["time"]} ({inr(top["comp"]["fare"])}), with '
                f'<b>{top["remaining"]} seats</b> unsold and {top["dout"]} days '
                f'to go — <span class="big">{inr(top["risk"])}</span> riding on '
                f'this one price.')
    else:
        head = (f'Start with <b>{top["route"]}</b> {top["flight"]}, though no '
                f'rival departs at a similar time.')

    movers = [t for t in open_rows
              if t["move_pc"] is not None and abs(t["move_pc"]) > 0.05]
    mv = ""
    if movers:
        m = max(movers, key=lambda x: abs(x["move_pc"]))
        mv = (f' {len(movers)} rival fares moved more than 5% overnight, the '
              f'biggest being {m["comp"]["airline"]} on {m["route"]}, '
              f'{"up" if m["move"] > 0 else "down"} {inr(abs(m["move"]))}.')

    st.markdown(
        f'<div class="insight"><b>{len(red)} fares need action</b> and '
        f'{len(amber)} are worth watching. {head}{mv} Across everything still '
        f'open, <b>{inr(sum(t["risk"] for t in open_rows))}</b> of revenue is '
        f'exposed.' + (f' {len(settled)} were already priced today.'
                       if settled else '') + '</div>', unsafe_allow_html=True)

    # ── Overview table ───────────────────────────────────────
    html = ("""<table class="wrap"><colgroup>
    <col style="width:3%"><col style="width:17%"><col style="width:9%">
    <col style="width:11%"><col style="width:9%"><col style="width:15%">
    <col style="width:7%"><col style="width:9%"><col style="width:20%">
    </colgroup><thead><tr>
      <th></th><th>Flight</th><th>How full</th><th>Selling speed</th>
      <th>Our fare</th><th>Closest rival flight</th><th>Difference</th>
      <th>Money at stake</th><th>What this means</th>
    </tr></thead><tbody>""")
    for t in open_rows:
        fcls = {"red": "flag-red", "amber": "flag-amber"}[t["flag"]]
        rowc = {"red": "row-red", "amber": "row-amber"}[t["flag"]]
        lcls, dot = lf_cls(t["lf"])
        spd_t, spd_c = fill_speed_words(t["pace"])
        if t["comp"]:
            rival = (f'{t["comp"]["airline"]}<br><span style="color:{GREY}">'
                     f'{t["comp"]["time"]} · {inr(t["comp"]["fare"])}</span>')
            g = t["gap"]
            gtxt = f'{"+" if g > 0 else ""}{g*100:.0f}%'
            gcls = "f-exp" if g > 0.03 else ("f-cheap" if g < -0.03 else "f-sim")
            if t["move"] is not None and abs(t["move"]) >= 1:
                arrow = "▲" if t["move"] > 0 else "▼"
                acls  = "dl-up" if t["move"] > 0 else "dl-dn"
                rival += (f'<br><span class="{acls}" style="font-size:0.66rem">'
                          f'{arrow} {inr(abs(t["move"]))} since yesterday</span>')
        else:
            rival, gtxt, gcls = "—", "—", ""
        html += f"""<tr class="{rowc}">
          <td><span class="{fcls}">●</span></td>
          <td><b class="f-navy">{t['route'].replace(' to ',' → ')}</b><br>
              <span style="color:{GREY};font-size:0.7rem">{t['flight']} ·
              {t['time']} · {cabin_short(t['cabin'])}</span></td>
          <td class="num"><span class="{lcls}">{dot} {round(t['lf']*100)}%</span><br>
              <span style="color:{GREY};font-size:0.66rem">{t['sold']}/{t['total']}</span></td>
          <td><span class="{spd_c}">{spd_t}</span></td>
          <td class="num f-mag"><b>{inr(t['fare'])}</b></td>
          <td style="font-size:0.71rem">{rival}</td>
          <td class="num"><span class="{gcls}">{gtxt}</span></td>
          <td class="num f-navy">{inr(t['risk'])}</td>
          <td class="advice">{advice_line(t)}</td>
        </tr>"""
    html += "</tbody></table>"
    st.markdown(html, unsafe_allow_html=True)
    st.markdown(f"""<div class="legend-row">
      <span style="color:{RED}">●</span> <b>Needs action</b> — over 15% from the
      closest rival with a week or less left, or that rival moved over 10%
      overnight &nbsp;·&nbsp;
      <span style="color:{AMBER}">●</span> <b>Watch</b> — over 8% away, or rival
      moved over 5% &nbsp;·&nbsp;
      <b>Money at stake</b> = price difference × seats still unsold
    </div>""", unsafe_allow_html=True)

    # ── Price each one inline ────────────────────────────────
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown('<div class="sec-hd">Set a fare without leaving this page</div>',
                unsafe_allow_html=True)

    if not (analyst or "").strip():
        st.warning("Enter your name in the sidebar before pricing — every "
                   "decision is recorded against whoever made it.")

    for t in open_rows:
        k = t["key"]
        icon = "🔴" if t["flag"] == "red" else "🟠"
        title = (f'{icon}  {t["route"]} · {t["flight"]} {t["time"]} · '
                 f'{cabin_short(t["cabin"])}  —  {inr(t["risk"])} at stake')
        with st.expander(title, expanded=(t is open_rows[0])):
            i1, i2, i3, i4 = st.columns(4)
            i1.metric("How full", f'{round(t["lf"]*100)}%',
                      f'{t["remaining"]} seats left')
            i2.metric("Our fare", inr(t["fare"]))
            i3.metric("Closest rival",
                      inr(t["comp"]["fare"]) if t["comp"] else "—",
                      f'{t["gap"]*100:+.0f}%' if t["gap"] is not None else None)
            i4.metric("Days to departure", t["dout"],
                      fill_speed_words(t["pace"])[0])
            st.caption(advice_line(t))

            rdef = standing.get(t["route"], STRATEGIC_OPTIONS[0])
            ridx = STRATEGIC_OPTIONS.index(rdef) if rdef in STRATEGIC_OPTIONS else 0
            strat = st.selectbox("Pricing goal", STRATEGIC_OPTIONS,
                                 index=ridx, key=f"st_{k}")

            if st.button("🤖  Get AI recommendation", key=f"ai_{k}"):
                if not (analyst or "").strip():
                    st.error("Enter your name in the sidebar first.")
                else:
                    hist = []
                    if not ai_log_df.empty and "Route" in ai_log_df.columns:
                        _h = ai_log_df[(ai_log_df["Route"] == t["route"]) &
                                       (ai_log_df.get("Manager Decision", "")
                                        .isin(["Accepted", "Overridden"]))]
                        hist = _h.to_dict("records")
                    clist = [(str(c["Airline"]), str(c["Flight No."]),
                              str(c["Departure Time"]), int(c["Fare (INR)"]))
                             for _, c in t["comp_rows"].iterrows()
                             if pd.notna(c.get("Fare (INR)"))] \
                            if not t["comp_rows"].empty else []
                    snap = "; ".join(f"{a} {fn} {ft} {inr(fa)}"
                                     for a, fn, ft, fa in clist) or "none"
                    pargs = dict(route=t["route"], flight_no=t["flight"],
                                 dep_time=t["time"], cabin=t["cabin"],
                                 dep_date=dfmt(t["dep"]), days_to_dep=t["dout"],
                                 load_factor=t["lf"], pace_delta=t["pace"],
                                 arithmetic_fare=t["fare"], bd=t["bd"],
                                 comp_match=t["comp"], comp_all=clist,
                                 strategy=strat, history=hist,
                                 pax_type=pax_type, trip_type=trip_type)
                    with st.spinner("Asking the AI pricing analyst..."):
                        dec, fare, rat, engine, note = call_llm(
                            pargs, t["bd"], t["fare"], t["comp"], t["lf"])
                    try:
                        save_ai_log({
                            "Log Date": datetime.now().strftime("%Y-%m-%d %H:%M"),
                            "Analyst": analyst, "Route": t["route"],
                            "Flight No.": t["raw"], "Departure Time": t["time"],
                            "Departure Date": dkey(t["dep"]),
                            "Cabin Class": t["cabin"], "Days to Departure": t["dout"],
                            "Load Factor": round(t["lf"] * 100, 1),
                            "Seats At Decision": t["sold"],
                            "Arithmetic Fare": t["fare"], "AI Decision": dec,
                            "AI Suggested Fare": fare, "AI Rationale": rat,
                            "Engine": engine, "Competitor Snapshot": snap,
                            "Strategic Direction": strat,
                            "Manager Decision": "Pending", "Final Fare Used": ""})
                    except Exception as e:
                        st.warning(f"Received but not logged: {e}")
                    st.session_state[f"res_{k}"] = {
                        "dec": dec, "fare": fare, "rat": rat, "arith": t["fare"],
                        "route": t["route"], "flight_raw": t["raw"],
                        "time": t["time"], "date": dkey(t["dep"]),
                        "cabin": t["cabin"], "days": t["dout"], "lf": t["lf"],
                        "sold": t["sold"], "strategy": strat, "engine": engine,
                        "snapshot": snap, "analyst": analyst,
                        "pax": pax_type, "trip": trip_type}
                    st.rerun()

            res = st.session_state.get(f"res_{k}")
            if res:
                ok = str(res["dec"]).lower().startswith("approve")
                badge = "ai-badge-ok" if ok else "ai-badge-ov"
                btxt = "✔ Agrees with our fare" if ok else "⚡ Suggests different"
                eng = res.get("engine", "")
                is_fb = eng.startswith("Rules")
                d = res["fare"] - res["arith"]
                dtxt = ("same as our rules" if d == 0 else
                        f'{inr(abs(d))} {"higher" if d > 0 else "lower"} than our rules')
                st.markdown(f"""
                <div class="ai-result">
                  <div style="display:flex;align-items:center;justify-content:space-between;">
                    <span style="font-size:0.6rem;font-weight:700;color:#1B2D6B;
                          text-transform:uppercase;letter-spacing:0.1em;">
                      {"Rules-based suggestion" if is_fb else "AI recommendation"}</span>
                    <span class="{badge}">{btxt}</span></div>
                  <div class="ai-price">{inr(res['fare'])}</div>
                  <div style="font-size:0.72rem;color:#7c8db5;margin-top:-0.2rem;">{dtxt}</div>
                  <div class="ai-rat">{res['rat']}</div>
                  <div style="margin-top:0.5rem;">
                    <span class="engine-chip" style="background:
                      {'#fef3c7' if is_fb else '#e8f0fe'};border:1px solid
                      {'#D97706' if is_fb else '#2F6FD0'};color:
                      {'#b45309' if is_fb else '#1B2D6B'};">Engine: {eng}</span></div>
                </div>""", unsafe_allow_html=True)
                if is_fb:
                    st.caption("No AI engine reachable — this came from the "
                               "pricing rules alone.")

                b1, b2 = st.columns(2)
                with b1:
                    if st.button("✔  Use this fare", key=f"acc_{k}"):
                        try:
                            commit_decision(res, "Accepted", res["fare"], "")
                            st.session_state.pop(f"res_{k}", None)
                            st.success(f"Set at {inr(res['fare'])}.")
                            st.cache_data.clear(); st.rerun()
                        except Exception as e:
                            st.error(f"Could not save: {e}")
                with b2:
                    ov = st.number_input("My fare (₹)", min_value=500,
                                         max_value=500000, value=int(res["fare"]),
                                         step=100, key=f"ov_{k}")
                    why = st.text_input("Why are you changing it?",
                                        placeholder="e.g. group booking expected",
                                        key=f"why_{k}")
                    if st.button("✏  Use my fare", key=f"ovr_{k}"):
                        if not why.strip():
                            st.error("Please give a short reason.")
                        else:
                            try:
                                commit_decision(res, "Overridden", int(ov),
                                                why.strip())
                                st.session_state.pop(f"res_{k}", None)
                                st.success(f"Set at {inr(ov)}. Reason recorded.")
                                st.cache_data.clear(); st.rerun()
                            except Exception as e:
                                st.error(f"Could not save: {e}")

    if settled:
        with st.expander(f"Already priced today ({len(settled)})"):
            for t in settled:
                st.markdown(f"- **{t['route']}** {t['flight']} {t['time']} · "
                            f"{cabin_short(t['cabin'])} — cleared from the list")


# ═════════════════════════════════════════════════════════════
# TAB 3 — DECISION HISTORY
# ═════════════════════════════════════════════════════════════
def render_history(C):
    ai_log_df, indigo_df = C["ai_log_df"], C["indigo_df"]
    sel_route, dcol = C["sel_route"], C["dcol"]

    st.markdown('<div class="tab-intro">Every fare the AI has reviewed, what it '
                'said, who decided, and whether bookings moved afterwards. '
                'This record is fed back into future recommendations.</div>',
                unsafe_allow_html=True)

    if ai_log_df.empty:
        st.info("Nothing recorded yet. Get your first recommendation on the "
                "Flight dashboard and it will appear here.")
        return

    h = ai_log_df.copy()
    c1, c2 = st.columns(2)
    with c1:
        scope = st.radio("Show", ["This route", "All routes"], index=0,
                         horizontal=True, key="hist_scope")
    with c2:
        outcome = st.selectbox("Outcome", ["All", "Accepted", "Overridden",
                                           "Pending"], key="hist_outcome")
    if scope == "This route" and "Route" in h.columns:
        h = h[h["Route"] == sel_route]
    if outcome != "All" and "Manager Decision" in h.columns:
        h = h[h["Manager Decision"] == outcome]
    if h.empty:
        st.info("Nothing matches this filter.")
        return

    if "Log Date" in h.columns:
        h["_ld"] = pd.to_datetime(h["Log Date"], errors="coerce")
        h["_day"] = h["_ld"].dt.normalize()
        keys = [c for c in ["Flight No.", "Cabin Class", "Departure Date"]
                if c in h.columns]
        if keys:
            h = h.sort_values("_ld").drop_duplicates(subset=keys + ["_day"],
                                                     keep="last")

    def bookings_after(row):
        try:
            fno = str(row.get("Flight No.", ""))
            cb  = str(row.get("Cabin Class", ""))
            dk  = dkey(row.get("Departure Date", ""))
            d0  = pd.to_datetime(row.get("Log Date"), errors="coerce").normalize()
            s0  = pd.to_numeric(row.get("Seats At Decision"), errors="coerce")
            if pd.isna(d0) or pd.isna(s0):
                return None
            obs = indigo_df[(indigo_df["Flight No."].astype(str) == fno) &
                            (indigo_df["Cabin Class"].astype(str) == cb) &
                            (indigo_df["Departure Date"].map(dkey) == dk) &
                            (indigo_df[dcol] > d0) &
                            (indigo_df[dcol] <= d0 + timedelta(days=1))]
            if obs.empty:
                return None
            s1 = pd.to_numeric(obs.sort_values(dcol).iloc[-1]["Seats Sold"],
                               errors="coerce")
            return None if pd.isna(s1) else int(s1 - s0)
        except Exception:
            return None

    h["Seats booked next day"] = h.apply(bookings_after, axis=1)

    mgr   = h.get("Manager Decision", pd.Series(dtype=str))
    n_acc = int((mgr == "Accepted").sum())
    n_ovr = int((mgr == "Overridden").sum())
    n_pen = int((mgr == "Pending").sum())
    rate  = n_acc / max(len(h), 1) * 100
    tracked = h["Seats booked next day"].dropna()
    out_txt = ""
    if len(tracked) > 0:
        out_txt = (f' Of the {len(tracked)} decisions old enough to check, '
                   f'flights picked up <b>{tracked.mean():.1f} seats on average</b> '
                   f'the following day.')
    judge = ("the pricing team largely trusts the AI" if rate >= 70 else
             "the team accepts the AI about half the time" if rate >= 40 else
             "the team is overriding the AI more often than accepting it")
    st.markdown(
        f'<div class="insight"><b>{len(h)} fares reviewed.</b> {n_acc} accepted '
        f'as suggested, {n_ovr} overridden by a manager, {n_pen} awaiting a '
        f'decision. Acceptance rate <span class="big">{rate:.0f}%</span>, which '
        f'suggests {judge}.{out_txt}</div>', unsafe_allow_html=True)

    # Flight number is stored raw and can arrive as a float or long number
    if "Flight No." in h.columns:
        tcol = h["Departure Time"] if "Departure Time" in h.columns else ""
        h["Flight"] = [fno_disp(v, t) for v, t in
                       zip(h["Flight No."],
                           tcol if len(tcol) else [""] * len(h))]
    for c in ("Log Date", "Departure Date"):
        if c in h.columns:
            h[c] = h[c].map(lambda v: dfmt(v, "stamp" if c == "Log Date" else "short"))

    show = [c for c in ["Log Date", "Analyst", "Route", "Flight",
                        "Departure Time", "Cabin Class", "Departure Date",
                        "Load Factor", "Arithmetic Fare", "AI Suggested Fare",
                        "Engine", "Manager Decision", "Final Fare Used",
                        "Manager Notes", "Seats booked next day",
                        "Strategic Direction", "Competitor Snapshot",
                        "AI Rationale"] if c in h.columns]
    st.dataframe(h.sort_values("_ld", ascending=False)[show],
                 use_container_width=True, hide_index=True,
                 column_config={
                     "Log Date": st.column_config.TextColumn("Reviewed on"),
                     "Departure Date": st.column_config.TextColumn("Departs"),
                     "Load Factor": st.column_config.NumberColumn(
                         "% full", format="%.0f%%"),
                     "Arithmetic Fare": st.column_config.NumberColumn(
                         "Our fare", format="₹%d"),
                     "AI Suggested Fare": st.column_config.NumberColumn(
                         "AI said", format="₹%d"),
                     "Final Fare Used": st.column_config.NumberColumn(
                         "Fare used", format="₹%d"),
                     "Manager Notes": st.column_config.TextColumn(
                         "Manager's reason", width="medium"),
                     "AI Rationale": st.column_config.TextColumn(
                         "Why the AI said that", width="large"),
                     "Competitor Snapshot": st.column_config.TextColumn(
                         "Rival fares at the time", width="medium")})
    st.caption("Accepted and overridden rows for the selected route are quoted "
               "back to the AI on its next review, so it learns which of its "
               "advice the team acts on and why.")


# ═════════════════════════════════════════════════════════════
# TAB 4 — BUSINESS CASE
# ═════════════════════════════════════════════════════════════
def render_business_case(C):
    indigo_df, feedback_df = C["indigo_df"], C["feedback_df"]
    sel_route, sel_cabin = C["sel_route"], C["sel_cabin"]
    pace_curve, dcol = C["pace_curve"], C["dcol"]

    st.markdown('<div class="tab-intro">What this system is worth: how much more '
                'revenue our pricing rules would have earned compared with one '
                'flat fare, and the profit on fares already approved.</div>',
                unsafe_allow_html=True)

    st.markdown('<div class="sec-hd">If we had priced this way all along</div>',
                unsafe_allow_html=True)
    scope = st.radio("Test on", ["This route and cabin", "All routes and cabins"],
                     horizontal=True, key="bt_scope")

    bt = indigo_df.copy()
    if scope == "This route and cabin":
        bt = bt[(bt["Route"] == sel_route) & (bt["Cabin Class"] == sel_cabin)]

    if bt.empty or dcol not in bt.columns:
        st.info("Not enough history to test against yet.")
    else:
        bt = bt.dropna(subset=[dcol, "Load Factor",
                               "Days to Departure"]).sort_values(dcol)
        n_routes  = bt["Route"].nunique()
        n_flights = bt.groupby(["Route", "Flight No."]).ngroups
        dep_lo, dep_hi = bt["Departure Date"].min(), bt["Departure Date"].max()
        obs_lo, obs_hi = bt[dcol].min(), bt[dcol].max()

        st.markdown(
            f'<div class="scope-note"><b>Exactly what is being tested:</b> '
            f'{n_flights} flights across {n_routes} route'
            f'{"s" if n_routes != 1 else ""}'
            f'{"" if scope != "This route and cabin" else f" ({sel_route}, {sel_cabin})"}, '
            f'departing between <b>{dfmt(dep_lo)}</b> and <b>{dfmt(dep_hi)}</b>, '
            f'using {len(bt):,} daily readings recorded between '
            f'<b>{dfmt(obs_lo)}</b> and <b>{dfmt(obs_hi)}</b>.</div>',
            unsafe_allow_html=True)

        keys = ["Route", "Flight No.", "Cabin Class", "Departure Date"]
        bt["Seats Sold"] = pd.to_numeric(bt["Seats Sold"], errors="coerce")
        bt["_new"] = (bt.groupby(keys)["Seats Sold"].diff()
                      .fillna(bt["Seats Sold"]).clip(lower=0))

        dyn_rev = flat_rev = 0.0
        seats_n = 0
        per_flight = {}
        for _, g in bt.iterrows():
            new = float(g["_new"] or 0)
            if new <= 0:
                continue
            rt, cb = str(g["Route"]), str(g["Cabin Class"])
            dl = pace_delta_for(pace_curve, rt, cb, int(g["Days to Departure"]),
                                float(g["Load Factor"]))
            v, _ = calc_fare(rt, cb, int(g["Days to Departure"]),
                             float(g["Load Factor"]), 0,
                             str(g.get("Holiday / Festival", "No")) == "Yes",
                             deph(g.get("Departure Time", "10:00")), pace_delta=dl)
            flat = BASE_FARES.get(rt, 5000) * CABIN_MULT.get(cb, 1.0)
            dyn_rev  += v * new
            flat_rev += flat * new
            seats_n  += int(new)
            lbl = (f'{rt} · {fno_disp(g["Flight No."], g.get("Departure Time",""))} '
                   f'· {dfmt(g["Departure Date"])}')
            a, b, s = per_flight.get(lbl, (0.0, 0.0, 0))
            per_flight[lbl] = (a + v * new, b + flat * new, s + int(new))

        if seats_n == 0:
            st.info("No booking movements found in the history to price against.")
        else:
            uplift = dyn_rev - flat_rev
            upct = (uplift / flat_rev * 100) if flat_rev else 0
            word = "more" if uplift >= 0 else "less"
            st.markdown(
                f'<div class="insight">Across <b>{seats_n:,} seats</b> actually '
                f'sold on those flights, charging one flat fare throughout would '
                f'have brought in <b>{inr(flat_rev)}</b>. Pricing each seat by our '
                f'rules — reacting to how full the flight was and how close '
                f'departure had come — would have brought in <b>{inr(dyn_rev)}</b>. '
                f'That is <span class="big">{inr(abs(uplift))} {word}</span>, or '
                f'{abs(upct):.1f}%.</div>', unsafe_allow_html=True)

            b1, b2, b3 = st.columns(3)
            b1.metric("Seats priced", f"{seats_n:,}")
            b2.metric("One flat fare throughout", inr(flat_rev))
            b3.metric("Priced by our rules", inr(dyn_rev), delta=f"{upct:+.1f}%")

            pf = pd.DataFrame(
                [{"Flight and departure date": k,
                  "Seats sold": s,
                  "Flat pricing": round(b),
                  "Our rules": round(a),
                  "Difference": round(a - b)}
                 for k, (a, b, s) in per_flight.items()])
            pf = pf.sort_values("Difference", ascending=False)
            with st.expander(f"Flight-by-flight breakdown ({len(pf)} flights)"):
                st.dataframe(pf, use_container_width=True, hide_index=True,
                             column_config={
                                 "Flat pricing": st.column_config.NumberColumn(
                                     format="₹%d"),
                                 "Our rules": st.column_config.NumberColumn(
                                     format="₹%d"),
                                 "Difference": st.column_config.NumberColumn(
                                     format="₹%d")})
            st.caption("Every booking in the history is re-priced twice: once at "
                       "the flat cabin fare, once at what the rules would have "
                       "charged that day. Competitor reactions are excluded so "
                       "the comparison isolates the demand rules alone.")

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown('<div class="sec-hd">Profit on fares a manager has approved</div>',
                unsafe_allow_html=True)

    if feedback_df.empty or "Manager Decision" not in feedback_df.columns:
        st.info("No decisions recorded yet. Approve a fare and the profit "
                "impact will appear here.")
        return
    acc = feedback_df[feedback_df["Manager Decision"]
                      .isin(["Accepted", "Overridden"])].copy()
    if acc.empty:
        st.info("No approved fares yet.")
        return

    acc["Final Fare Used"] = pd.to_numeric(acc["Final Fare Used"], errors="coerce")
    acc["_cost"] = acc.apply(lambda r: seat_cost(str(r.get("Route", "")),
                                                 str(r.get("Cabin Class", "Economy"))),
                             axis=1)
    acc["Profit per seat"] = acc["Final Fare Used"] - acc["_cost"]
    acc["_lf"] = pd.to_numeric(acc["Load Factor"], errors="coerce") / 100
    acc["_seats"] = acc["Route"].map(TOTAL_SEATS_MAP).fillna(180)
    acc["Profit for the flight"] = acc["Profit per seat"] * acc["_seats"] * acc["_lf"]
    acc["_base"] = acc.apply(
        lambda r: BASE_FARES.get(str(r.get("Route", "")), 5000)
                  * CABIN_MULT.get(str(r.get("Cabin Class", "Economy")), 1.0), axis=1)
    acc["Extra vs flat fare"] = ((acc["Final Fare Used"] - acc["_base"])
                                 * acc["_seats"] * acc["_lf"])

    if "Departure Date" in acc.columns:
        dd = pd.to_datetime(acc["Departure Date"], errors="coerce")
        span = (f'departing between <b>{dfmt(dd.min())}</b> and '
                f'<b>{dfmt(dd.max())}</b>') if dd.notna().any() else ""
    else:
        span = ""
    st.markdown(
        f'<div class="scope-note"><b>Exactly what is counted:</b> the '
        f'{len(acc)} fares a manager has accepted or overridden across '
        f'{acc["Route"].nunique()} routes, {span}. Profit assumes the flight '
        f'sells out at the approved fare and the load factor recorded at the '
        f'time of the decision.</div>', unsafe_allow_html=True)

    st.markdown(
        f'<div class="insight">Those <b>{len(acc)} approved fares</b> are worth '
        f'<span class="big">{inr(acc["Extra vs flat fare"].sum())}</span> in '
        f'extra revenue versus charging the standard cabin fare, at an average '
        f'profit of <b>{inr(acc["Profit per seat"].mean())} per seat</b>.</div>',
        unsafe_allow_html=True)

    p1, p2, p3, p4 = st.columns(4)
    p1.metric("Fares approved", len(acc))
    p2.metric("Extra vs flat fare", inr(acc["Extra vs flat fare"].sum()))
    p3.metric("Average profit per seat", inr(acc["Profit per seat"].mean()))
    p4.metric("Total flight profit", inr(acc["Profit for the flight"].sum()))

    st.markdown("<br>", unsafe_allow_html=True)
    rp = (acc.groupby("Route")["Profit for the flight"].sum()
          .reset_index().sort_values("Profit for the flight"))
    fig = go.Figure(go.Bar(
        x=rp["Profit for the flight"], y=rp["Route"], orientation="h",
        marker_color=[GREEN if x > 0 else RED for x in rp["Profit for the flight"]],
        text=[inr(x) for x in rp["Profit for the flight"]],
        textposition="outside", textfont=dict(size=11)))
    fig.update_xaxes(title_text="Estimated profit for the flight (₹)")
    style_chart(fig, height=210, legend=False)
    fig.update_layout(margin=dict(l=10, r=95, t=10, b=10))
    st.plotly_chart(fig, use_container_width=True)
    st.caption(f"A Business seat is costed at {CABIN_COST_MULT['Business']:.1f} "
               "times an economy seat, because it takes up that much more of "
               "the aircraft.")

    if "Flight No." in acc.columns:
        tc = acc["Departure Time"] if "Departure Time" in acc.columns else ""
        acc["Flight"] = [fno_disp(v, t) for v, t in
                         zip(acc["Flight No."],
                             tc if len(tc) else [""] * len(acc))]
    for c in ("Timestamp", "Departure Date"):
        if c in acc.columns:
            acc[c] = acc[c].map(
                lambda v: dfmt(v, "stamp" if c == "Timestamp" else "short"))
    cols = [c for c in ["Timestamp", "Analyst", "Route", "Flight",
                        "Departure Time", "Departure Date", "Cabin Class",
                        "Load Factor", "Arithmetic Fare", "AI Suggested Fare",
                        "Final Fare Used", "Manager Decision", "Manager Notes",
                        "Profit per seat", "Profit for the flight"]
            if c in acc.columns]
    st.dataframe(acc[cols], use_container_width=True, hide_index=True,
                 column_config={
                     "Timestamp": st.column_config.TextColumn("Decided on"),
                     "Departure Date": st.column_config.TextColumn("Departs"),
                     "Profit per seat": st.column_config.NumberColumn(format="₹%d"),
                     "Profit for the flight": st.column_config.NumberColumn(
                         format="₹%d")})

    st.markdown("""<div style="margin-top:1.6rem;padding:0.6rem 0;
      border-top:1px solid #dde3f0;text-align:center;font-size:0.6rem;
      color:#8095bd;letter-spacing:0.08em;">
      IndiGo Pricing Intelligence · Team 5 · ISB Action Learning Project 2026 · Confidential
    </div>""", unsafe_allow_html=True)


if __name__ == "__main__":
    main()
