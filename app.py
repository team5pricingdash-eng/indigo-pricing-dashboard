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

/* ── Aliases + additions for v7 sections ── */
.kpi-strip.six { grid-template-columns:repeat(6,minmax(0,1fr)); }
.fare-tbl { width:100%; border-collapse:separate; border-spacing:0; font-size:0.72rem;
  border:1px solid #dde3f0; border-radius:10px; overflow:hidden;
  table-layout:fixed; background:#fff; }
.fare-tbl thead tr { background:#f2f5fc; }
.fare-tbl th { padding:0.48rem 0.5rem; font-size:0.56rem; font-weight:700;
  letter-spacing:0.07em; text-transform:uppercase; color:#1B2D6B;
  border-bottom:2px solid #dde3f0; text-align:left;
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.fare-tbl td { padding:0.42rem 0.5rem; border-bottom:1px solid #f2f5fc; color:#2a4060;
  font-family:'DM Mono',monospace; font-size:0.7rem;
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.fare-tbl tr:last-child td { border-bottom:none; }
.fare-tbl tbody tr:hover td { background:#fafbff; }
.date-sep td { background:#eaf0fb !important; color:#1B2D6B !important;
  font-family:'DM Sans',sans-serif !important; font-weight:700 !important;
  font-size:0.7rem !important; padding:0.32rem 0.55rem !important;
  border-top:2px solid #c9d6f0 !important; }
.dl-up { color:#DC2626; font-weight:700; }
.dl-dn { color:#16A34A; font-weight:700; }
.dl-fl { color:#8095bd; }

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
# MAIN
# ═════════════════════════════════════════════════════════════
def main():
    try:
        comp_df, indigo_df = load_data()
    except Exception as e:
        st.error(f"Could not connect to Google Sheets: {e}")
        st.info("Check GOOGLE_SHEET_NAME and [gcp_service_account] in Streamlit "
                "Secrets, and that the sheet is shared with the service account email.")
        return

    feedback_df = load_tab(FEEDBACK_TAB, ("Timestamp",))
    ai_log_df   = load_tab(AI_LOG_TAB, ("Log Date",))
    strategy_df = load_tab(STRATEGY_TAB)

    today = pd.Timestamp.today().normalize()
    dmin, dmax = today - timedelta(days=30), today + timedelta(days=90)
    dcol = "Date" if "Date" in indigo_df.columns else "Scrape Date"

    pace_curve = booking_pace_curve(indigo_df)

    # Standing strategy lookup: route -> direction
    standing = {}
    if not strategy_df.empty and "Route" in strategy_df.columns:
        for _, s in strategy_df.iterrows():
            standing[str(s.get("Route", ""))] = str(s.get("Strategic Direction", ""))

    # A triage jump can pre-set the sidebar widgets before they are created
    if "jump_route" in st.session_state:
        st.session_state["route_sel"] = st.session_state.pop("jump_route")
        st.session_state["cabin_sel"] = st.session_state.pop("jump_cabin")
        st.session_state.pop("lfpick", None)

    # ── SIDEBAR ──────────────────────────────────────────────
    with st.sidebar:
        st.markdown('<div class="sb-brand">'
                    '<span style="color:#E91E8C;font-weight:800">6E</span>'
                    '&nbsp; IndiGo · Pricing Intelligence</div>', unsafe_allow_html=True)

        analyst = st.text_input("Analyst name", value=st.session_state.get("analyst", ""),
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
                           label_visibility="collapsed", key="d1")

        if trip_type == "Round Trip":
            st.markdown('<p style="color:#1B2D6B;font-size:0.62rem;font-weight:700;'
                        'text-transform:uppercase;letter-spacing:0.09em;'
                        'margin:0.6rem 0 0.15rem">Return Date</p>',
                        unsafe_allow_html=True)
            d2 = st.date_input("Return", value=(today + timedelta(days=7)).date(),
                               min_value=dmin.date(), max_value=dmax.date(),
                               label_visibility="collapsed", key="d2")
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

    # ── SNAPSHOT OF LATEST STATE PER SKU (all routes/cabins) ─
    snap_all = indigo_df[indigo_df["Departure Date"].isin(sel_dates)].copy()
    if not snap_all.empty and dcol in snap_all.columns:
        snap_all = (snap_all.sort_values(dcol)
                    .groupby(["Route", "Flight No.", "Cabin Class", "Departure Date"],
                             as_index=False).last())

    comp_all = comp_df[comp_df["Departure Date"].isin(sel_dates)].copy()
    comp_latest = pd.DataFrame()
    comp_prev   = pd.DataFrame()
    if not comp_all.empty and "Scrape Date" in comp_all.columns:
        comp_latest = (comp_all.sort_values("Scrape Date")
                       .groupby(["Airline", "Flight No.", "Route", "Cabin Class",
                                 "Departure Date"], as_index=False).last())
        # Previous scrape, for overnight movement
        prev_pool = comp_all.merge(
            comp_latest[["Airline", "Flight No.", "Route", "Cabin Class",
                         "Departure Date", "Scrape Date"]]
            .rename(columns={"Scrape Date": "_latest"}),
            on=["Airline", "Flight No.", "Route", "Cabin Class", "Departure Date"],
            how="left")
        prev_pool = prev_pool[prev_pool["Scrape Date"] < prev_pool["_latest"]]
        if not prev_pool.empty:
            comp_prev = (prev_pool.sort_values("Scrape Date")
                         .groupby(["Airline", "Flight No.", "Route", "Cabin Class",
                                   "Departure Date"], as_index=False).last())

    def overnight_move(airline, flight, route, cabin, dep_date):
        """Latest minus previous scrape fare for one competitor flight."""
        if comp_latest.empty or comp_prev.empty:
            return None
        cur = comp_latest[(comp_latest["Airline"] == airline) &
                          (comp_latest["Flight No."].astype(str) == str(flight)) &
                          (comp_latest["Route"] == route) &
                          (comp_latest["Cabin Class"] == cabin) &
                          (comp_latest["Departure Date"] == dep_date)]
        prv = comp_prev[(comp_prev["Airline"] == airline) &
                        (comp_prev["Flight No."].astype(str) == str(flight)) &
                        (comp_prev["Route"] == route) &
                        (comp_prev["Cabin Class"] == cabin) &
                        (comp_prev["Departure Date"] == dep_date)]
        if cur.empty or prv.empty:
            return None
        try:
            return float(cur.iloc[0]["Fare (INR)"]) - float(prv.iloc[0]["Fare (INR)"])
        except Exception:
            return None

    # ── HEADER ───────────────────────────────────────────────
    date_disp = " & ".join(d.strftime("%d %b %Y") for d in sel_dates)
    n_skus = len(snap_all)
    n_dec_today = 0
    if not ai_log_df.empty and "Log Date" in ai_log_df.columns:
        n_dec_today = int((ai_log_df["Log Date"] >= today).sum())

    st.markdown(f"""
    <div class="pid-hdr">
      <div>
        <div class="pid-title">
          <span style="color:#ffd9ee;font-weight:800">6E</span>
          &nbsp;IndiGo Pricing Intelligence</div>
        <div class="pid-sub">Real-time fare monitor · AI recommendation engine · ISB ALP 2026</div>
      </div>
      <div class="pid-ctx">
        <div>
          <div class="pid-ctx-val">{date_disp}</div>
          <div class="pid-ctx-lbl">Departure</div>
        </div>
        <div class="pid-div"></div>
        <div>
          <div class="pid-ctx-val">{n_skus}</div>
          <div class="pid-ctx-lbl">SKUs tracked</div>
        </div>
        <div class="pid-div"></div>
        <div>
          <div class="pid-ctx-val">{n_dec_today}</div>
          <div class="pid-ctx-lbl">AI calls today</div>
        </div>
        <div class="pid-div"></div>
        <div>
          <div class="pid-ctx-val">{n_dec_today * MANUAL_MINUTES_PER_SKU // 60}h
            {n_dec_today * MANUAL_MINUTES_PER_SKU % 60}m</div>
          <div class="pid-ctx-lbl">Analyst time saved</div>
        </div>
        <div class="live-pill"><div class="live-dot"></div>LIVE</div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    # ══════════ 0 · TRIAGE — EVERY SKU, RANKED BY REVENUE AT RISK ══════════
    st.markdown('<div class="sec-hd">Today\'s exceptions — every SKU ranked by '
                'revenue at risk</div>', unsafe_allow_html=True)

    triage_rows = []
    if not snap_all.empty:
        for _, r in snap_all.iterrows():
            rt, fno = str(r["Route"]), str(r["Flight No."])
            cb, dd  = str(r["Cabin Class"]), r["Departure Date"]
            ftm     = str(r.get("Departure Time", ""))
            lf      = float(r.get("Load Factor", 0) or 0)
            tot     = int(r.get("Total Seats", TOTAL_SEATS_MAP.get(rt, 180)) or 180)
            sold    = int(r.get("Seats Sold", 0) or 0)
            if sold <= 0 and lf > 0:
                sold = int(round(lf * tot))
            remaining = max(tot - sold, 0)
            dout = int(r.get("Days to Departure", 30) or 30)
            hol  = str(r.get("Holiday / Festival", "No")) == "Yes"

            cm_rows = comp_latest[(comp_latest["Route"] == rt) &
                                  (comp_latest["Cabin Class"] == cb) &
                                  (comp_latest["Departure Date"] == dd)] \
                      if not comp_latest.empty else pd.DataFrame()
            match = match_competitor(cm_rows, deph(ftm))

            pdlt = pace_delta_for(pace_curve, rt, cb, dout, lf)
            fare, bd = calc_fare(rt, cb, dout, lf, match["fare"] if match else 0,
                                 hol, deph(ftm), pace_delta=pdlt)

            gap = None
            if match and match["fare"]:
                gap = (fare - match["fare"]) / match["fare"]
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
                elif abs(gap) > 0.08 or \
                     (move_pc is not None and abs(move_pc) > 0.05):
                    flag = "amber"

            triage_rows.append({
                "route": rt, "flight": fno, "time": ftm, "cabin": cb,
                "dep": dd, "dout": dout, "lf": lf, "remaining": remaining,
                "fare": fare, "comp": match, "gap": gap, "move": move,
                "move_pc": move_pc, "risk": risk, "flag": flag,
                "pace": pdlt,
            })

    if not triage_rows:
        st.info("No SKUs found for the selected departure date. Pick a date "
                "covered by your data.")
        return

    triage_rows.sort(key=lambda x: -x["risk"])
    n_red   = sum(1 for t in triage_rows if t["flag"] == "red")
    n_amber = sum(1 for t in triage_rows if t["flag"] == "amber")

    st.markdown(
        f'<div style="font-size:0.72rem;color:#3a5080;margin-bottom:0.5rem;">'
        f'<span style="color:{RED};font-weight:700">{n_red} need action</span>'
        f' &nbsp;·&nbsp; <span style="color:{AMBER};font-weight:700">{n_amber} to watch</span>'
        f' &nbsp;·&nbsp; {len(triage_rows) - n_red - n_amber} priced sensibly'
        f' &nbsp;·&nbsp; sorted by money at stake (fare gap × unsold seats)</div>',
        unsafe_allow_html=True)

    thtml = ("""<table class="fare-tbl"><colgroup>
    <col style="width:4%"><col style="width:15%"><col style="width:11%">
    <col style="width:10%"><col style="width:7%"><col style="width:8%">
    <col style="width:9%"><col style="width:12%"><col style="width:8%">
    <col style="width:8%"><col style="width:8%"></colgroup>
    <thead><tr>
      <th></th><th>Route</th><th>Flight</th><th>Cabin</th><th>Load</th>
      <th>Pace</th><th>Our fare</th><th>Nearest competitor</th><th>Gap</th>
      <th>Overnight</th><th>At stake</th>
    </tr></thead><tbody>""")

    for t in triage_rows:
        c, dot = lf_cls(t["lf"])
        fcls = {"red": "flag-red", "amber": "flag-amber", "green": "flag-green"}[t["flag"]]
        rowc = {"red": "row-red", "amber": "row-amber", "green": ""}[t["flag"]]
        gap_s = pct(t["gap"]) if t["gap"] is not None else "—"
        gap_c = ("f-exp" if t["gap"] is not None and t["gap"] > 0.03
                 else "f-cheap" if t["gap"] is not None and t["gap"] < -0.03
                 else "f-sim")
        comp_s = (f'{t["comp"]["airline"]} {t["comp"]["time"]} '
                  f'{inr(t["comp"]["fare"])}' if t["comp"] else "—")
        if t["move"] is None:
            mv_s, mv_c = "—", ""
        elif t["move"] > 0:
            mv_s, mv_c = f'▲ {inr(t["move"])}', "dl-up"
        elif t["move"] < 0:
            mv_s, mv_c = f'▼ {inr(abs(t["move"]))}', "dl-dn"
        else:
            mv_s, mv_c = "· 0", "dl-fl"
        pace_s = ("—" if t["pace"] is None else
                  f'{"+" if t["pace"] >= 0 else ""}{t["pace"]*100:.0f}pt')
        pace_c = ("f-cheap" if t["pace"] is not None and t["pace"] >= 0.02
                  else "f-exp" if t["pace"] is not None and t["pace"] <= -0.05
                  else "f-sim")

        thtml += f"""<tr class="{rowc}">
          <td><span class="{fcls}">●</span></td>
          <td class="f-navy">{t['route'].replace(' to ',' → ')}</td>
          <td>{t['flight']} {t['time']}</td>
          <td style="color:{GREY}">{t['cabin']}</td>
          <td><span class="{c}">{dot} {round(t['lf']*100)}%</span></td>
          <td><span class="{pace_c}">{pace_s}</span></td>
          <td class="f-mag">{inr(t['fare'])}</td>
          <td style="color:{GREY};font-size:0.67rem">{comp_s}</td>
          <td><span class="{gap_c}">{gap_s}</span></td>
          <td><span class="{mv_c}">{mv_s}</span></td>
          <td class="f-navy">{inr(t['risk'])}</td>
        </tr>"""
    thtml += "</tbody></table>"
    st.markdown(thtml, unsafe_allow_html=True)
    st.markdown(f"""<div class="legend-row">
      <span style="color:{RED}">●</span> Act now: &gt;15% off market with ≤7 days
      left, or competitor moved &gt;10% overnight &nbsp;
      <span style="color:{AMBER}">●</span> Watch: &gt;8% gap or &gt;5% overnight move &nbsp;
      <span style="color:{GREEN}">●</span> Priced sensibly &nbsp;·&nbsp;
      Pace = load factor vs this route's own historical curve at the same
      days-out &nbsp;·&nbsp; At stake = fare gap × unsold seats
    </div>""", unsafe_allow_html=True)

    # Jump straight from an exception to the drill-down
    jump_opts = ["— pick an SKU to analyse —"] + [
        f"{t['route']} · {t['flight']} {t['time']} · {t['cabin']}"
        for t in triage_rows if t["flag"] != "green"]
    if len(jump_opts) > 1:
        jsel = st.selectbox("Jump to exception", jump_opts, key="jumpbox")
        if jsel != jump_opts[0]:
            jr = next(t for t in triage_rows
                      if f"{t['route']} · {t['flight']} {t['time']} · {t['cabin']}" == jsel)
            if jr["route"] != sel_route or jr["cabin"] != sel_cabin:
                st.session_state["jump_route"] = jr["route"]
                st.session_state["jump_cabin"] = jr["cabin"]
                st.rerun()

    st.markdown("<br>", unsafe_allow_html=True)
    # ══════════ 1 · DRILL-DOWN: SELECTED ROUTE & CABIN ══════════
    st.markdown(f'<div class="sec-hd">Drill-down — {sel_route} · {sel_cabin}</div>',
                unsafe_allow_html=True)

    indigo_f = snap_all[(snap_all["Route"] == sel_route) &
                        (snap_all["Cabin Class"] == sel_cabin)].copy()
    if sel_time != "All Times" and "Departure Time" in indigo_f.columns:
        indigo_f = indigo_f[indigo_f["Departure Time"].astype(str) == sel_time]

    comp_f = comp_latest[(comp_latest["Route"] == sel_route) &
                         (comp_latest["Cabin Class"] == sel_cabin)].copy() \
             if not comp_latest.empty else pd.DataFrame()

    if indigo_f.empty:
        st.info("No IndiGo flights for this route, cabin, time and date. "
                "Widen the filters or pick another date.")
        return

    fopts = (indigo_f[["Flight No.", "Departure Time",
                       "Departure Date", "Days to Departure"]]
             .drop_duplicates().sort_values(["Departure Date", "Departure Time"]))
    labels = [f"{r['Flight No.']}  ·  {r['Departure Time']}  ·  "
              f"{pd.Timestamp(r['Departure Date']).strftime('%d %b %Y')}  "
              f"({int(r['Days to Departure'])}d to departure)"
              for _, r in fopts.iterrows()]
    sel_label = st.selectbox("Select flight to analyse", labels)
    fr = fopts.iloc[labels.index(sel_label)]

    f_no   = str(fr["Flight No."])
    f_time = str(fr["Departure Time"])
    f_date = fr["Departure Date"]
    f_days = int(fr["Days to Departure"])

    frow    = indigo_f[(indigo_f["Flight No."] == f_no) &
                       (indigo_f["Departure Date"] == f_date)]
    f_lf    = float(frow["Load Factor"].iloc[0]) if not frow.empty else 0.6
    f_total = int(frow["Total Seats"].iloc[0]) if not frow.empty else 180
    _raw    = frow["Seats Sold"].iloc[0] if not frow.empty else 0
    f_sold  = 0 if pd.isna(_raw) else int(_raw)
    if f_sold <= 0 and f_lf > 0:
        f_sold = int(round(f_lf * f_total))
    f_hol   = str(frow["Holiday / Festival"].iloc[0]) if not frow.empty else "No"
    f_slot  = str(frow["Time Slot"].iloc[0]) if not frow.empty else ""

    comp_same = comp_f[comp_f["Departure Date"] == f_date] \
                if not comp_f.empty else pd.DataFrame()
    f_match = match_competitor(comp_same, deph(f_time))
    comp_list = [(str(c["Airline"]), str(c["Flight No."]),
                  str(c["Departure Time"]), int(c["Fare (INR)"]))
                 for _, c in comp_same.iterrows() if pd.notna(c.get("Fare (INR)"))]

    f_pace = pace_delta_for(pace_curve, sel_route, sel_cabin, f_days, f_lf)
    arith, bd = calc_fare(sel_route, sel_cabin, f_days, f_lf,
                          f_match["fare"] if f_match else 0,
                          f_hol == "Yes", deph(f_time),
                          pax_type, trip_type, pace_delta=f_pace)

    # Latest AI log entry for this flight
    ai_today, mgr_today = "—", "Pending"
    if not ai_log_df.empty and "Flight No." in ai_log_df.columns:
        _log = ai_log_df.copy()
        _log["_dk"] = _log["Departure Date"].map(dkey)
        tl = _log[(_log["Flight No."].astype(str) == f_no) &
                  (_log.get("Cabin Class", pd.Series(dtype=str)).astype(str)
                   == sel_cabin) &
                  (_log["_dk"] == dkey(f_date))]
        if not tl.empty:
            ai_today  = inr(tl.iloc[-1].get("AI Suggested Fare", ""))
            mgr_today = str(tl.iloc[-1].get("Manager Decision", "Pending") or "Pending")

    # Manager override, today only
    ov_today = "—"
    if not feedback_df.empty and "Timestamp" in feedback_df.columns:
        fb = feedback_df.copy()
        fb["_ts"] = pd.to_datetime(fb["Timestamp"], errors="coerce")
        fb["_dk"] = fb.get("Departure Date", pd.Series(dtype=str)).map(dkey)
        m = fb[(fb.get("Flight No.", pd.Series(dtype=str)).astype(str) == f_no) &
               (fb["_dk"] == dkey(f_date)) &
               (fb["_ts"] >= today) &
               (fb.get("Manager Decision", pd.Series(dtype=str)) == "Overridden")]
        if not m.empty:
            ov_today = inr(m.iloc[-1].get("Final Fare Used", ""))

    cseat         = seat_cost(sel_route, sel_cabin)
    profit_seat   = arith - cseat
    flight_profit = profit_seat * f_sold

    pace_val = "—" if f_pace is None else \
               f'{"+" if f_pace >= 0 else ""}{f_pace*100:.0f}pt'
    pace_kls = ("k-green" if f_pace is not None and f_pace >= 0.02 else
                "k-red" if f_pace is not None and f_pace <= -0.05 else "k-amber")
    pace_sub = ("vs this route's booking curve" if f_pace is not None
                else "no history at this days-out")

    st.markdown(f"""
    <div class="kpi-strip six">
      <div class="kpi-card">
        <div class="kpi-val {lf_kpi(f_lf)}">{round(f_lf*100,1)}%</div>
        <div class="kpi-lbl">Load factor</div>
        <div class="kpi-sub">{f_sold}/{f_total} seats booked</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-val {pace_kls}">{pace_val}</div>
        <div class="kpi-lbl">Booking pace</div>
        <div class="kpi-sub">{pace_sub}</div>
      </div>
      <div class="kpi-card accent">
        <div class="kpi-val k-mag">{inr(arith)}</div>
        <div class="kpi-lbl">Arithmetic fare</div>
        <div class="kpi-sub">{sel_cabin} base {inr(bd['cabin_base'])}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-val k-navy">{ai_today}</div>
        <div class="kpi-lbl">AI recommended</div>
        <div class="kpi-sub">Manager: {mgr_today}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-val k-amber">{ov_today}</div>
        <div class="kpi-lbl">Manager override</div>
        <div class="kpi-sub">Valid today only</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-val {'k-green' if flight_profit > 0 else 'k-red'}">{inr(flight_profit)}</div>
        <div class="kpi-lbl">Est. flight profit</div>
        <div class="kpi-sub">{inr(profit_seat)}/seat · cost {inr(cseat)}</div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    # ══════════ 2 · AI PANEL ══════════
    st.markdown('<div class="sec-hd">AI pricing recommendation</div>',
                unsafe_allow_html=True)

    lfc, lfd = lf_cls(f_lf)
    match_s = (f'{f_match["airline"]} {f_match["flight"]} at {f_match["time"]} '
               f'— {inr(f_match["fare"])} ({f_match["gap"]:.0f}h away)'
               if f_match else "no competitor on a comparable schedule")
    st.markdown(f"""
    <div class="flt-pill">
      <span class="flt-pill-t">{f_no} · {f_time} · {f_slot}</span>
      &nbsp;&nbsp;Load <span class="{lfc}">{lfd} {round(f_lf*100,1)}%</span>
      &nbsp;·&nbsp; {f_sold}/{f_total} seats
      &nbsp;·&nbsp; {f_days} days out
      &nbsp;·&nbsp; time-matched competitor: {match_s}
    </div>""", unsafe_allow_html=True)

    a_left, a_right = st.columns([1, 1.25], gap="large")

    with a_left:
        st.markdown('<div style="font-size:0.6rem;font-weight:700;color:#1B2D6B;'
                    'text-transform:uppercase;letter-spacing:0.09em;'
                    'margin-bottom:0.35rem;">How the arithmetic fare is built</div>',
                    unsafe_allow_html=True)
        rows = [
            f'<div class="bd-row"><span>Route base (Economy)</span>'
            f'<span>{inr(bd["route_base"])}</span></div>',
            f'<div class="bd-row"><span class="bd-neu">{sel_cabin} tier '
            f'× {bd["tier_mult"]:.2f}</span>'
            f'<span>{inr(bd["cabin_base"])}</span></div>',
            f'<div class="bd-row"><span style="color:#1B2D6B;font-weight:600">'
            f'Demand signals</span><span></span></div>',
        ]
        for lbl, v in bd["items"]:
            cls  = "bd-pos" if v > 0 else ("bd-neg" if v < 0 else "bd-neu")
            sign = "+" if v > 0 else ""
            rows.append(f'<div class="bd-row"><span class="bd-neu">&nbsp;&nbsp;{lbl}'
                        f'</span><span class="{cls}">{sign}{v*100:.0f}%</span></div>')
        ccls = ("bd-pos" if bd["comp_adj"] > 0 else
                "bd-neg" if bd["comp_adj"] < 0 else "bd-neu")
        rows.append(f'<div class="bd-row"><span class="bd-neu">&nbsp;&nbsp;'
                    f'{bd["comp_label"]}</span>'
                    f'<span class="{ccls}">{bd["comp_adj"]*100:+.0f}%</span></div>')
        cap_note = " (capped)" if bd["capped"] else ""
        rows.append(f'<div class="bd-row"><span>Net demand adjustment{cap_note}</span>'
                    f'<span>{bd["total_demand"]*100:+.1f}%</span></div>')
        rows.append(f'<div class="bd-row"><span>Arithmetic fare</span>'
                    f'<span style="color:#E91E8C">{inr(bd["final"])}</span></div>')
        st.markdown("<div class='arith-box'>" + "".join(rows) + "</div>",
                    unsafe_allow_html=True)
        st.caption(f"{sel_cabin} is a product tier multiplier applied before demand "
                   f"logic; demand itself is capped at "
                   f"{DEMAND_CAP_LO*100:.0f}% to +{DEMAND_CAP_HI*100:.0f}%.")

    with a_right:
        route_default = standing.get(sel_route, STRATEGIC_OPTIONS[0])
        idx = STRATEGIC_OPTIONS.index(route_default) \
              if route_default in STRATEGIC_OPTIONS else 0
        strategy = st.selectbox(
            f"Strategic direction for {sel_route} (standing — remembered per route)",
            STRATEGIC_OPTIONS, index=idx)
        if strategy != route_default and st.button("📌  Save as route strategy"):
            try:
                save_strategy(sel_route, strategy, analyst or "Unknown")
                st.success(f"Standing strategy saved for {sel_route}.")
                st.cache_data.clear()
            except Exception as e:
                st.warning(f"Could not save strategy: {e}")

        if st.button("🤖  Get AI recommendation"):
            if not (analyst or "").strip():
                st.error("Enter your analyst name in the sidebar first — every "
                         "decision is logged with who made it.")
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
                    cabin=sel_cabin, dep_date=str(f_date)[:10],
                    days_to_dep=f_days, load_factor=f_lf, pace_delta=f_pace,
                    arithmetic_fare=arith, bd=bd, comp_match=f_match,
                    comp_all=comp_list, strategy=strategy, history=hist,
                    pax_type=pax_type, trip_type=trip_type)

                with st.spinner("Asking the pricing analyst..."):
                    dec, fare, rat, engine, note = call_llm(
                        prompt_args, bd, arith, f_match, f_lf)

                try:
                    save_ai_log({"Log Date": datetime.now().strftime("%Y-%m-%d %H:%M"),
                                 "Analyst": analyst, "Route": sel_route,
                                 "Flight No.": f_no, "Departure Time": f_time,
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
                    st.warning(f"Recommendation received but could not be logged: {e}")
                st.session_state["ai"] = {
                    "dec": dec, "fare": fare, "rat": rat, "arith": arith,
                    "flt": f_no, "time": f_time, "date": dkey(f_date),
                    "days": f_days, "lf": f_lf, "sold": f_sold,
                    "strategy": strategy, "engine": engine, "note": note,
                    "snapshot": snapshot}
                st.rerun()

        if "ai" in st.session_state:
            r  = st.session_state["ai"]
            ok = str(r["dec"]).lower().startswith("approve")
            badge = "ai-badge-ok" if ok else "ai-badge-ov"
            btxt  = "✔ Approves arithmetic fare" if ok else "⚡ Overrides arithmetic fare"
            eng   = r.get("engine", "")
            is_fb = eng.startswith("Rules")
            hdr   = "Rules-based recommendation" if is_fb else "AI recommendation"
            c_bg  = "#fef3c7" if is_fb else "#e8f0fe"
            c_bd  = "#D97706" if is_fb else "#2F6FD0"
            c_tx  = "#b45309" if is_fb else "#1B2D6B"

            st.markdown(f"""
            <div class="ai-result">
              <div style="display:flex;align-items:center;justify-content:space-between;">
                <span style="font-size:0.6rem;font-weight:700;color:#1B2D6B;
                      text-transform:uppercase;letter-spacing:0.1em;">{hdr}</span>
                <span class="{badge}">{btxt}</span>
              </div>
              <div class="ai-price">{inr(r['fare'])}</div>
              <div class="ai-rat">{r['rat']}</div>
              <div style="margin-top:0.5rem;">
                <span class="engine-chip" style="background:{c_bg};
                      border:1px solid {c_bd};color:{c_tx};">Engine: {eng}</span>
              </div>
            </div>""", unsafe_allow_html=True)

            if is_fb:
                st.caption("No AI engine was reachable — this fare comes from the "
                           "arithmetic rules only and is not an AI recommendation.")
            if r.get("note"):
                with st.expander("Engine diagnostics"):
                    st.code(r["note"])

            st.markdown(f"**Manager decision** — applies to today only · "
                        f"logged as **{analyst or 'Unknown'}**")

            def commit(kind, final_fare):
                base_row = {
                    "Analyst": analyst or "Unknown", "Route": sel_route,
                    "Flight No.": r["flt"], "Departure Time": r["time"],
                    "Departure Date": r["date"], "Cabin Class": sel_cabin,
                    "Days to Departure": r["days"],
                    "Load Factor": round(r["lf"] * 100, 1),
                    "Seats At Decision": r["sold"],
                    "Arithmetic Fare": r["arith"], "AI Decision": r["dec"],
                    "AI Suggested Fare": r["fare"], "AI Rationale": r["rat"],
                    "Engine": r.get("engine", ""),
                    "Competitor Snapshot": r.get("snapshot", ""),
                    "Strategic Direction": r["strategy"],
                    "Manager Decision": kind, "Final Fare Used": final_fare}
                save_feedback({**base_row,
                               "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
                               "Passenger Type": pax_type, "Trip Type": trip_type,
                               "Manager Notes": ""})
                save_ai_log({**base_row,
                             "Log Date": datetime.now().strftime("%Y-%m-%d %H:%M")})

            m1, m2 = st.columns(2)
            with m1:
                if st.button("✔  Accept this fare"):
                    try:
                        commit("Accepted", r["fare"])
                        st.success(f"Accepted at {inr(r['fare'])}.")
                        del st.session_state["ai"]
                        st.cache_data.clear()
                        st.rerun()
                    except Exception as e:
                        st.error(f"Could not save: {e}")
            with m2:
                ov = st.number_input("Your fare (₹)", min_value=500, max_value=500000,
                                     value=int(r["fare"]), step=100, key="ovval")
                if st.button("✏  Use my fare instead"):
                    try:
                        commit("Overridden", int(ov))
                        st.success(f"Override saved at {inr(ov)}.")
                        del st.session_state["ai"]
                        st.cache_data.clear()
                        st.rerun()
                    except Exception as e:
                        st.error(f"Could not save: {e}")

    st.markdown("<br>", unsafe_allow_html=True)
    # ══════════ 3 · FARE COMPARISON TABLE ══════════
    st.markdown('<div class="sec-hd">Fare comparison — time-matched competitors</div>',
                unsafe_allow_html=True)

    acc_lookup, log_lookup = {}, {}
    if not feedback_df.empty and "Manager Decision" in feedback_df.columns:
        for _, x in feedback_df[feedback_df["Manager Decision"]
                                .isin(["Accepted", "Overridden"])].iterrows():
            k = (str(x.get("Flight No.", "")), str(x.get("Cabin Class", "")),
                 dkey(x.get("Departure Date", "")))
            try:
                acc_lookup[k] = int(x.get("Final Fare Used", 0))
            except Exception:
                pass
    if not ai_log_df.empty and "Flight No." in ai_log_df.columns:
        for _, x in ai_log_df.iterrows():
            k = (str(x.get("Flight No.", "")), str(x.get("Cabin Class", "")),
                 dkey(x.get("Departure Date", "")))
            try:
                log_lookup[k] = int(x.get("AI Suggested Fare", 0))
            except Exception:
                pass

    cabin_base_disp = int(BASE_FARES.get(sel_route, 5000)
                          * CABIN_MULT.get(sel_cabin, 1.0))

    html = ("""<table class="fare-tbl"><colgroup>
    <col style="width:13%"><col style="width:10%"><col style="width:8%">
    <col style="width:8%"><col style="width:7%"><col style="width:10%">
    <col style="width:10%"><col style="width:10%"><col style="width:16%">
    <col style="width:8%"></colgroup>
    <thead><tr>
      <th>IndiGo flight</th><th>Slot</th><th>Load</th><th>Seats</th>
      <th>Pace</th><th>Cabin base</th><th>Arithmetic</th><th>AI rec</th>
      <th>Nearest competitor</th><th>Gap</th>
    </tr></thead><tbody>""")

    cur_date = None
    for _, row in indigo_f.sort_values(["Departure Date", "Departure Time"]).iterrows():
        dd   = row["Departure Date"]
        fno  = str(row.get("Flight No.", ""))
        ftm  = str(row.get("Departure Time", ""))
        slot = str(row.get("Time Slot", ""))
        dout = int(row.get("Days to Departure", 30) or 30)
        lf   = float(row.get("Load Factor", 0) or 0)
        tot  = int(row.get("Total Seats", 180) or 180)
        sold = int(row.get("Seats Sold", 0) or 0)
        if sold <= 0 and lf > 0:
            sold = int(round(lf * tot))
        hol  = str(row.get("Holiday / Festival", "No"))
        c, dot = lf_cls(lf)

        if cur_date is None or dd != cur_date:
            cur_date = dd
            lbl = dd.strftime("%A, %d %B %Y") if hasattr(dd, "strftime") else str(dd)[:10]
            html += (f'<tr class="date-sep"><td colspan="10">✈ {lbl}'
                     f' &nbsp;—&nbsp; {dout} days to departure</td></tr>')

        rows_c = comp_f[comp_f["Departure Date"] == dd] \
                 if not comp_f.empty else pd.DataFrame()
        mt = match_competitor(rows_c, deph(ftm))
        pdl = pace_delta_for(pace_curve, sel_route, sel_cabin, dout, lf)
        ar, _bd = calc_fare(sel_route, sel_cabin, dout, lf,
                            mt["fare"] if mt else 0, hol == "Yes", deph(ftm),
                            pax_type, trip_type, pace_delta=pdl)

        dk = dkey(dd)
        rec = acc_lookup.get((fno, sel_cabin, dk)) or log_lookup.get((fno, sel_cabin, dk))
        rec_cls = "f-ai" if acc_lookup.get((fno, sel_cabin, dk)) else "f-ailog"

        if mt:
            comp_s = f'{mt["airline"]} {mt["flight"]} {mt["time"]} · {inr(mt["fare"])}'
            g = (ar - mt["fare"]) / mt["fare"]
            gap_s = pct(g)
            gcls = "f-exp" if g > 0.03 else ("f-cheap" if g < -0.03 else "f-sim")
        else:
            comp_s, gap_s, gcls = "—", "—", ""

        pace_s = ("—" if pdl is None else
                  f'{"+" if pdl >= 0 else ""}{pdl*100:.0f}pt')
        pcls = ("f-cheap" if pdl is not None and pdl >= 0.02
                else "f-exp" if pdl is not None and pdl <= -0.05 else "f-sim")

        html += f"""<tr>
          <td class="f-navy">{fno} {ftm}</td>
          <td style="color:{GREY};font-size:0.67rem">{slot}</td>
          <td><span class="{c}">{dot} {round(lf*100,1)}%</span></td>
          <td style="color:{GREY}">{sold}/{tot}</td>
          <td><span class="{pcls}">{pace_s}</span></td>
          <td class="f-navy">{inr(cabin_base_disp)}</td>
          <td class="f-mag">{inr(ar)}</td>
          <td class="{rec_cls}">{inr(rec) if rec else '—'}</td>
          <td style="color:{GREY};font-size:0.67rem">{comp_s}</td>
          <td><span class="{gcls}">{gap_s}</span></td>
        </tr>"""

    html += "</tbody></table>"
    st.markdown(html, unsafe_allow_html=True)
    st.markdown(f"""<div class="legend-row">
      Cabin base = route base × {sel_cabin} tier multiplier
      ({CABIN_MULT.get(sel_cabin, 1.0):.2f}×) &nbsp;·&nbsp;
      <span style="color:{MAGENTA}">■</span> Arithmetic fare for your filters &nbsp;
      <span style="color:{SKY}">■</span> AI rec accepted by manager &nbsp;
      <span style="color:#0891b2">■</span> AI rec suggested, pending &nbsp;·&nbsp;
      Competitor shown is the nearest by departure time, not the day's cheapest
    </div>""", unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ══════════ 4 · CHARTS ══════════
    c_left, c_right = st.columns([1, 1.15], gap="large")

    # 4a — Booking build-up with pace target
    with c_left:
        st.markdown('<div class="sec-hd">Booking build-up vs route pace</div>',
                    unsafe_allow_html=True)

        flights = []
        subr = indigo_df[indigo_df["Route"] == sel_route]
        if not subr.empty:
            flights = sorted(
                subr.apply(lambda r: f"{r['Flight No.']} {r['Departure Time']}",
                           axis=1).dropna().unique().tolist())

        if not flights:
            st.info("No flights available for this route.")
        else:
            cur = f"{f_no} {f_time}"
            pick = st.selectbox("Flight", flights,
                                index=flights.index(cur) if cur in flights else 0,
                                key="lfpick")
            pno = pick.split(" ")[0]
            hist = indigo_df[(indigo_df["Route"] == sel_route) &
                             (indigo_df["Cabin Class"] == sel_cabin) &
                             (indigo_df["Flight No."].astype(str) == pno) &
                             (indigo_df["Departure Date"].isin(sel_dates))].copy()

            if hist.empty or dcol not in hist.columns:
                st.info("No booking history for this flight on the selected date.")
            else:
                hist = hist.dropna(subset=[dcol]).sort_values(dcol)
                hist = hist.groupby(dcol, as_index=False).last()
                hist["LF%"] = (pd.to_numeric(hist["Load Factor"], errors="coerce")
                               * 100).round(1)
                seats = pd.to_numeric(hist["Seats Sold"], errors="coerce")
                hist["New bookings"] = seats.diff().fillna(seats).clip(lower=0)
                hist["dout"] = pd.to_numeric(hist["Days to Departure"],
                                             errors="coerce")
                hist["Target%"] = hist["dout"].map(
                    lambda d: (pace_curve.get((sel_route, sel_cabin, int(d)))
                               if pd.notna(d) else None))
                hist["Target%"] = (pd.to_numeric(hist["Target%"], errors="coerce")
                                   * 100).round(1)

                fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                                    vertical_spacing=0.08, row_heights=[0.4, 0.6])
                fig.add_trace(go.Bar(
                    x=hist[dcol], y=hist["New bookings"],
                    name="New bookings that day",
                    marker_color="rgba(233,30,140,0.55)",
                    hovertemplate="%{y:.0f} seats<extra></extra>"), row=1, col=1)
                fig.add_trace(go.Scatter(
                    x=hist[dcol], y=hist["LF%"], name="This flight's load factor",
                    mode="lines+markers", line=dict(color=NAVY, width=2.5),
                    marker=dict(size=5, color=NAVY), fill="tozeroy",
                    fillcolor="rgba(27,45,107,0.07)",
                    hovertemplate="%{y:.1f}%<extra></extra>"), row=2, col=1)
                if hist["Target%"].notna().any():
                    fig.add_trace(go.Scatter(
                        x=hist[dcol], y=hist["Target%"],
                        name="Route's typical pace",
                        mode="lines", line=dict(color=AMBER, width=2, dash="dot"),
                        hovertemplate="%{y:.1f}%<extra></extra>"), row=2, col=1)
                fig.add_hline(y=85, line_dash="dot", line_color=RED,
                              line_width=1, row=2, col=1)
                fig.update_yaxes(title_text="Seats", row=1, col=1)
                fig.update_yaxes(title_text="Load %", range=[0, 105], row=2, col=1)
                fig.update_xaxes(title_text="Observation date", row=2, col=1)
                style_chart(fig, height=340)
                st.plotly_chart(fig, use_container_width=True)
                st.caption(f"{pick} against the average booking curve of "
                           f"{sel_route} {sel_cabin} at the same days-out. "
                           "Above the dotted amber line = ahead of pace.")

    # 4b — Price trend, matched-competitor granularity
    with c_right:
        st.markdown('<div class="sec-hd">Price trend — this flight vs its '
                    'time-matched competitors</div>', unsafe_allow_html=True)
        frames = []

        # Competitor fares over scrape history, only flights within 3h of ours
        ch = comp_df[(comp_df["Route"] == sel_route) &
                     (comp_df["Cabin Class"] == sel_cabin) &
                     (comp_df["Departure Date"].isin(sel_dates)) &
                     (comp_df["Scrape Date"] >= dmin)].copy() \
             if not comp_df.empty and "Scrape Date" in comp_df.columns \
             else pd.DataFrame()
        if not ch.empty:
            ch["_gap"] = ch["Departure Time"].map(
                lambda t: clock_gap(deph(t), deph(f_time)))
            near = ch[ch["_gap"] <= 3.0]
            if near.empty:
                near = ch
            g = (near.groupby(["Scrape Date", "Airline"])["Fare (INR)"]
                 .mean().reset_index())
            g.columns = ["Date", "Series", "Fare"]
            frames.append(g)

        # Our arithmetic fare recomputed for each historical day, THIS flight only
        ih = indigo_df[(indigo_df["Route"] == sel_route) &
                       (indigo_df["Cabin Class"] == sel_cabin) &
                       (indigo_df["Flight No."].astype(str) == f_no) &
                       (indigo_df["Departure Date"].isin(sel_dates))].copy()
        if not ih.empty and dcol in ih.columns:
            ih = ih.dropna(subset=[dcol])
            ih = ih[ih[dcol] >= dmin].sort_values(dcol)
            rows = []
            for _, g2 in ih.iterrows():
                d = g2[dcol]
                day_comp = ch[ch["Scrape Date"] == d] if not ch.empty else pd.DataFrame()
                mtd = match_competitor(day_comp, deph(f_time)) \
                      if not day_comp.empty else None
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
                rows.append({"Date": d, "Series": "IndiGo arithmetic", "Fare": v})
            if rows:
                frames.append(pd.DataFrame(rows))

        # AI recommendations for THIS flight and cabin
        if not ai_log_df.empty and "Log Date" in ai_log_df.columns:
            al = ai_log_df.copy()
            al["_dk"] = al.get("Departure Date",
                               pd.Series(dtype=str)).map(dkey)
            al = al[(al.get("Route", "") == sel_route) &
                    (al.get("Cabin Class", "") == sel_cabin) &
                    (al.get("Flight No.", pd.Series(dtype=str)).astype(str) == f_no) &
                    (al["_dk"] == dkey(f_date))]
            if not al.empty and "AI Suggested Fare" in al.columns:
                al["AI Suggested Fare"] = pd.to_numeric(al["AI Suggested Fare"],
                                                        errors="coerce")
                al["_d"] = pd.to_datetime(al["Log Date"],
                                          errors="coerce").dt.normalize()
                al = al.dropna(subset=["_d", "AI Suggested Fare"])
                if not al.empty:
                    g = al.groupby("_d")["AI Suggested Fare"].mean().reset_index()
                    g.columns = ["Date", "Fare"]
                    g["Series"] = "IndiGo AI recommended"
                    frames.append(g[["Date", "Series", "Fare"]])

        if frames:
            allt = pd.concat(frames, ignore_index=True).dropna(subset=["Fare"])
            fig2 = px.line(allt, x="Date", y="Fare", color="Series", markers=True,
                           color_discrete_map={"Air India": SKY, "Akasa Air": RED,
                                               "IndiGo arithmetic": MAGENTA,
                                               "IndiGo AI recommended": NAVY})
            for tr in fig2.data:
                if tr.name in ("IndiGo arithmetic", "IndiGo AI recommended"):
                    tr.line.dash = "dash"
                    tr.line.width = 2.5
            fig2.update_yaxes(title_text="Fare (₹)")
            fig2.update_xaxes(title_text="")
            style_chart(fig2, height=340)
            st.plotly_chart(fig2, use_container_width=True)
            st.caption("Every line describes the same product: this flight and "
                       "cabin, with competitors filtered to departures within 3 "
                       "hours. Dashed = IndiGo's own arithmetic and AI fares.")
        else:
            st.info("No trend data yet for this flight.")

    # 4c — Demand curve scatter, full width
    st.markdown('<div class="sec-hd">Demand curve — fare vs load factor, '
                'every observation</div>', unsafe_allow_html=True)

    sc = indigo_df[(indigo_df["Route"] == sel_route) &
                   (indigo_df["Cabin Class"] == sel_cabin)].copy()
    if sc.empty or dcol not in sc.columns:
        st.info("Not enough history for a demand scatter yet.")
    else:
        sc = sc.dropna(subset=["Load Factor", "Days to Departure"])
        pts = []
        for _, g2 in sc.iterrows():
            dl = pace_delta_for(pace_curve, sel_route, sel_cabin,
                                int(g2["Days to Departure"]),
                                float(g2["Load Factor"]))
            v, _ = calc_fare(sel_route, sel_cabin, int(g2["Days to Departure"]),
                             float(g2["Load Factor"]), 0,
                             str(g2.get("Holiday / Festival", "No")) == "Yes",
                             deph(g2.get("Departure Time", "10:00")),
                             pace_delta=dl)
            pts.append({"Fare": v, "LF": float(g2["Load Factor"]) * 100,
                        "Days out": int(g2["Days to Departure"]),
                        "Flight": f"{g2['Flight No.']} {g2['Departure Time']}"})
        spd = pd.DataFrame(pts)
        fig4 = px.scatter(spd, x="Fare", y="LF", color="Flight",
                          size="Days out", size_max=13, opacity=0.65,
                          color_discrete_sequence=[NAVY, MAGENTA, SKY, AMBER])
        fig4.update_yaxes(title_text="Load factor %", range=[0, 105])
        fig4.update_xaxes(title_text="Rules-based fare (₹)")
        style_chart(fig4, height=300)
        st.plotly_chart(fig4, use_container_width=True)
        st.caption("Each dot is one daily observation of one flight. Bigger dots "
                   "are further from departure. This is the route's demand "
                   "picture: how fill rate and our pricing move together.")

    st.markdown("<br>", unsafe_allow_html=True)
    # ══════════ 5 · BACK-TEST: DYNAMIC RULES vs FLAT BASE PRICING ══════════
    st.markdown('<div class="sec-hd">Back-test — what the rules engine would have '
                'earned vs flat pricing</div>', unsafe_allow_html=True)

    bt_scope = st.radio("Back-test scope", ["This route & cabin", "All routes"],
                        horizontal=True, key="bt_scope")

    bt = indigo_df.copy()
    if bt_scope == "This route & cabin":
        bt = bt[(bt["Route"] == sel_route) & (bt["Cabin Class"] == sel_cabin)]
    if bt.empty or dcol not in bt.columns:
        st.info("Not enough history to back-test yet.")
    else:
        bt = bt.dropna(subset=[dcol, "Load Factor", "Days to Departure"]).copy()
        bt = bt.sort_values(dcol)
        keys = ["Route", "Flight No.", "Cabin Class", "Departure Date"]
        bt["Seats Sold"] = pd.to_numeric(bt["Seats Sold"], errors="coerce")
        bt["_new"] = (bt.groupby(keys)["Seats Sold"].diff()
                      .fillna(bt["Seats Sold"]).clip(lower=0))

        dyn_rev, flat_rev, dyn_prof, flat_prof, n_obs = 0.0, 0.0, 0.0, 0.0, 0
        for _, g2 in bt.iterrows():
            new = float(g2["_new"] or 0)
            if new <= 0:
                continue
            rt, cb = str(g2["Route"]), str(g2["Cabin Class"])
            dl = pace_delta_for(pace_curve, rt, cb,
                                int(g2["Days to Departure"]),
                                float(g2["Load Factor"]))
            v, _ = calc_fare(rt, cb, int(g2["Days to Departure"]),
                             float(g2["Load Factor"]), 0,
                             str(g2.get("Holiday / Festival", "No")) == "Yes",
                             deph(g2.get("Departure Time", "10:00")),
                             pace_delta=dl)
            flat = BASE_FARES.get(rt, 5000) * CABIN_MULT.get(cb, 1.0)
            cst  = seat_cost(rt, cb)
            dyn_rev  += v * new
            flat_rev += flat * new
            dyn_prof  += (v - cst) * new
            flat_prof += (flat - cst) * new
            n_obs += 1

        if n_obs == 0:
            st.info("No booking movements found in the history to price against.")
        else:
            uplift = dyn_rev - flat_rev
            upct = (uplift / flat_rev * 100) if flat_rev else 0
            b1, b2, b3, b4 = st.columns(4)
            b1.metric("Bookings priced", f"{int(bt['_new'].sum()):,} seats")
            b2.metric("Revenue — flat base pricing", inr(flat_rev))
            b3.metric("Revenue — dynamic rules", inr(dyn_rev))
            b4.metric("Uplift from dynamic pricing", inr(uplift),
                      delta=f"{upct:+.1f}%")
            st.caption("Every booking observed in the historical data is re-priced "
                       "two ways: at the flat cabin base fare, and at what the "
                       "rules engine would have charged that day given the "
                       "actual load factor and days-out. The gap is the value of "
                       "pricing dynamically. Competitor corrections are excluded "
                       "here so the comparison isolates the demand rules.")

    st.markdown("<br>", unsafe_allow_html=True)

    # ══════════ 6 · RECOMMENDATION HISTORY — FEEDBACK LOOP DATA BANK ══════════
    st.markdown('<div class="sec-hd">Recommendation history — feedback loop '
                'data bank</div>', unsafe_allow_html=True)

    if ai_log_df.empty:
        st.info("No recommendations logged yet. Each AI call is recorded with its "
                "rationale, the competitor snapshot it saw, who decided, and what "
                "bookings did next — and this history feeds future prompts.")
    else:
        hist_df = ai_log_df.copy()

        h1, h2, h3 = st.columns([1, 1, 2])
        with h1:
            scope = st.radio("Show", ["This route", "All routes"],
                             index=0, horizontal=True, key="hist_scope")
        with h2:
            outcome = st.selectbox("Outcome", ["All", "Accepted", "Overridden",
                                               "Pending"], key="hist_outcome")

        if scope == "This route" and "Route" in hist_df.columns:
            hist_df = hist_df[hist_df["Route"] == sel_route]
        if outcome != "All" and "Manager Decision" in hist_df.columns:
            hist_df = hist_df[hist_df["Manager Decision"] == outcome]

        keys = [c for c in ["Flight No.", "Cabin Class", "Departure Date"]
                if c in hist_df.columns]
        if keys and not hist_df.empty and "Log Date" in hist_df.columns:
            hist_df["_ld"] = pd.to_datetime(hist_df["Log Date"], errors="coerce")
            hist_df["_day"] = hist_df["_ld"].dt.normalize()
            hist_df = (hist_df.sort_values("_ld")
                       .drop_duplicates(subset=keys + ["_day"], keep="last"))

        if hist_df.empty:
            st.info("No entries match this filter.")
        else:
            # Outcome tracking: bookings in the ~24h after each decision
            def bookings_after(row):
                try:
                    fno = str(row.get("Flight No.", ""))
                    cb  = str(row.get("Cabin Class", ""))
                    dk  = dkey(row.get("Departure Date", ""))
                    d0  = pd.to_datetime(row.get("Log Date"),
                                         errors="coerce").normalize()
                    s0  = pd.to_numeric(row.get("Seats At Decision"),
                                        errors="coerce")
                    if pd.isna(d0) or pd.isna(s0):
                        return None
                    obs = indigo_df[
                        (indigo_df["Flight No."].astype(str) == fno) &
                        (indigo_df["Cabin Class"].astype(str) == cb) &
                        (indigo_df["Departure Date"].map(dkey) == dk) &
                        (indigo_df[dcol] > d0) &
                        (indigo_df[dcol] <= d0 + timedelta(days=1))]
                    if obs.empty:
                        return None
                    s1 = pd.to_numeric(obs.sort_values(dcol).iloc[-1]["Seats Sold"],
                                       errors="coerce")
                    if pd.isna(s1):
                        return None
                    return int(s1 - s0)
                except Exception:
                    return None

            hist_df["Bookings +24h"] = hist_df.apply(bookings_after, axis=1)

            with h3:
                mgr = hist_df.get("Manager Decision", pd.Series(dtype=str))
                n_acc = int((mgr == "Accepted").sum())
                n_ovr = int((mgr == "Overridden").sum())
                n_pen = int((mgr == "Pending").sum())
                total = max(len(hist_df), 1)
                st.markdown(
                    f'<div style="padding-top:1.1rem;font-size:0.72rem;color:#3a5080;">'
                    f'<b>{len(hist_df)}</b> recommendations &nbsp;·&nbsp; '
                    f'<span style="color:{GREEN}">{n_acc} accepted</span> &nbsp;·&nbsp; '
                    f'<span style="color:{AMBER}">{n_ovr} overridden</span> &nbsp;·&nbsp; '
                    f'<span style="color:{GREY}">{n_pen} pending</span> &nbsp;·&nbsp; '
                    f'AI acceptance rate <b>{n_acc/total*100:.0f}%</b>'
                    f'</div>', unsafe_allow_html=True)

            show = [c for c in ["Log Date", "Analyst", "Route", "Flight No.",
                                "Cabin Class", "Departure Date", "Load Factor",
                                "Arithmetic Fare", "AI Suggested Fare", "Engine",
                                "Manager Decision", "Final Fare Used",
                                "Bookings +24h", "Strategic Direction",
                                "Competitor Snapshot", "AI Rationale"]
                    if c in hist_df.columns]
            st.dataframe(hist_df.sort_values("_ld", ascending=False)[show],
                         use_container_width=True, hide_index=True,
                         column_config={
                             "AI Rationale": st.column_config.TextColumn(
                                 "AI rationale", width="large"),
                             "Competitor Snapshot": st.column_config.TextColumn(
                                 "Competitors seen at decision", width="medium"),
                         })
            st.caption("Full audit trail: what the AI saw, what it said, who "
                       "decided, and whether bookings actually moved in the next "
                       "24 hours. Accepted and overridden entries are injected "
                       "into future prompts — the feedback loop your mentor "
                       "asked for, with outcomes rather than opinions.")

    st.markdown("<br>", unsafe_allow_html=True)

    # ══════════ 7 · PROFITABILITY FROM APPROVED FARES ══════════
    st.markdown('<div class="sec-hd">Profitability from manager-approved fares</div>',
                unsafe_allow_html=True)

    if feedback_df.empty or "Manager Decision" not in feedback_df.columns:
        st.info("No manager decisions recorded yet. Accept or override a "
                "recommendation above and the profit impact will appear here.")
    else:
        acc = feedback_df[feedback_df["Manager Decision"]
                          .isin(["Accepted", "Overridden"])].copy()
        if acc.empty:
            st.info("No accepted recommendations yet.")
        else:
            acc["Final Fare Used"] = pd.to_numeric(acc["Final Fare Used"],
                                                   errors="coerce")
            acc["_cost"] = acc.apply(
                lambda r: seat_cost(str(r.get("Route", "")),
                                    str(r.get("Cabin Class", "Economy"))), axis=1)
            acc["Profit Per Seat"] = acc["Final Fare Used"] - acc["_cost"]
            acc["LF"] = pd.to_numeric(acc["Load Factor"], errors="coerce") / 100
            acc["Seats"] = acc["Route"].map(TOTAL_SEATS_MAP).fillna(180)
            acc["Flight Profit"] = acc["Profit Per Seat"] * acc["Seats"] * acc["LF"]
            acc["_base"] = acc.apply(
                lambda r: BASE_FARES.get(str(r.get("Route", "")), 5000)
                          * CABIN_MULT.get(str(r.get("Cabin Class", "Economy")), 1.0),
                axis=1)
            acc["Revenue Uplift"] = ((acc["Final Fare Used"] - acc["_base"])
                                     * acc["Seats"] * acc["LF"])

            p1, p2, p3, p4 = st.columns(4)
            p1.metric("Decisions recorded", len(acc))
            p2.metric("Revenue uplift vs cabin base", inr(acc["Revenue Uplift"].sum()))
            p3.metric("Avg profit per seat", inr(acc["Profit Per Seat"].mean()))
            p4.metric("Est. total flight profit", inr(acc["Flight Profit"].sum()))

            st.markdown("<br>", unsafe_allow_html=True)
            rp = (acc.groupby("Route")["Flight Profit"].sum()
                  .reset_index().sort_values("Flight Profit"))
            fig5 = go.Figure(go.Bar(
                x=rp["Flight Profit"], y=rp["Route"], orientation="h",
                marker_color=[GREEN if x > 0 else RED for x in rp["Flight Profit"]],
                text=[inr(x) for x in rp["Flight Profit"]],
                textposition="outside", textfont=dict(size=11)))
            fig5.update_xaxes(title_text="Estimated flight profit (₹)")
            style_chart(fig5, height=210, legend=False)
            fig5.update_layout(margin=dict(l=10, r=90, t=10, b=10))
            st.plotly_chart(fig5, use_container_width=True)
            st.caption("Costs are cabin-aware: a Business seat is costed at "
                       f"{CABIN_COST_MULT['Business']:.1f}× the economy seat cost "
                       "because it occupies that much more aircraft floor space.")

            cols = [c for c in ["Timestamp", "Analyst", "Route", "Flight No.",
                                "Departure Date", "Cabin Class", "Load Factor",
                                "Arithmetic Fare", "AI Suggested Fare", "Engine",
                                "Final Fare Used", "Manager Decision",
                                "Profit Per Seat", "Flight Profit"]
                    if c in acc.columns]
            st.dataframe(acc[cols].sort_values("Timestamp", ascending=False),
                         use_container_width=True, hide_index=True)

    st.markdown("""<div style="margin-top:1.6rem;padding:0.6rem 0;
      border-top:1px solid #dde3f0;text-align:center;font-size:0.6rem;
      color:#8095bd;letter-spacing:0.08em;">
      IndiGo Pricing Intelligence · Team 5 · ISB Action Learning Project 2026 · Confidential
    </div>""", unsafe_allow_html=True)


if __name__ == "__main__":
    main()
