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
              "Manager Decision", "Final Fare Used"]

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
    try:
        t = pd.to_datetime(v, errors="coerce")
        if pd.isna(t):
            return str(v)[:10]
        if style == "long":  return t.strftime("%A, %d-%m-%Y")
        if style == "day":   return t.strftime("%d %b")
        if style == "month": return t.strftime("%b %Y")
        if style == "stamp": return t.strftime("%d-%m-%Y %H:%M")
        return t.strftime("%d-%m-%Y")
    except Exception:
        return str(v)[:10]


def fno_disp(v, dep_time=""):
    s = str(v).strip()
    if s.endswith(".0"):
        s = s[:-2]
    if s in ("", "nan", "None", "0") or s.replace(".", "").replace("e+", "").isdigit():
        return f"dep {dep_time}" if dep_time else "—"
    return s


def cabin_short(c):
    return {"Premium Economy": "Prem Econ"}.get(str(c), str(c))


def fill_speed_words(delta):
    if delta is None or (isinstance(delta, float) and pd.isna(delta)):
        return "No history yet", "sp-none"
    if delta >= 0.10:  return "Much faster than usual", "sp-fast"
    if delta >= 0.05:  return "Faster than usual", "sp-fast"
    if delta <= -0.10: return "Much slower than usual", "sp-slow"
    if delta <= -0.05: return "Slower than usual", "sp-slow"
    return "About normal", "sp-norm"


def speed_band(delta):
    if delta is None or (isinstance(delta, float) and pd.isna(delta)):
        return "No history"
    if delta >= 0.05:  return "Faster than usual"
    if delta <= -0.05: return "Slower than usual"
    return "About normal"


def fill_band(lf):
    if lf > 0.85: return "Nearly full (over 85%)"
    if lf > 0.70: return "Filling well (70-85%)"
    if lf > 0.40: return "Half empty (40-70%)"
    return "Very empty (under 40%)"


FILL_BANDS   = ["Nearly full (over 85%)", "Filling well (70-85%)",
                "Half empty (40-70%)", "Very empty (under 40%)"]
SPEED_BANDS  = ["Faster than usual", "About normal", "Slower than usual",
                "No history"]
STATUS_BANDS = ["Needs action", "Watch", "Priced sensibly", "Priced today"]

ALL_TOKEN = "✓  Select all"


def slicer(label, options, key, help_text=None):
    """Searchable multi-select with a select-all option.

    Streamlit's multiselect already filters as you type; the extra token gives
    a one-click way back to everything, which a plain multiselect lacks.
    Selecting nothing is treated as selecting everything, so the page never
    goes blank by accident.
    """
    options = list(options)
    if not options:
        return []
    opts = [ALL_TOKEN] + options
    if key not in st.session_state:
        st.session_state[key] = [ALL_TOKEN]
    sel = st.multiselect(label, opts, key=key, help=help_text,
                         placeholder="Type to search…")
    if not sel or ALL_TOKEN in sel:
        return list(options)
    return [s for s in sel if s in options]


def sku_key(route, flight_raw, dep_time, cabin, dep_date):
    """Unique id for one flight, cabin and departure date. Route and time are
    included because several sheet rows carry a blank or numeric flight
    number, which would otherwise collapse different flights onto one id."""
    parts = [str(route), str(flight_raw), str(dep_time), str(cabin),
             dkey(dep_date)]
    joined = "|".join(p.strip() for p in parts)
    return "".join(ch if (ch.isalnum() or ch in "|-") else "_" for ch in joined)


FARE_SRC = {
    "arith":   ("#E91E8C", "Rules only",   ""),
    "ai":      ("#2F6FD0", "AI, accepted", "●"),
    "manager": ("#D97706", "Manager set",  "◆"),
}


def effective_fare(decided, key, arithmetic):
    """The fare actually in force today. A manager decision overrides the
    rules for the rest of the day, so every page shows the same number."""
    hit = decided.get(key)
    if not hit:
        return arithmetic, "arith"
    return hit[0], hit[1]


def fare_dot(src):
    return (f'<span class="fdot" style="background:{FARE_SRC[src][0]}" '
            f'title="{FARE_SRC[src][1]}"></span>')


def fare_legend():
    bits = " &nbsp; ".join(
        f'<span class="fdot" style="background:{c}"></span>{lbl}'
        + (f' <b>{mark}</b>' if mark else '')
        for c, lbl, mark in FARE_SRC.values())
    return ('<div class="legend-row"><b>Fare source:</b> &nbsp;' + bits +
            " &nbsp;·&nbsp; a manager's decision applies for the rest of "
            "today and shows on every page</div>")


def advice_line(t):
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


def goto_flight(t):
    st.session_state["nav"] = "✈️  Flight detail"
    st.session_state["fd_route"]  = t["route"]
    st.session_state["fd_cabin"]  = t["cabin"]
    st.session_state["fd_flight"] = t["key"]


# ═════════════════════════════════════════════════════════════
# SHARED INLINE PRICING PANEL
# Used by the attention list and by the network grid, so a fare can be set
# from wherever the manager happens to spot the problem.
# ═════════════════════════════════════════════════════════════
def price_panel(t, C, kp):
    ai_log_df, analyst = C["ai_log_df"], C["analyst"]
    pax_type, trip_type, standing = C["pax_type"], C["trip_type"], C["standing"]
    k = f"{kp}_{t['key']}"

    if not (analyst or "").strip():
        st.warning("Enter your name in the sidebar before pricing — every "
                   "decision is recorded against whoever made it.")

    m1, m2 = st.columns([1.1, 1])
    with m1:
        rdef = standing.get(t["route"], STRATEGIC_OPTIONS[0])
        ridx = STRATEGIC_OPTIONS.index(rdef) if rdef in STRATEGIC_OPTIONS else 0
        strat = st.selectbox("Pricing goal", STRATEGIC_OPTIONS, index=ridx,
                             key=f"st_{k}")
    with m2:
        st.markdown("<div style='height:1.65rem'></div>", unsafe_allow_html=True)
        go = st.button("🤖  Get AI recommendation", key=f"ai_{k}")

    if go:
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
                     if pd.notna(c.get("Fare (INR)"))] if len(t["comp_rows"]) else []
            snap = "; ".join(f"{a} {fn} {ft} {inr(fa)}"
                             for a, fn, ft, fa in clist) or "none"
            pargs = dict(route=t["route"], flight_no=t["flight"],
                         dep_time=t["time"], cabin=t["cabin"],
                         dep_date=dfmt(t["dep"]), days_to_dep=t["dout"],
                         load_factor=t["lf"], pace_delta=t["pace"],
                         arithmetic_fare=t["arith"], bd=t["bd"],
                         comp_match=t["comp"], comp_all=clist,
                         strategy=strat, history=hist,
                         pax_type=pax_type, trip_type=trip_type)
            with st.spinner("Asking the AI pricing analyst..."):
                dec, fare, rat, engine, note = call_llm(
                    pargs, t["bd"], t["arith"], t["comp"], t["lf"])
            try:
                save_ai_log({
                    "Log Date": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "Analyst": analyst, "Route": t["route"],
                    "Flight No.": t["raw"], "Departure Time": t["time"],
                    "Departure Date": dkey(t["dep"]), "Cabin Class": t["cabin"],
                    "Days to Departure": t["dout"],
                    "Load Factor": round(t["lf"] * 100, 1),
                    "Seats At Decision": t["sold"], "Arithmetic Fare": t["arith"],
                    "AI Decision": dec, "AI Suggested Fare": fare,
                    "AI Rationale": rat, "Engine": engine,
                    "Competitor Snapshot": snap, "Strategic Direction": strat,
                    "Manager Decision": "Pending", "Final Fare Used": ""})
            except Exception as e:
                st.warning(f"Received but not logged: {e}")
            st.session_state[f"res_{t['key']}"] = {
                "dec": dec, "fare": fare, "rat": rat, "arith": t["arith"],
                "route": t["route"], "flight_raw": t["raw"], "time": t["time"],
                "date": dkey(t["dep"]), "cabin": t["cabin"], "days": t["dout"],
                "lf": t["lf"], "sold": t["sold"], "strategy": strat,
                "engine": engine, "snapshot": snap, "analyst": analyst,
                "pax": pax_type, "trip": trip_type}
            st.rerun()

    res = st.session_state.get(f"res_{t['key']}")
    if not res:
        st.caption(f"Rules fare {inr(t['arith'])}"
                   + (f" · in force {inr(t['fare'])} "
                      f"({FARE_SRC[t['fsrc']][1].lower()})"
                      if t["fsrc"] != "arith" else "")
                   + ". Ask the AI, or set your own price after it replies.")
        return

    ok    = str(res["dec"]).lower().startswith("approve")
    is_fb = res.get("engine", "").startswith("Rules")
    d     = res["fare"] - res["arith"]
    dtxt  = ("same as our rules" if d == 0 else
             f'{inr(abs(d))} {"higher" if d > 0 else "lower"} than our rules')
    st.markdown(f"""
    <div class="ai-result">
      <div style="display:flex;align-items:center;justify-content:space-between;">
        <span style="font-size:0.6rem;font-weight:700;color:#1B2D6B;
              text-transform:uppercase;letter-spacing:0.1em;">
          {"Rules-based suggestion" if is_fb else "AI recommendation"}</span>
        <span class="{'ai-badge-ok' if ok else 'ai-badge-ov'}">
          {"✔ Agrees with our fare" if ok else "⚡ Suggests different"}</span></div>
      <div class="ai-price">{inr(res['fare'])}</div>
      <div style="font-size:0.72rem;color:#7c8db5;margin-top:-0.2rem;">{dtxt}</div>
      <div class="ai-rat">{res['rat']}</div>
      <div style="margin-top:0.5rem;">
        <span class="engine-chip" style="background:
          {'#fef3c7' if is_fb else '#e8f0fe'};border:1px solid
          {'#D97706' if is_fb else '#2F6FD0'};color:
          {'#b45309' if is_fb else '#1B2D6B'};">
          Engine: {res.get('engine','')}</span></div>
    </div>""", unsafe_allow_html=True)
    if is_fb:
        st.caption("No AI engine reachable — this came from the pricing rules "
                   "alone. It is not an AI recommendation.")

    b1, b2 = st.columns(2)
    with b1:
        if st.button("✔  Use this fare", key=f"acc_{k}"):
            try:
                commit_decision(res, "Accepted", res["fare"], "")
                st.session_state.pop(f"res_{t['key']}", None)
                st.success(f"Set at {inr(res['fare'])}.")
                st.cache_data.clear(); st.rerun()
            except Exception as e:
                st.error(f"Could not save: {e}")
    with b2:
        ov = st.number_input("My fare (₹)", min_value=500, max_value=500000,
                             value=int(res["fare"]), step=100, key=f"ov_{k}")
        why = st.text_input("Why are you changing it?",
                            placeholder="e.g. group booking expected",
                            key=f"why_{k}")
        if st.button("✏  Use my fare", key=f"ovr_{k}"):
            if not why.strip():
                st.error("Please give a short reason.")
            else:
                try:
                    commit_decision(res, "Overridden", int(ov), why.strip())
                    st.session_state.pop(f"res_{t['key']}", None)
                    st.success(f"Set at {inr(ov)}. Reason recorded.")
                    st.cache_data.clear(); st.rerun()
                except Exception as e:
                    st.error(f"Could not save: {e}")


st.markdown("""
<style>
/* Navigation: hide the radio dots so it reads as a tab bar */
.navbar div[role="radiogroup"] { gap:0.4rem; }
.navbar div[role="radiogroup"] > label { background:#eef2fa !important;
  border:1px solid #dde3f0 !important; border-radius:9px !important;
  padding:0.45rem 1rem !important; font-size:0.82rem !important;
  font-weight:600 !important; color:#5a6f9c !important; }
.navbar div[role="radiogroup"] > label:has(input:checked) {
  background:#1B2D6B !important; color:#fff !important;
  border-color:#1B2D6B !important; }
.navbar div[role="radiogroup"] > label > div:first-child { display:none !important; }

/* Multiselect tags in brand navy rather than the default red */
span[data-baseweb="tag"] { background-color:#1B2D6B !important;
  border-radius:6px !important; }
span[data-baseweb="tag"] span { color:#fff !important; font-size:0.72rem !important; }

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

/* One combined action table: header strip plus per-row cells */
.rowhead { display:grid; gap:0.5rem; padding:0.45rem 0.7rem;
  background:#f2f5fc; border:1px solid #dde3f0; border-radius:9px 9px 0 0;
  font-size:0.56rem; font-weight:700; letter-spacing:0.07em;
  text-transform:uppercase; color:#1B2D6B; }
.cell { font-size:0.74rem; color:#2a4060; line-height:1.45; }
.cell .mono { font-family:'DM Mono',monospace; }
.cell .sub { font-size:0.66rem; color:#8095bd; }
.rowadvice { font-size:0.72rem; color:#3a5080; line-height:1.5;
  border-top:1px dashed #e6ebf7; padding-top:0.4rem; margin-top:0.15rem; }

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
.fdot { display:inline-block; width:8px; height:8px; border-radius:50%;
  margin-right:5px; vertical-align:middle; }
.dl-up { color:#DC2626; font-weight:700; }
.dl-dn { color:#16A34A; font-weight:700; }
.dl-fl { color:#8095bd; }
tr.date-sep td { background:#eaf0fb !important; color:#1B2D6B !important;
  font-weight:700 !important; font-size:0.72rem !important;
  padding:0.35rem 0.6rem !important; border-top:2px solid #c9d6f0 !important; }
.scope-note { background:#fff8e6; border:1px solid #f0d9a0; border-radius:8px;
  padding:0.55rem 0.85rem; font-size:0.73rem; color:#7a5c14; margin-bottom:0.8rem; }
.filt-tag { display:inline-block; background:#eaf0fb; border:1px solid #c9d6f0;
  color:#1B2D6B; border-radius:20px; padding:0.12rem 0.6rem; font-size:0.66rem;
  font-weight:600; margin:0 0.3rem 0.3rem 0; }
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
    dcol  = "Date" if "Date" in indigo_df.columns else "Scrape Date"
    pace_curve = booking_pace_curve(indigo_df)

    standing = {}
    if not strategy_df.empty and "Route" in strategy_df.columns:
        for _, s in strategy_df.iterrows():
            standing[str(s.get("Route", ""))] = str(s.get("Strategic Direction", ""))

    all_routes = sorted(indigo_df["Route"].dropna().unique().tolist())
    all_cabins = sorted(indigo_df["Cabin Class"].dropna().unique().tolist())
    all_slots  = sorted(indigo_df["Time Slot"].dropna().astype(str).unique().tolist()) \
                 if "Time Slot" in indigo_df.columns else []

    dep_min = pd.to_datetime(indigo_df["Departure Date"]).min()
    dep_max = pd.to_datetime(indigo_df["Departure Date"]).max()
    lo_default = max(today, dep_min) if pd.notna(dep_min) else today
    hi_default = min(lo_default + timedelta(days=29),
                     dep_max if pd.notna(dep_max) else lo_default + timedelta(days=29))

    # ── SIDEBAR: GLOBAL SLICERS ──────────────────────────────
    with st.sidebar:
        st.markdown('<div class="sb-brand">'
                    '<span style="color:#E91E8C;font-weight:800">6E</span>'
                    '&nbsp; IndiGo · Pricing Intelligence</div>', unsafe_allow_html=True)

        analyst = st.text_input("Analyst name",
                                value=st.session_state.get("analyst", ""),
                                placeholder="Who is making decisions?")
        st.session_state["analyst"] = analyst

        st.markdown("**Filters** — these apply to every page")

        grain = st.selectbox("Look at", ["A date range", "A single day",
                                         "A whole month"], key="g_grain")
        if grain == "A single day":
            d1 = st.date_input("Departure date", value=lo_default.date(),
                               min_value=dep_min.date(), max_value=dep_max.date(),
                               format="DD-MM-YYYY", key="g_d1")
            d_from = d_to = pd.Timestamp(d1)
        elif grain == "A whole month":
            months  = sorted(pd.to_datetime(indigo_df["Departure Date"])
                             .dt.to_period("M").dropna().unique())
            mlabels = [str(m) for m in months]
            mdisp   = [pd.Period(m).to_timestamp().strftime("%B %Y") for m in mlabels]
            pick_m  = st.selectbox("Month", mdisp, key="g_month")
            per = pd.Period(mlabels[mdisp.index(pick_m)])
            d_from = per.to_timestamp()
            d_to   = per.to_timestamp(how="end").normalize()
        else:
            c1, c2 = st.columns(2)
            with c1:
                a = st.date_input("From", value=lo_default.date(),
                                  min_value=dep_min.date(), max_value=dep_max.date(),
                                  format="DD-MM-YYYY", key="g_from")
            with c2:
                b = st.date_input("To", value=hi_default.date(),
                                  min_value=dep_min.date(), max_value=dep_max.date(),
                                  format="DD-MM-YYYY", key="g_to")
            d_from, d_to = pd.Timestamp(a), pd.Timestamp(b)
            if d_from > d_to:
                d_from, d_to = d_to, d_from

        f_routes = slicer("Routes", all_routes, "g_routes")
        f_cabins = slicer("Cabins", all_cabins, "g_cabins")
        f_slots  = slicer("Time of day", all_slots, "g_slots") if all_slots else []
        f_fill   = slicer("How full", FILL_BANDS, "g_fill")
        f_speed  = slicer("Selling speed", SPEED_BANDS, "g_speed")
        f_status = slicer("Status", STATUS_BANDS, "g_status")

        st.markdown("---")
        st.caption("Quoting options — these change the fare shown on Flight "
                   "detail only, not the network views")
        pax_type  = st.selectbox("Passenger type",
                                 ["Adult", "Corporate", "Student",
                                  "Senior Citizen", "Child"], key="g_pax")
        trip_type = st.selectbox("Trip type", ["One Way", "Round Trip"],
                                 key="g_trip")

        st.markdown("---")
        if st.button("↻  Refresh data"):
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

    # ── SNAPSHOT + COMPETITOR INDEX ──────────────────────────
    snap = indigo_df[(indigo_df["Departure Date"] >= d_from) &
                     (indigo_df["Departure Date"] <= d_to) &
                     (indigo_df["Route"].isin(f_routes)) &
                     (indigo_df["Cabin Class"].isin(f_cabins))].copy()
    if not snap.empty and dcol in snap.columns:
        snap = (snap.sort_values(dcol)
                .groupby(["Route", "Flight No.", "Cabin Class", "Departure Date"],
                         as_index=False).last())

    gk = ["Airline", "Flight No.", "Route", "Cabin Class", "Departure Date"]
    cwin = comp_df[(comp_df["Departure Date"] >= d_from) &
                   (comp_df["Departure Date"] <= d_to)].copy()
    comp_latest, comp_prev = pd.DataFrame(), pd.DataFrame()
    if not cwin.empty and "Scrape Date" in cwin.columns:
        comp_latest = (cwin.sort_values("Scrape Date")
                       .groupby(gk, as_index=False).last())
        pool = cwin.merge(comp_latest[gk + ["Scrape Date"]]
                          .rename(columns={"Scrape Date": "_latest"}),
                          on=gk, how="left")
        pool = pool[pool["Scrape Date"] < pool["_latest"]]
        if not pool.empty:
            comp_prev = (pool.sort_values("Scrape Date")
                         .groupby(gk, as_index=False).last())

    comp_idx, prev_idx = {}, {}
    if not comp_latest.empty:
        for (rt, cb, dd), grp in comp_latest.groupby(
                ["Route", "Cabin Class", "Departure Date"]):
            comp_idx[(rt, cb, dkey(dd))] = grp
    if not comp_prev.empty:
        for _, x in comp_prev.iterrows():
            prev_idx[(x["Airline"], str(x["Flight No."]), x["Route"],
                      x["Cabin Class"], dkey(x["Departure Date"]))] = x["Fare (INR)"]

    decided_today, decided_fares = set(), {}
    if not feedback_df.empty and "Timestamp" in feedback_df.columns:
        fbt = feedback_df.copy()
        fbt["_ts"] = pd.to_datetime(fbt["Timestamp"], errors="coerce")
        fbt = fbt[(fbt["_ts"] >= today) &
                  (fbt.get("Manager Decision", pd.Series(dtype=str))
                   .isin(["Accepted", "Overridden"]))].sort_values("_ts")
        for _, x in fbt.iterrows():
            k = sku_key(x.get("Route", ""), x.get("Flight No.", ""),
                        x.get("Departure Time", ""), x.get("Cabin Class", ""),
                        x.get("Departure Date", ""))
            decided_today.add(k)
            try:
                fv = int(round(float(x.get("Final Fare Used", 0))))
            except Exception:
                continue
            if fv > 0:
                decided_fares[k] = (
                    fv,
                    "manager" if x.get("Manager Decision") == "Overridden" else "ai",
                    str(x.get("Manager Notes", "") or ""))

    # ── SKU UNIVERSE ─────────────────────────────────────────
    skus = []
    for _, r in snap.iterrows():
        rt, cb, dd = str(r["Route"]), str(r["Cabin Class"]), r["Departure Date"]
        ftm  = str(r.get("Departure Time", ""))
        raw  = str(r.get("Flight No.", ""))
        slot = str(r.get("Time Slot", ""))
        lf   = float(r.get("Load Factor", 0) or 0)
        tot  = int(r.get("Total Seats", TOTAL_SEATS_MAP.get(rt, 180)) or 180)
        sold = int(r.get("Seats Sold", 0) or 0)
        if sold <= 0 and lf > 0:
            sold = int(round(lf * tot))
        dout = int(r.get("Days to Departure", 30) or 30)
        hol  = str(r.get("Holiday / Festival", "No")) == "Yes"

        cm    = comp_idx.get((rt, cb, dkey(dd)), pd.DataFrame())
        match = match_competitor(cm, deph(ftm))
        pdlt  = pace_delta_for(pace_curve, rt, cb, dout, lf)
        arith_f, bdx = calc_fare(rt, cb, dout, lf, match["fare"] if match else 0,
                                 hol, deph(ftm), pace_delta=pdlt)
        key = sku_key(rt, raw, ftm, cb, dd)
        fare, fsrc = effective_fare(decided_fares, key, arith_f)

        gap  = (fare - match["fare"]) / match["fare"] if (match and match["fare"]) else None
        remaining = max(tot - sold, 0)
        risk = abs(fare - match["fare"]) * remaining if match else 0

        move = move_pc = None
        if match:
            pk = (match["airline"], str(match["flight"]), rt, cb, dkey(dd))
            if pk in prev_idx:
                try:
                    move = float(match["fare"]) - float(prev_idx[pk])
                    move_pc = move / match["fare"] if match["fare"] else None
                except Exception:
                    move = move_pc = None

        flag = "green"
        if gap is not None:
            if (abs(gap) > 0.15 and dout <= 7) or \
               (move_pc is not None and abs(move_pc) > 0.10):
                flag = "red"
            elif abs(gap) > 0.08 or (move_pc is not None and abs(move_pc) > 0.05):
                flag = "amber"

        settled = key in decided_today
        status = ("Priced today" if settled else
                  "Needs action" if flag == "red" else
                  "Watch" if flag == "amber" else "Priced sensibly")

        skus.append({
            "key": key, "route": rt, "raw": raw, "flight": fno_disp(raw, ftm),
            "time": ftm, "slot": slot, "cabin": cb, "dep": dd, "dout": dout,
            "lf": lf, "sold": sold, "total": tot, "remaining": remaining,
            "arith": arith_f, "fare": fare, "fsrc": fsrc, "bd": bdx,
            "comp": match, "comp_rows": cm, "gap": gap, "move": move,
            "move_pc": move_pc, "risk": risk, "flag": flag, "pace": pdlt,
            "holiday": hol, "settled": settled, "status": status,
            "fill_band": fill_band(lf), "speed_band": speed_band(pdlt)})

    if f_slots:
        skus = [s for s in skus if (s["slot"] in f_slots or not s["slot"])]
    skus = [s for s in skus if s["fill_band"]  in f_fill]
    skus = [s for s in skus if s["speed_band"] in f_speed]
    skus = [s for s in skus if s["status"]     in f_status]
    skus.sort(key=lambda x: -x["risk"])

    ctx = dict(comp_df=comp_df, indigo_df=indigo_df, feedback_df=feedback_df,
               ai_log_df=ai_log_df, skus=skus, comp_latest=comp_latest,
               comp_idx=comp_idx, pace_curve=pace_curve, standing=standing,
               today=today, d_from=d_from, d_to=d_to, dcol=dcol, grain=grain,
               pax_type=pax_type, trip_type=trip_type, analyst=analyst,
               decided_today=decided_today, decided_fares=decided_fares,
               all_routes=all_routes, all_cabins=all_cabins,
               f_routes=f_routes, f_cabins=f_cabins)

    if d_from == d_to:
        span = dfmt(d_from)
    elif grain == "A whole month":
        span = dfmt(d_from, "month")
    else:
        span = f"{dfmt(d_from)} to {dfmt(d_to)}"

    n_open = sum(1 for s in skus if s["flag"] != "green" and not s["settled"])
    n_set  = sum(1 for s in skus if s["settled"])
    n_rev  = 0
    if not ai_log_df.empty and "Log Date" in ai_log_df.columns:
        _t = ai_log_df.copy()
        _t["_ld"] = pd.to_datetime(_t["Log Date"], errors="coerce")
        _t = _t[_t["_ld"] >= today]
        if not _t.empty:
            kc = [c for c in ["Flight No.", "Cabin Class", "Departure Date"]
                  if c in _t.columns]
            n_rev = int(_t.drop_duplicates(subset=kc).shape[0]) if kc else len(_t)
    sh, sm = divmod(n_rev * MANUAL_MINUTES_PER_SKU, 60)

    st.markdown(f"""
    <div class="pid-hdr">
      <div>
        <div class="pid-title">
          <span style="color:#ffd9ee;font-weight:800">6E</span>
          &nbsp;IndiGo Pricing Intelligence</div>
        <div class="pid-sub">Competitor fare monitor · AI pricing adviser · ISB ALP 2026</div>
      </div>
      <div class="pid-ctx">
        <div><div class="pid-ctx-val">{span}</div>
             <div class="pid-ctx-lbl">Departures in view</div></div>
        <div class="pid-div"></div>
        <div><div class="pid-ctx-val">{len(skus)}</div>
             <div class="pid-ctx-lbl">Fares in scope</div></div>
        <div class="pid-div"></div>
        <div><div class="pid-ctx-val" style="color:{'#ffd9ee' if n_open else '#8affc0'}">{n_open}</div>
             <div class="pid-ctx-lbl">Need attention</div></div>
        <div class="pid-div"></div>
        <div><div class="pid-ctx-val">{n_set}</div>
             <div class="pid-ctx-lbl">Priced today</div></div>
        <div class="pid-div"></div>
        <div><div class="pid-ctx-val">{sh}h {sm}m</div>
             <div class="pid-ctx-lbl">Analyst time saved</div></div>
        <div class="live-pill"><div class="live-dot"></div>LIVE</div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    pages = ["🌐  Network overview", "✈️  Flight detail",
             "🚩  Needs attention", "🕘  Decision history", "💰  Business case"]
    if st.session_state.get("nav") not in pages:
        st.session_state["nav"] = pages[0]
    st.markdown('<div class="navbar">', unsafe_allow_html=True)
    page = st.radio("Page", pages, key="nav", horizontal=True,
                    label_visibility="collapsed")
    st.markdown('</div>', unsafe_allow_html=True)

    chips = [f"{len(f_routes)} of {len(all_routes)} routes",
             f"{len(f_cabins)} of {len(all_cabins)} cabins"]
    if len(f_fill)   < len(FILL_BANDS):   chips.append("how full filtered")
    if len(f_speed)  < len(SPEED_BANDS):  chips.append("speed filtered")
    if len(f_status) < len(STATUS_BANDS): chips.append("status filtered")
    st.markdown('<div style="margin:0.3rem 0 0.7rem">'
                + "".join(f'<span class="filt-tag">{c}</span>' for c in chips)
                + f'<span class="filt-tag">{span}</span></div>',
                unsafe_allow_html=True)

    if not skus and page in pages[:3]:
        st.info("No flights match the current filters. Widen them in the sidebar.")
        return

    if   page == pages[0]: render_overview(ctx)
    elif page == pages[1]: render_flight_detail(ctx)
    elif page == pages[2]: render_action_list(ctx)
    elif page == pages[3]: render_history(ctx)
    else:                  render_business_case(ctx)


# ═════════════════════════════════════════════════════════════
# PAGE 1 — NETWORK OVERVIEW
# ═════════════════════════════════════════════════════════════
def render_overview(C):
    skus, today = C["skus"], C["today"]

    st.markdown('<div class="tab-intro">The whole network at the level your '
                'filters describe. Nothing here is about one flight — use '
                '<b>Flight detail</b> for that, or price straight from the '
                'grid below.</div>', unsafe_allow_html=True)

    n          = len(skus)
    avg_full   = np.mean([s["lf"] for s in skus]) if skus else 0
    seats_tot  = sum(s["total"] for s in skus)
    seats_sold = sum(s["sold"] for s in skus)
    unsold     = seats_tot - seats_sold
    n_red      = sum(1 for s in skus if s["flag"] == "red" and not s["settled"])
    n_amb      = sum(1 for s in skus if s["flag"] == "amber" and not s["settled"])
    n_set      = sum(1 for s in skus if s["settled"])
    risk       = sum(s["risk"] for s in skus if not s["settled"])
    gaps       = [s["gap"] for s in skus if s["gap"] is not None]
    avg_gap    = np.mean(gaps) if gaps else None
    slow       = sum(1 for s in skus if s["pace"] is not None and s["pace"] <= -0.05)
    revenue    = sum(s["fare"] * s["sold"] for s in skus)

    worst = max((s for s in skus if not s["settled"]),
                key=lambda x: x["risk"], default=None)
    lead = ""
    if worst and worst["comp"]:
        lead = (f' The single biggest exposure is <b>{worst["route"]}</b> '
                f'{worst["flight"]} on <b>{dfmt(worst["dep"])}</b> '
                f'({cabin_short(worst["cabin"])}), where '
                f'<span class="big">{inr(worst["risk"])}</span> rides on the '
                f'price being right.')
    gap_txt = ("in line with rivals overall" if avg_gap is None or abs(avg_gap) < 0.03
               else f'on average <b>{abs(avg_gap)*100:.0f}% '
                    f'{"above" if avg_gap > 0 else "below"}</b> the closest rival')
    st.markdown(
        f'<div class="insight">Across <b>{n} fares</b> in scope, flights are '
        f'<b>{avg_full*100:.0f}% full</b> with <b>{unsold:,} seats</b> still to '
        f'sell, and we are {gap_txt}. <b>{n_red} need action</b> and {n_amb} are '
        f'worth watching; {n_set} have already been priced today.{lead}</div>',
        unsafe_allow_html=True)

    st.markdown(f"""
    <div class="kpi-strip six">
      <div class="kpi-card">
        <div class="kpi-val k-navy">{n}</div>
        <div class="kpi-lbl">Fares in scope</div>
        <div class="kpi-sub">{len({s["route"] for s in skus})} routes ·
          {len({dkey(s["dep"]) for s in skus})} departure days</div></div>
      <div class="kpi-card">
        <div class="kpi-val {lf_kpi(avg_full)}">{avg_full*100:.0f}%</div>
        <div class="kpi-lbl">Average fullness</div>
        <div class="kpi-sub">{seats_sold:,} of {seats_tot:,} seats sold</div></div>
      <div class="kpi-card">
        <div class="kpi-val k-red">{n_red}</div>
        <div class="kpi-lbl">Need action</div>
        <div class="kpi-sub">{n_amb} more to watch</div></div>
      <div class="kpi-card accent">
        <div class="kpi-val k-mag">{inr(risk)}</div>
        <div class="kpi-lbl">Revenue at stake</div>
        <div class="kpi-sub">price gap × unsold seats</div></div>
      <div class="kpi-card">
        <div class="kpi-val {'k-amber' if slow else 'k-green'}">{slow}</div>
        <div class="kpi-lbl">Selling slower than usual</div>
        <div class="kpi-sub">of {n} fares in scope</div></div>
      <div class="kpi-card">
        <div class="kpi-val k-navy">{inr(revenue)}</div>
        <div class="kpi-lbl">Revenue booked so far</div>
        <div class="kpi-sub">seats sold × fare in force</div></div>
    </div>
    """, unsafe_allow_html=True)

    # ══════════ MATRIX ══════════
    st.markdown('<div class="sec-hd">Route by departure date</div>',
                unsafe_allow_html=True)

    cabs_here = sorted({s["cabin"] for s in skus})
    m1, m2 = st.columns([1.6, 1])
    with m1:
        metric = st.selectbox("Colour the squares by",
                              ["How full each flight is", "Selling speed vs normal",
                               "Fare in force", "Gap against closest rival",
                               "Revenue at stake"], key="mx_metric")
    with m2:
        mx_cabin = st.selectbox("Cabin", cabs_here, key="mx_cabin",
                                help="The grid shows one cabin at a time so the "
                                     "numbers mean something. Economy and "
                                     "Business fares are not comparable.")

    mskus = [s for s in skus if s["cabin"] == mx_cabin]
    if not mskus:
        st.info("No flights in this cabin with the current filters.")
        return

    dates = sorted({pd.Timestamp(s["dep"]) for s in mskus})
    rts   = sorted({s["route"] for s in mskus})
    bucket = {}
    for s in mskus:
        bucket.setdefault((s["route"], dkey(s["dep"])), []).append(s)

    Z, T, H = [], [], []
    for rt in rts:
        zr, tr, hr = [], [], []
        for dd in dates:
            grp = bucket.get((rt, dkey(dd)), [])
            if not grp:
                zr.append(None); tr.append(""); hr.append("No flight")
                continue
            lf_m   = np.mean([g["lf"] for g in grp])
            fare_m = np.mean([g["fare"] for g in grp])
            gp     = [g["gap"] for g in grp if g["gap"] is not None]
            gap_m  = np.mean(gp) if gp else None
            pc     = [g["pace"] for g in grp if g["pace"] is not None]
            pace_m = np.mean(pc) if pc else None
            risk_m = sum(g["risk"] for g in grp if not g["settled"])
            reds   = sum(1 for g in grp if g["flag"] == "red" and not g["settled"])
            srcs   = {g["fsrc"] for g in grp if g["fsrc"] != "arith"}

            if metric == "How full each flight is":
                zr.append(round(lf_m * 100, 1)); tr.append(f"{lf_m*100:.0f}%")
            elif metric == "Selling speed vs normal":
                zr.append(round(pace_m * 100, 1) if pace_m is not None else None)
                tr.append(f"{pace_m*100:+.0f}" if pace_m is not None else "")
            elif metric == "Fare in force":
                zr.append(round(fare_m)); tr.append(f"{fare_m/1000:.1f}k")
            elif metric == "Gap against closest rival":
                zr.append(round(gap_m * 100, 1) if gap_m is not None else None)
                tr.append(f"{gap_m*100:+.0f}%" if gap_m is not None else "")
            else:
                zr.append(round(risk_m))
                tr.append(f"{risk_m/1000:.0f}k" if risk_m >= 500 else "")

            # Visual tag for anything already priced today
            marks = "".join(FARE_SRC[x][2] for x in sorted(srcs))
            if marks:
                tr[-1] = (tr[-1] or "") + " " + marks

            hr.append(
                (f"<b>Priced today · "
                 f"{' and '.join(FARE_SRC[x][1].lower() for x in sorted(srcs))}</b><br>"
                 if srcs else "") +
                f"<b>{rt}</b><br>{dfmt(dd, 'long')} · {mx_cabin}<br>"
                f"{len(grp)} flight{'s' if len(grp) != 1 else ''}<br>"
                f"─────────────<br>{lf_m*100:.0f}% full<br>"
                f"Fare {inr(fare_m)}<br>"
                + ("No rival flight" if gap_m is None
                   else f"{gap_m*100:+.0f}% vs closest rival") +
                f"<br>{fill_speed_words(pace_m)[0]}<br>"
                f"{inr(risk_m)} at stake<br>"
                + (f"{reds} need action" if reds else "Nothing flagged"))
        Z.append(zr); T.append(tr); H.append(hr)

    scale, zmid, cbar = {
        "How full each flight is":   ("RdYlGn_r", None, "% full"),
        "Selling speed vs normal":   ("RdYlGn", 0, "points vs normal"),
        "Fare in force":             ("Blues", None, "fare ₹"),
        "Gap against closest rival": ("RdBu_r", 0, "% vs rival"),
        "Revenue at stake":          ("Reds", None, "₹ at stake"),
    }[metric]

    fig = go.Figure(go.Heatmap(
        z=Z, x=[d.strftime("%d %b") for d in dates],
        y=[r.replace(" to ", " → ") for r in rts],
        text=T, texttemplate="%{text}", textfont=dict(size=10),
        customdata=H, hovertemplate="%{customdata}<extra></extra>",
        colorscale=scale, zmid=zmid, hoverongaps=False,
        colorbar=dict(title=dict(text=cbar, font=dict(size=9)),
                      thickness=11, len=0.85), xgap=2, ygap=2))
    fig.update_xaxes(type="category", side="top", tickangle=-60,
                     tickfont=dict(size=9), title_text="")
    fig.update_yaxes(type="category", tickfont=dict(size=10),
                     autorange="reversed", title_text="")
    style_chart(fig, height=130 + 52 * len(rts), legend=False)
    fig.update_layout(margin=dict(l=10, r=10, t=52, b=10))
    st.plotly_chart(fig, use_container_width=True)
    st.caption(f"{mx_cabin} only. Each row is a route, each column a departure "
               f"date, each square the average across the flights on that route "
               f"and day. <b>●</b> marks a fare accepted from the AI today, "
               f"<b>◆</b> one a manager set. Hover for detail.",
               unsafe_allow_html=True)
    st.markdown(fare_legend(), unsafe_allow_html=True)

    # ══════════ PRICE STRAIGHT FROM THE GRID ══════════
    st.markdown('<div class="sec-hd">Price a fare from the grid</div>',
                unsafe_allow_html=True)
    p1, p2 = st.columns([1.3, 1])
    with p1:
        g_route = st.selectbox("Route", rts, key="mx_prt")
    day_opts = sorted({pd.Timestamp(s["dep"]) for s in mskus
                       if s["route"] == g_route})
    with p2:
        g_date = st.selectbox("Departure date", day_opts,
                              format_func=lambda d: dfmt(d), key="mx_pdt")

    cell = sorted([s for s in mskus if s["route"] == g_route
                   and dkey(s["dep"]) == dkey(g_date)],
                  key=lambda x: x["time"])
    if not cell:
        st.info("Nothing in that square.")
    else:
        labels = [f'{"🔴" if s["flag"] == "red" else "🟠" if s["flag"] == "amber" else "🟢"}'
                  f'  {s["flight"]} · {s["time"]} · {inr(s["fare"])}'
                  f'{" " + FARE_SRC[s["fsrc"]][2] if s["fsrc"] != "arith" else ""}'
                  for s in cell]
        pick = st.radio("Flight", labels, horizontal=True, key="mx_pfl")
        t = cell[labels.index(pick)]

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("How full", f'{round(t["lf"]*100)}%',
                  f'{t["remaining"]} seats left')
        k2.metric("Fare in force", inr(t["fare"]), FARE_SRC[t["fsrc"]][1])
        k3.metric("Closest rival",
                  inr(t["comp"]["fare"]) if t["comp"] else "—",
                  f'{t["gap"]*100:+.0f}%' if t["gap"] is not None else None)
        k4.metric("Days to departure", t["dout"], fill_speed_words(t["pace"])[0])
        st.caption(advice_line(t))
        price_panel(t, C, "grid")
        if st.button("Open this flight in Flight detail", key="mx_goto"):
            goto_flight(t)
            st.rerun()

    # ══════════ ROUTE SUMMARY ══════════
    st.markdown('<div class="sec-hd">By route</div>', unsafe_allow_html=True)
    rows = []
    for rt in sorted({s["route"] for s in skus}):
        grp = [s for s in skus if s["route"] == rt]
        gp  = [g["gap"] for g in grp if g["gap"] is not None]
        rows.append({
            "route": rt, "n": len(grp),
            "lf": np.mean([g["lf"] for g in grp]),
            "unsold": sum(g["remaining"] for g in grp),
            "fare": np.mean([g["fare"] for g in grp]),
            "gap": np.mean(gp) if gp else None,
            "risk": sum(g["risk"] for g in grp if not g["settled"]),
            "red": sum(1 for g in grp if g["flag"] == "red" and not g["settled"]),
            "amber": sum(1 for g in grp if g["flag"] == "amber" and not g["settled"]),
            "set": sum(1 for g in grp if g["settled"])})
    rows.sort(key=lambda x: -x["risk"])

    html = ("""<table class="wrap"><colgroup>
    <col style="width:20%"><col style="width:8%"><col style="width:11%">
    <col style="width:10%"><col style="width:11%"><col style="width:10%">
    <col style="width:12%"><col style="width:18%"></colgroup><thead><tr>
      <th>Route</th><th>Fares</th><th>Avg fullness</th><th>Seats unsold</th>
      <th>Avg fare</th><th>Vs rivals</th><th>At stake</th><th>Status</th>
    </tr></thead><tbody>""")
    for r in rows:
        c, dot = lf_cls(r["lf"])
        if r["gap"] is None:
            gtxt, gcls = "—", ""
        else:
            gtxt = f'{"+" if r["gap"] > 0 else ""}{r["gap"]*100:.0f}%'
            gcls = ("f-exp" if r["gap"] > 0.03 else
                    "f-cheap" if r["gap"] < -0.03 else "f-sim")
        bits = []
        if r["red"]:   bits.append(f'<span style="color:{RED};font-weight:700">'
                                   f'{r["red"]} need action</span>')
        if r["amber"]: bits.append(f'<span style="color:{AMBER}">'
                                   f'{r["amber"]} to watch</span>')
        if r["set"]:   bits.append(f'<span style="color:{GREEN}">'
                                   f'{r["set"]} priced</span>')
        if not bits:   bits.append(f'<span style="color:{GREY}">all in line</span>')
        html += f"""<tr>
          <td><b class="f-navy">{r['route'].replace(' to ',' → ')}</b></td>
          <td class="num">{r['n']}</td>
          <td class="num"><span class="{c}">{dot} {r['lf']*100:.0f}%</span></td>
          <td class="num" style="color:{GREY}">{r['unsold']:,}</td>
          <td class="num f-mag">{inr(r['fare'])}</td>
          <td class="num"><span class="{gcls}">{gtxt}</span></td>
          <td class="num f-navy">{inr(r['risk'])}</td>
          <td style="font-size:0.71rem">{' · '.join(bits)}</td>
        </tr>"""
    html += "</tbody></table>"
    st.markdown(html, unsafe_allow_html=True)

    # ══════════ FULLNESS TOWARD DEPARTURE ══════════
    st.markdown('<div class="sec-hd">Fullness as departure approaches</div>',
                unsafe_allow_html=True)
    pts = pd.DataFrame([{"Route": s["route"].replace(" to ", " → "),
                         "Days to departure": s["dout"],
                         "Full%": round(s["lf"] * 100, 1)} for s in skus])
    if pts.empty:
        st.info("No data for this view.")
    else:
        agg = (pts.groupby(["Route", "Days to departure"])["Full%"]
               .mean().reset_index().sort_values("Days to departure"))
        figc = px.line(agg, x="Days to departure", y="Full%", color="Route",
                       markers=True,
                       color_discrete_sequence=[NAVY, MAGENTA, SKY, AMBER, GREEN])
        figc.update_xaxes(autorange="reversed",
                          title_text="Days before departure (departure at right)")
        figc.update_yaxes(title_text="% full", range=[0, 105])
        style_chart(figc, height=320)
        st.plotly_chart(figc, use_container_width=True)
        st.caption("Every fare in scope, averaged by route. Lines should climb "
                   "toward the right as departure nears; one that stays flat is "
                   "a route that is not filling.")


# ═════════════════════════════════════════════════════════════
# PAGE 2 — FLIGHT DETAIL  (exactly one flight, cabin and date)
# ═════════════════════════════════════════════════════════════
def render_flight_detail(C):
    skus, indigo_df, comp_df = C["skus"], C["indigo_df"], C["comp_df"]
    feedback_df, ai_log_df = C["feedback_df"], C["ai_log_df"]
    pace_curve, dcol, today = C["pace_curve"], C["dcol"], C["today"]
    pax_type, trip_type = C["pax_type"], C["trip_type"]
    analyst, standing = C["analyst"], C["standing"]

    if not skus:
        st.info("No flights match the current filters. Widen them in the sidebar.")
        return

    st.markdown('<div class="tab-intro">One flight, one cabin, one departure '
                'date. Everything on this page is about that single fare.</div>',
                unsafe_allow_html=True)

    # ── Pick the flight, honouring any drill-through ─────────
    sel_routes = sorted({s["route"] for s in skus})
    r_pref = st.session_state.get("fd_route")
    r_ix = sel_routes.index(r_pref) if r_pref in sel_routes else 0
    c1, c2, c3 = st.columns([1.2, 1, 2])
    with c1:
        route = st.selectbox("Route", sel_routes, index=r_ix, key="fd_route_sel")
    cabs = sorted({s["cabin"] for s in skus if s["route"] == route})
    c_pref = st.session_state.get("fd_cabin")
    c_ix = cabs.index(c_pref) if c_pref in cabs else 0
    with c2:
        cabin = st.selectbox("Cabin", cabs, index=c_ix, key="fd_cabin_sel")

    pool = sorted([s for s in skus if s["route"] == route and s["cabin"] == cabin],
                  key=lambda x: (x["dep"], x["time"]))
    if not pool:
        st.info("Nothing matches. Widen the filters.")
        return
    labels = [f'{s["flight"]} · {s["time"]} · {dfmt(s["dep"])} '
              f'({s["dout"]} days to departure)' for s in pool]
    k_pref = st.session_state.get("fd_flight")
    f_ix = next((i for i, s in enumerate(pool) if s["key"] == k_pref), 0)
    with c3:
        pick = st.selectbox("Flight and departure date", labels, index=f_ix,
                            key="fd_flight_sel")
    T = pool[labels.index(pick)]
    st.session_state["fd_route"]  = route
    st.session_state["fd_cabin"]  = cabin
    st.session_state["fd_flight"] = T["key"]

    f_raw_no, f_time, f_date = T["raw"], T["time"], T["dep"]
    f_no, f_days, f_lf = T["flight"], T["dout"], T["lf"]
    f_sold, f_total, f_slot = T["sold"], T["total"], T["slot"]
    f_match, f_pace = T["comp"], T["pace"]
    comp_list = [(str(c["Airline"]), str(c["Flight No."]), str(c["Departure Time"]),
                  int(c["Fare (INR)"])) for _, c in T["comp_rows"].iterrows()
                 if pd.notna(c.get("Fare (INR)"))] if len(T["comp_rows"]) else []

    # The quoting toggles apply here only, so recompute with them
    arith, bd = calc_fare(route, cabin, f_days, f_lf,
                          f_match["fare"] if f_match else 0,
                          T["holiday"], deph(f_time), pax_type, trip_type,
                          pace_delta=f_pace)
    live_fare, live_src = effective_fare(C["decided_fares"], T["key"], arith)

    spd_txt, spd_cls = fill_speed_words(f_pace)
    if f_match:
        gap = (live_fare - f_match["fare"]) / f_match["fare"]
        cmp_s = (f'Against {f_match["airline"]} departing {f_match["time"]} at '
                 f'{inr(f_match["fare"])}, we are <b>{abs(gap)*100:.0f}% '
                 f'{"higher" if gap > 0 else "lower"}</b>.')
    else:
        cmp_s = "No rival flight departs at a similar time on this date."
    fare_s = (f'Our rules put the fare at <span class="big">{inr(arith)}</span>. '
              if live_src == "arith" else
              f'The fare in force is <span class="big">{inr(live_fare)}</span> '
              f'({FARE_SRC[live_src][1].lower()}, against {inr(arith)} from the '
              f'rules). ')
    st.markdown(
        f'<div class="insight"><b>{f_no}</b> departs {f_time} on '
        f'<b>{dfmt(f_date, "long")}</b>, {f_days} days from now. It is '
        f'<b>{round(f_lf*100)}% full</b> ({f_sold} of {f_total} seats sold, '
        f'{f_total - f_sold} left) and is <b>{spd_txt.lower()}</b> for this '
        f'route at this stage. {fare_s}{cmp_s}</div>', unsafe_allow_html=True)

    ai_today, mgr_today = "—", "Not yet reviewed"
    if not ai_log_df.empty and "Flight No." in ai_log_df.columns:
        _l = ai_log_df.copy()
        _l["_dk"] = _l["Departure Date"].map(dkey)
        tl = _l[(_l["Flight No."].astype(str) == f_raw_no) &
                (_l.get("Cabin Class", pd.Series(dtype=str)).astype(str) == cabin) &
                (_l["_dk"] == dkey(f_date))]
        if not tl.empty:
            ai_today  = inr(tl.iloc[-1].get("AI Suggested Fare", ""))
            mgr_today = str(tl.iloc[-1].get("Manager Decision", "Pending") or "Pending")

    cseat  = seat_cost(route, cabin)
    p_seat = live_fare - cseat
    booked = p_seat * f_sold

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
        <div class="kpi-val" style="color:{FARE_SRC[live_src][0]}">
          {fare_dot(live_src)}{inr(live_fare)}</div>
        <div class="kpi-lbl">Fare in force now</div>
        <div class="kpi-sub">{FARE_SRC[live_src][1]}{'' if live_src == 'arith'
          else f' · rules said {inr(arith)}'}</div></div>
      <div class="kpi-card">
        <div class="kpi-val k-navy">{ai_today}</div>
        <div class="kpi-lbl">AI suggested</div>
        <div class="kpi-sub">{mgr_today}</div></div>
      <div class="kpi-card">
        <div class="kpi-val {'k-exp' if f_match and live_fare > f_match['fare'] else 'k-green'}"
             style="color:{RED if (f_match and live_fare > f_match['fare']) else GREEN}">
          {inr(f_match['fare']) if f_match else '—'}</div>
        <div class="kpi-lbl">Closest rival</div>
        <div class="kpi-sub">{f_match['airline'] + ' ' + f_match['time']
          if f_match else 'none at a similar time'}</div></div>
      <div class="kpi-card">
        <div class="kpi-val {'k-green' if booked > 0 else 'k-red'}">{inr(booked)}</div>
        <div class="kpi-lbl">Profit on seats sold so far</div>
        <div class="kpi-sub">{inr(p_seat)}/seat · {inr(p_seat * f_total)} if full</div></div>
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
            f'<div class="bd-row"><span class="bd-neu">{cabin_short(cabin)} '
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
        rows.append(f'<div class="bd-row"><span>Rules fare</span>'
                    f'<span style="color:#E91E8C">{inr(bd["final"])}</span></div>')
        st.markdown("<div class='arith-box'>" + "".join(rows) + "</div>",
                    unsafe_allow_html=True)
        st.caption(f"Quoted for {pax_type}, {trip_type}. Change those in the "
                   f"sidebar. The cabin multiplier applies first, then demand "
                   f"adjustments within a {DEMAND_CAP_LO*100:.0f}% to "
                   f"+{DEMAND_CAP_HI*100:.0f}% band.")

    with a_right:
        rdef = standing.get(route, STRATEGIC_OPTIONS[0])
        ridx = STRATEGIC_OPTIONS.index(rdef) if rdef in STRATEGIC_OPTIONS else 0
        strategy = st.selectbox(f"Pricing goal for {route} (remembered for this route)",
                                STRATEGIC_OPTIONS, index=ridx, key="fd_strat")
        if strategy != rdef and st.button("📌  Save as this route's goal"):
            try:
                save_strategy(route, strategy, analyst or "Unknown")
                st.success(f"Saved for {route}.")
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
                    _h = ai_log_df[(ai_log_df["Route"] == route) &
                                   (ai_log_df.get("Manager Decision", "")
                                    .isin(["Accepted", "Overridden"]))]
                    hist = _h.to_dict("records")
                snapshot = "; ".join(f"{a} {fn} {ft} {inr(fa)}"
                                     for a, fn, ft, fa in comp_list) or "none"
                pargs = dict(route=route, flight_no=f_no, dep_time=f_time,
                             cabin=cabin, dep_date=dfmt(f_date),
                             days_to_dep=f_days, load_factor=f_lf,
                             pace_delta=f_pace, arithmetic_fare=arith, bd=bd,
                             comp_match=f_match, comp_all=comp_list,
                             strategy=strategy, history=hist,
                             pax_type=pax_type, trip_type=trip_type)
                with st.spinner("Asking the AI pricing analyst..."):
                    dec, fare, rat, engine, note = call_llm(
                        pargs, bd, arith, f_match, f_lf)
                try:
                    save_ai_log({"Log Date": datetime.now().strftime("%Y-%m-%d %H:%M"),
                                 "Analyst": analyst, "Route": route,
                                 "Flight No.": f_raw_no, "Departure Time": f_time,
                                 "Departure Date": dkey(f_date),
                                 "Cabin Class": cabin, "Days to Departure": f_days,
                                 "Load Factor": round(f_lf * 100, 1),
                                 "Seats At Decision": f_sold,
                                 "Arithmetic Fare": arith, "AI Decision": dec,
                                 "AI Suggested Fare": fare, "AI Rationale": rat,
                                 "Engine": engine, "Competitor Snapshot": snapshot,
                                 "Strategic Direction": strategy,
                                 "Manager Decision": "Pending", "Final Fare Used": ""})
                except Exception as e:
                    st.warning(f"Recommendation received but not logged: {e}")
                st.session_state["ai"] = {
                    "dec": dec, "fare": fare, "rat": rat, "arith": arith,
                    "route": route, "flight_raw": f_raw_no, "disp": f_no,
                    "time": f_time, "date": dkey(f_date), "cabin": cabin,
                    "days": f_days, "lf": f_lf, "sold": f_sold,
                    "strategy": strategy, "engine": engine, "note": note,
                    "snapshot": snapshot, "analyst": analyst,
                    "pax": pax_type, "trip": trip_type}
                st.rerun()

        r = st.session_state.get("ai")
        if r and r.get("flight_raw") == f_raw_no and r.get("date") == dkey(f_date) \
                and r.get("cabin") == cabin:
            ok = str(r["dec"]).lower().startswith("approve")
            badge = "ai-badge-ok" if ok else "ai-badge-ov"
            btxt  = "✔ Agrees with our fare" if ok else "⚡ Suggests a different fare"
            eng   = r.get("engine", "")
            is_fb = eng.startswith("Rules")
            d = r["fare"] - r["arith"]
            dtxt = ("same as our rules" if d == 0 else
                    f'{inr(abs(d))} {"higher" if d > 0 else "lower"} than our rules')
            st.markdown(f"""
            <div class="ai-result">
              <div style="display:flex;align-items:center;justify-content:space-between;">
                <span style="font-size:0.6rem;font-weight:700;color:#1B2D6B;
                      text-transform:uppercase;letter-spacing:0.1em;">
                  {"Rules-based suggestion" if is_fb else "AI recommendation"}</span>
                <span class="{badge}">{btxt}</span></div>
              <div class="ai-price">{inr(r['fare'])}</div>
              <div style="font-size:0.72rem;color:#7c8db5;margin-top:-0.2rem;">{dtxt}</div>
              <div class="ai-rat">{r['rat']}</div>
              <div style="margin-top:0.5rem;">
                <span class="engine-chip" style="background:
                  {'#fef3c7' if is_fb else '#e8f0fe'};border:1px solid
                  {'#D97706' if is_fb else '#2F6FD0'};color:
                  {'#b45309' if is_fb else '#1B2D6B'};">Engine: {eng}</span></div>
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
                                     step=100, key="fd_ov")
                why = st.text_input("Why are you changing it?",
                                    placeholder="e.g. corporate block expected",
                                    key="fd_why")
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

    # ══════════ CHARTS FOR THIS ONE FLIGHT ══════════
    win = st.selectbox("Chart period", ["Last 7 days", "Last 14 days",
                                        "Last 30 days", "Last 60 days"],
                       index=2, key="fd_win")
    win_days = {"Last 7 days": 7, "Last 14 days": 14,
                "Last 30 days": 30, "Last 60 days": 60}[win]
    win_from = today - timedelta(days=win_days)

    g_left, g_right = st.columns([1, 1.15], gap="large")

    with g_left:
        st.markdown('<div class="sec-hd">How this flight has been filling up</div>',
                    unsafe_allow_html=True)
        hist = indigo_df[(indigo_df["Route"] == route) &
                         (indigo_df["Cabin Class"] == cabin) &
                         (indigo_df["Flight No."].astype(str) == f_raw_no) &
                         (indigo_df["Departure Date"] == f_date)].copy()
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
            hist["Booked"] = seats.diff().clip(lower=0)
            hist["dout"] = pd.to_numeric(hist["Days to Departure"], errors="coerce")
            hist["Usual%"] = hist["dout"].map(
                lambda d: pace_curve.get((route, cabin, int(d)))
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
                mode="lines+markers", line=dict(color=NAVY, width=2.6),
                marker=dict(size=5, color=NAVY),
                hovertemplate="%{y:.1f}% full<extra></extra>"), row=2, col=1)
            if hist["Usual%"].notna().any():
                fig.add_trace(go.Scatter(
                    x=hist[dcol], y=hist["Usual%"], name="Normal for this route",
                    mode="lines", line=dict(color=AMBER, width=2, dash="dot"),
                    hovertemplate="%{y:.1f}% typical<extra></extra>"), row=2, col=1)
            lo = hist[dcol].min() - pd.Timedelta(days=1)
            hi = hist[dcol].max() + pd.Timedelta(days=1)
            span = max((hi - lo).days, 1)
            step = 2 if span <= 16 else (4 if span <= 34 else 7)
            bmax = float(hist["Booked"].max() or 0)
            fig.update_yaxes(title_text="Seats sold", row=1, col=1,
                             range=[0, max(bmax * 1.35, 5)])
            ylo = max(0, min(hist["Full%"].min(),
                             hist["Usual%"].min() if hist["Usual%"].notna().any()
                             else 100) - 12)
            fig.update_yaxes(title_text="% full", range=[ylo, 100], row=2, col=1)
            for rr in (1, 2):
                fig.update_xaxes(type="date", tickformat="%d %b",
                                 dtick=86400000 * step, tickangle=0,
                                 range=[lo, hi], title_text="", row=rr, col=1)
            style_chart(fig, height=360)
            st.plotly_chart(fig, use_container_width=True)
            st.caption("Pink bars are seats sold each day. Navy is how full the "
                       "flight is; dotted amber is how full this route usually "
                       "is by the same point.")

    with g_right:
        st.markdown('<div class="sec-hd">How prices have moved</div>',
                    unsafe_allow_html=True)
        frames = []
        ch = comp_df[(comp_df["Route"] == route) &
                     (comp_df["Cabin Class"] == cabin) &
                     (comp_df["Departure Date"] == f_date) &
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

        ih = indigo_df[(indigo_df["Route"] == route) &
                       (indigo_df["Cabin Class"] == cabin) &
                       (indigo_df["Flight No."].astype(str) == f_raw_no) &
                       (indigo_df["Departure Date"] == f_date)].copy()
        if not ih.empty and dcol in ih.columns:
            ih = ih.dropna(subset=[dcol])
            ih = ih[ih[dcol] >= win_from].sort_values(dcol)
            rws = []
            for _, g2 in ih.iterrows():
                d = g2[dcol]
                dayc = ch[ch["Scrape Date"] == d] if not ch.empty else pd.DataFrame()
                mtd = match_competitor(dayc, deph(f_time)) if not dayc.empty else None
                dl = pace_delta_for(pace_curve, route, cabin,
                                    int(g2.get("Days to Departure", 30) or 30),
                                    float(g2.get("Load Factor", 0.6) or 0.6))
                v, _ = calc_fare(route, cabin,
                                 int(g2.get("Days to Departure", 30) or 30),
                                 float(g2.get("Load Factor", 0.6) or 0.6),
                                 mtd["fare"] if mtd else 0,
                                 str(g2.get("Holiday / Festival", "No")) == "Yes",
                                 deph(g2.get("Departure Time", "10:00")),
                                 pax_type, trip_type, pace_delta=dl)
                rws.append({"Date": d, "Series": "IndiGo (our rules)", "Fare": v})
            if rws:
                frames.append(pd.DataFrame(rws))

        if live_src != "arith":
            frames.append(pd.DataFrame([{
                "Date": today,
                "Series": ("IndiGo (manager set)" if live_src == "manager"
                           else "IndiGo (AI accepted)"),
                "Fare": live_fare}]))

        if frames:
            allt = pd.concat(frames, ignore_index=True).dropna(subset=["Fare"])
            allt["Date"] = pd.to_datetime(allt["Date"], errors="coerce")
            allt = allt.dropna(subset=["Date"])
            allt["Date"] = allt["Date"].dt.normalize()
            allt = (allt.groupby(["Date", "Series"], as_index=False)["Fare"]
                    .mean().sort_values("Date"))
            fig2 = px.line(allt, x="Date", y="Fare", color="Series", markers=True,
                           color_discrete_map={
                               "Air India": SKY, "Akasa Air": RED,
                               "IndiGo (our rules)": MAGENTA,
                               "IndiGo (AI accepted)": FARE_SRC["ai"][0],
                               "IndiGo (manager set)": FARE_SRC["manager"][0]})
            for tr in fig2.data:
                if tr.name.startswith("IndiGo"):
                    tr.line.dash = "dash"; tr.line.width = 2.5
                if tr.name in ("IndiGo (AI accepted)", "IndiGo (manager set)"):
                    tr.mode = "markers"; tr.marker.size = 16
                    tr.marker.symbol = "star"
                    tr.marker.line = dict(width=1.2, color="#ffffff")
            fig2.update_yaxes(title_text="Fare (₹)", tickprefix="₹",
                              separatethousands=True)
            tlo = allt["Date"].min() - pd.Timedelta(days=1)
            thi = allt["Date"].max() + pd.Timedelta(days=1)
            tspan = max((thi - tlo).days, 1)
            tstep = 2 if tspan <= 16 else (4 if tspan <= 34 else 7)
            fig2.update_xaxes(type="date", tickformat="%d %b", title_text="",
                              dtick=86400000 * tstep, tickangle=0,
                              range=[tlo, thi])
            style_chart(fig2, height=360)
            st.plotly_chart(fig2, use_container_width=True)
            st.caption(f"{win.lower()}, for this flight and cabin only. Rivals "
                       "are limited to departures within three hours of ours. "
                       "Dashed lines are IndiGo's own prices; a star marks a "
                       "fare set today.")
            st.markdown(fare_legend(), unsafe_allow_html=True)
        else:
            st.info("No price history in this period for this flight.")


# ═════════════════════════════════════════════════════════════
# PAGE 3 — NEEDS ATTENTION
# One table. Every row carries its own pricing controls, so nothing has to
# be matched up against a second list further down the page.
# ═════════════════════════════════════════════════════════════
COLW = [0.35, 2.5, 1.0, 1.25, 1.15, 1.7, 0.8, 1.15, 1.15]


def render_action_list(C):
    skus = C["skus"]
    analyst = C["analyst"]

    st.markdown('<div class="tab-intro">Fares that are out of line, biggest '
                'exposure first, across everything your filters cover. Open a '
                'row to price it — once you set a fare the row clears.</div>',
                unsafe_allow_html=True)

    open_rows = [t for t in skus if t["flag"] != "green" and not t["settled"]]
    settled   = [t for t in skus if t["settled"]]
    green     = [t for t in skus if t["flag"] == "green" and not t["settled"]]

    if not open_rows:
        st.success(f"Nothing outstanding. {len(green)} fares are in line with "
                   f"their closest rival"
                   + (f" and {len(settled)} were priced today." if settled else "."))
        return

    red   = [t for t in open_rows if t["flag"] == "red"]
    amber = [t for t in open_rows if t["flag"] == "amber"]
    top   = open_rows[0]
    if top["comp"]:
        direction = "more expensive than" if (top["gap"] or 0) > 0 else "cheaper than"
        head = (f'Start with <b>{top["route"]}</b>, {top["flight"]} at '
                f'{top["time"]} on <b>{dfmt(top["dep"])}</b>, '
                f'{cabin_short(top["cabin"])}. Our fare of '
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

    if not (analyst or "").strip():
        st.warning("Enter your name in the sidebar before pricing — every "
                   "decision is recorded against whoever made it.")

    st.markdown(
        '<div class="rowhead" style="grid-template-columns:'
        + " ".join(f"{w}fr" for w in COLW) + '">'
        '<div></div><div>Flight and date</div><div>How full</div>'
        '<div>Selling speed</div><div>Our fare</div><div>Closest rival</div>'
        '<div>Diff</div><div>At stake</div><div>Price it</div></div>',
        unsafe_allow_html=True)

    shown = open_rows[:25]
    for t in shown:
        k = t["key"]
        lcls, dot = lf_cls(t["lf"])
        spd_t, spd_c = fill_speed_words(t["pace"])
        flagcol = RED if t["flag"] == "red" else AMBER
        if t["comp"]:
            g = t["gap"]
            gtxt = f'{"+" if g > 0 else ""}{g*100:.0f}%'
            gcol = (RED if g > 0.03 else GREEN if g < -0.03 else AMBER)
            rival = (f'{t["comp"]["airline"]}<br><span class="sub">'
                     f'{t["comp"]["time"]} · {inr(t["comp"]["fare"])}</span>')
            if t["move"] is not None and abs(t["move"]) >= 1:
                arrow = "▲" if t["move"] > 0 else "▼"
                acol  = RED if t["move"] > 0 else GREEN
                rival += (f'<br><span style="color:{acol};font-size:0.65rem;'
                          f'font-weight:700">{arrow} {inr(abs(t["move"]))} '
                          f'since yesterday</span>')
        else:
            rival, gtxt, gcol = "—", "—", GREY

        with st.container(border=True):
            c = st.columns(COLW, vertical_alignment="center")
            c[0].markdown(f'<div style="color:{flagcol};font-size:1.1rem;'
                          f'line-height:1">●</div>', unsafe_allow_html=True)
            c[1].markdown(
                f'<div class="cell"><b style="color:{NAVY}">'
                f'{t["route"].replace(" to ", " → ")}</b><br>'
                f'<span class="sub">{t["flight"]} · {t["time"]} · '
                f'{cabin_short(t["cabin"])} · {dfmt(t["dep"])}</span></div>',
                unsafe_allow_html=True)
            c[2].markdown(f'<div class="cell mono"><span class="{lcls}">'
                          f'{dot} {round(t["lf"]*100)}%</span><br>'
                          f'<span class="sub">{t["sold"]}/{t["total"]}</span></div>',
                          unsafe_allow_html=True)
            c[3].markdown(f'<div class="cell"><span class="{spd_c}">{spd_t}</span>'
                          f'</div>', unsafe_allow_html=True)
            c[4].markdown(f'<div class="cell mono"><b style="color:'
                          f'{FARE_SRC[t["fsrc"]][0]}">{fare_dot(t["fsrc"])}'
                          f'{inr(t["fare"])}</b><br><span class="sub">'
                          f'{FARE_SRC[t["fsrc"]][1]}</span></div>',
                          unsafe_allow_html=True)
            c[5].markdown(f'<div class="cell">{rival}</div>',
                          unsafe_allow_html=True)
            c[6].markdown(f'<div class="cell mono" style="color:{gcol};'
                          f'font-weight:700">{gtxt}</div>',
                          unsafe_allow_html=True)
            c[7].markdown(f'<div class="cell mono" style="color:{NAVY};'
                          f'font-weight:700">{inr(t["risk"])}</div>',
                          unsafe_allow_html=True)
            openk = f"open_{k}"
            is_open = st.session_state.get(openk, t is shown[0])
            if c[8].button("Close" if is_open else "Price it",
                           key=f"tog_{k}", use_container_width=True):
                st.session_state[openk] = not is_open
                st.rerun()

            st.markdown(f'<div class="rowadvice">{advice_line(t)}</div>',
                        unsafe_allow_html=True)

            if is_open:
                price_panel(t, C, "act")

    st.markdown(f"""<div class="legend-row">
      <span style="color:{RED}">●</span> <b>Needs action</b> — over 15% from the
      closest rival with a week or less left, or that rival moved over 10%
      overnight &nbsp;·&nbsp;
      <span style="color:{AMBER}">●</span> <b>Watch</b> — over 8% away, or rival
      moved over 5% &nbsp;·&nbsp;
      <b>At stake</b> = price difference × seats still unsold
    </div>""", unsafe_allow_html=True)
    st.markdown(fare_legend(), unsafe_allow_html=True)

    if len(open_rows) > 25:
        st.info(f"Showing the top 25 by money at stake. {len(open_rows) - 25} "
                f"more are open — narrow the filters to work through them.")
    if settled:
        with st.expander(f"Already priced today ({len(settled)})"):
            for t in settled:
                st.markdown(f"- **{t['route']}** {t['flight']} {t['time']} · "
                            f"{cabin_short(t['cabin'])} · {dfmt(t['dep'])} — "
                            f"{inr(t['fare'])} ({FARE_SRC[t['fsrc']][1].lower()})")

# ═════════════════════════════════════════════════════════════
# PAGE 4 — DECISION HISTORY
# ═════════════════════════════════════════════════════════════
def render_history(C):
    ai_log_df, indigo_df = C["ai_log_df"], C["indigo_df"]
    dcol = C["dcol"]

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
        scope = st.radio("Show", ["Routes in my filter", "Everything"],
                         index=0, horizontal=True, key="hist_scope")
    with c2:
        outcome = st.selectbox("Outcome", ["All", "Accepted", "Overridden",
                                           "Pending"], key="hist_outcome")
    if scope == "Routes in my filter" and "Route" in h.columns:
        h = h[h["Route"].isin(C["f_routes"])]
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
    # Pending rows are not rejections, so they stay out of the denominator.
    rate  = n_acc / max(n_acc + n_ovr, 1) * 100
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
        f'decision. Of the {n_acc + n_ovr} actually decided, the acceptance rate '
        f'is <span class="big">{rate:.0f}%</span>, which suggests {judge}.'
        f'{out_txt}</div>', unsafe_allow_html=True)

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
# PAGE 5 — BUSINESS CASE
# ═════════════════════════════════════════════════════════════
def render_business_case(C):
    indigo_df, feedback_df = C["indigo_df"], C["feedback_df"]
    pace_curve, dcol = C["pace_curve"], C["dcol"]
    f_routes, f_cabins = C["f_routes"], C["f_cabins"]

    st.markdown('<div class="tab-intro">What this system is worth: how much more '
                'revenue our pricing rules would have earned compared with one '
                'flat fare, and the profit on fares already approved.</div>',
                unsafe_allow_html=True)

    st.markdown('<div class="sec-hd">If we had priced this way all along</div>',
                unsafe_allow_html=True)
    scope = st.radio("Test on", ["Routes and cabins in my filter",
                                 "Everything on record"],
                     horizontal=True, key="bt_scope")

    bt = indigo_df.copy()
    if scope == "Routes and cabins in my filter":
        bt = bt[bt["Route"].isin(f_routes) & bt["Cabin Class"].isin(f_cabins)]

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
            f'{"s" if n_routes != 1 else ""}, '
            f'departing between <b>{dfmt(dep_lo)}</b> and <b>{dfmt(dep_hi)}</b>, '
            f'using {len(bt):,} daily readings recorded between '
            f'<b>{dfmt(obs_lo)}</b> and <b>{dfmt(obs_hi)}</b>.</div>',
            unsafe_allow_html=True)

        keys = ["Route", "Flight No.", "Cabin Class", "Departure Date"]
        bt["Seats Sold"] = pd.to_numeric(bt["Seats Sold"], errors="coerce")
        # Daily bookings = change in seats sold. The FIRST reading of a flight
        # has no prior day, so those seats were sold before our data begins and
        # we cannot know what fare they went at. Earlier versions counted them
        # as one day's bookings, which put roughly a quarter of the volume on a
        # single arbitrary day's price. They are now excluded.
        bt["_new"] = bt.groupby(keys)["Seats Sold"].diff().clip(lower=0)
        n_dropped = int(bt["_new"].isna().sum())
        bt["_new"] = bt["_new"].fillna(0)

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

            st.caption(f"Priced at the standard adult one-way fare. "
                       f"{n_dropped} opening readings were excluded because the "
                       f"seats in them were sold before our data starts, so the "
                       f"fare they went at is unknown.")
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
