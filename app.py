"""
IndiGo Pricing Intelligence Dashboard
Team 5 — ISB Action Learning Project 2026

ALL CONFIG LIVES IN STREAMLIT SECRETS, NEVER IN THIS FILE.
Pasting this file over GitHub can never wipe your keys or sheet name.

Required Secrets (top-level keys must sit ABOVE the [gcp_service_account] block):

    LLM_PROVIDER      = "groq"
    GROQ_API_KEY      = "gsk_..."
    GROQ_MODEL        = "llama-3.3-70b-versatile"
    GEMINI_API_KEY    = "AIzaSy..."
    GEMINI_MODEL      = "gemini-2.5-flash-lite"
    GOOGLE_SHEET_NAME = "Price Intelligence"

    [gcp_service_account]
    ... service account JSON fields ...

Google Sheet tabs used:
    Competitor Prices   (written by the data generator)
    IndiGo Operations   (written by the data generator)
    Feedback            (created automatically on first manager decision)
    AI Price Log        (created automatically on first AI call)
"""

import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd
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

BASE_FARES = {
    "Mumbai to Delhi": 10000, "Bangalore to Delhi": 8000, "Mumbai to Goa": 7500,
    "Mumbai to Dubai": 14000, "Mumbai to London": 20000,
}
COST_PER_SEAT = {
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
CABIN_ADJ = {"Economy": 0.00, "Premium Economy": 0.50, "Business": 0.80}

STRATEGIC_OPTIONS = [
    "None — let AI decide",
    "Grow Traffic — prioritise volume, price competitively",
    "Charge Premium — maximise revenue per seat",
    "Match Competition — stay within 3% of lowest competitor",
    "Holiday Surge — apply festival premium pricing",
    "Fill Last Seats — aggressive discounting to maximise load",
]

# IndiGo brand palette
NAVY, MAGENTA, SKY = "#1B2D6B", "#E91E8C", "#2F6FD0"
GREEN, AMBER, RED  = "#16A34A", "#D97706", "#DC2626"


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
/* Top padding must clear Streamlit's fixed toolbar, or the header is cut off */
.block-container { padding:2.7rem 1.4rem 2rem !important; max-width:100% !important; }

.pid-hdr { background:linear-gradient(100deg,#1B2D6B 0%,#2b4aa8 55%,#E91E8C 130%);
  padding:0.9rem 1.5rem; margin:0 -1.4rem 1.1rem; display:flex; align-items:center;
  justify-content:space-between; gap:1rem; flex-wrap:wrap;
  box-shadow:0 3px 18px rgba(27,45,107,0.22); border-radius:0 0 10px 10px; }
.pid-title { font-size:1.2rem; font-weight:700; color:#fff; line-height:1.2; }
.pid-sub { font-size:0.6rem; color:rgba(255,255,255,0.62); text-transform:uppercase;
  letter-spacing:0.08em; margin-top:0.15rem; }
.pid-ctx { display:flex; gap:1.1rem; align-items:center; flex-wrap:wrap; }
.pid-ctx-val { font-size:0.9rem; font-weight:700; color:#fff;
  font-family:'DM Mono',monospace; line-height:1.1; }
.pid-ctx-lbl { font-size:0.54rem; color:rgba(255,255,255,0.6);
  text-transform:uppercase; letter-spacing:0.07em; }
.pid-div { width:1px; height:26px; background:rgba(255,255,255,0.25); }
.live-pill { background:rgba(255,255,255,0.14); border:1px solid rgba(255,255,255,0.3);
  color:#8affc0; border-radius:20px; padding:0.22rem 0.7rem; font-size:0.6rem;
  font-weight:700; display:flex; align-items:center; gap:0.35rem; letter-spacing:0.06em; }
.live-dot { width:6px; height:6px; background:#2ecc71; border-radius:50%;
  animation:blink 1.8s infinite; }
@keyframes blink { 0%,100%{opacity:1} 50%{opacity:0.25} }

.sec-hd { font-size:0.63rem; font-weight:700; letter-spacing:0.14em;
  text-transform:uppercase; color:#1B2D6B; margin:0.2rem 0 0.6rem 0;
  display:flex; align-items:center; gap:0.5rem; }
.sec-hd::after { content:''; flex:1; height:1px; background:#dde3f0; }

.kpi-strip { display:grid; grid-template-columns:repeat(5,minmax(0,1fr));
  gap:0.8rem; margin-bottom:0.5rem; }
.kpi-card { background:#fff; border:1px solid #dde3f0; border-radius:11px;
  padding:0.85rem 1rem; box-shadow:0 1px 7px rgba(27,45,107,0.06); min-width:0; }
.kpi-card.accent { border-left:3px solid #E91E8C; }
.kpi-val { font-size:1.45rem; font-weight:700; color:#1B2D6B;
  font-family:'DM Mono',monospace; line-height:1.1;
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.kpi-lbl { font-size:0.58rem; color:#6a80ad; text-transform:uppercase;
  letter-spacing:0.09em; margin-top:0.3rem; }
.kpi-sub { font-size:0.66rem; color:#8095bd; margin-top:0.12rem;
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.k-green{color:#16A34A !important;} .k-amber{color:#D97706 !important;}
.k-red{color:#DC2626 !important;} .k-mag{color:#E91E8C !important;}
.k-navy{color:#1B2D6B !important;}

.fare-tbl { width:100%; border-collapse:separate; border-spacing:0; font-size:0.74rem;
  border:1px solid #dde3f0; border-radius:10px; overflow:hidden;
  table-layout:fixed; background:#fff; }
.fare-tbl thead tr { background:#f2f5fc; }
.fare-tbl th { padding:0.5rem 0.55rem; font-size:0.58rem; font-weight:700;
  letter-spacing:0.08em; text-transform:uppercase; color:#1B2D6B;
  border-bottom:2px solid #dde3f0; text-align:left;
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.fare-tbl td { padding:0.44rem 0.55rem; border-bottom:1px solid #f2f5fc; color:#2a4060;
  font-family:'DM Mono',monospace; font-size:0.72rem;
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.fare-tbl tr:last-child td { border-bottom:none; }
.fare-tbl tbody tr:hover td { background:#fafbff; }
.date-sep td { background:#eaf0fb !important; color:#1B2D6B !important;
  font-family:'DM Sans',sans-serif !important; font-weight:700 !important;
  font-size:0.72rem !important; padding:0.34rem 0.6rem !important;
  border-top:2px solid #c9d6f0 !important; }
.f-navy{color:#1B2D6B !important;font-weight:600;}
.f-mag{color:#E91E8C !important;font-weight:600;}
.f-ai{color:#2F6FD0 !important;font-weight:700;}
.f-ailog{color:#0891b2 !important;font-weight:600;}
.f-cheap{color:#16A34A !important;font-weight:600;}
.f-exp{color:#DC2626 !important;} .f-sim{color:#D97706 !important;}
.lf-g{color:#16A34A;font-weight:600;} .lf-a{color:#D97706;font-weight:600;}
.lf-r{color:#DC2626;font-weight:600;}

.ai-result { background:linear-gradient(135deg,#f4f7ff 0%,#fdf2f9 100%);
  border:1.5px solid #E91E8C; border-radius:11px; padding:0.9rem 1.1rem; }
.ai-badge-ok { display:inline-block; background:#dcfce7; border:1px solid #16A34A;
  color:#15803d; font-size:0.68rem; font-weight:700;
  padding:0.18rem 0.65rem; border-radius:20px; }
.ai-badge-ov { display:inline-block; background:#fef3c7; border:1px solid #D97706;
  color:#b45309; font-size:0.68rem; font-weight:700;
  padding:0.18rem 0.65rem; border-radius:20px; }
.ai-price { font-size:2.1rem; font-weight:700; color:#1B2D6B;
  font-family:'DM Mono',monospace; line-height:1.1; margin:0.35rem 0; }
.ai-rat { font-size:0.78rem; color:#3a5080; line-height:1.65; padding:0.55rem 0.8rem;
  background:rgba(255,255,255,0.85); border-left:3px solid #E91E8C;
  border-radius:0 6px 6px 0; margin-top:0.5rem; }
.engine-chip { font-size:0.58rem; font-weight:700; padding:0.14rem 0.55rem;
  border-radius:20px; text-transform:uppercase; letter-spacing:0.06em; }

.arith-box { background:#fafbff; border:1px solid #dde3f0; border-radius:9px;
  padding:0.6rem 0.9rem; font-size:0.7rem; color:#3a5080;
  font-family:'DM Mono',monospace; line-height:1.85; }
.bd-row { display:flex; justify-content:space-between;
  border-bottom:1px dashed #e6ebf7; padding:0.03rem 0; }
.bd-row:last-child { border-bottom:none; font-weight:700; color:#1B2D6B; }
.bd-pos{color:#DC2626;} .bd-neg{color:#16A34A;} .bd-neu{color:#8095bd;}

.flt-pill { background:#f2f5fc; border:1px solid #c9d6f0; border-radius:9px;
  padding:0.5rem 0.8rem; font-size:0.73rem; color:#2a4060;
  line-height:1.7; margin-bottom:0.6rem; }
.flt-pill-t { font-size:0.9rem; font-weight:700; color:#1B2D6B;
  font-family:'DM Mono',monospace; }

section[data-testid="stSidebar"] { background:#fff !important; border-right:1px solid #dde3f0; }
section[data-testid="stSidebar"] .block-container { padding:1rem 0.85rem; }
.sb-brand { font-size:0.92rem; font-weight:700; color:#1B2D6B; padding-bottom:0.8rem;
  border-bottom:2px solid #E91E8C; margin-bottom:0.9rem; }

.stSelectbox label, .stDateInput label, .stRadio>label, .stNumberInput label {
  color:#1B2D6B !important; font-size:0.62rem !important; font-weight:700 !important;
  text-transform:uppercase !important; letter-spacing:0.09em !important; }
.stSelectbox>div>div { background:#fafbff !important; border:1px solid #c9d6f0 !important;
  color:#1a2740 !important; border-radius:8px !important; }
.stRadio>div { flex-direction:row !important; gap:0.5rem !important; flex-wrap:wrap !important; }
.stRadio>div>label { color:#2a4060 !important; font-size:0.75rem !important;
  text-transform:none !important; letter-spacing:0 !important; font-weight:500 !important;
  background:#fafbff; border:1px solid #c9d6f0; border-radius:6px; padding:0.2rem 0.65rem; }
.stButton>button { background:linear-gradient(120deg,#1B2D6B 0%,#E91E8C 140%);
  color:white; border:none; border-radius:8px; font-family:'DM Sans',sans-serif;
  font-size:0.82rem; font-weight:700; padding:0.48rem 1rem; width:100%;
  box-shadow:0 2px 9px rgba(27,45,107,0.22); }
.stButton>button:hover { background:linear-gradient(120deg,#E91E8C 0%,#1B2D6B 140%); }
div[data-testid="metric-container"] { background:#fff !important;
  border:1px solid #dde3f0 !important; border-radius:10px; padding:0.65rem 0.9rem;
  box-shadow:0 1px 6px rgba(27,45,107,0.05); }
div[data-testid="metric-container"] label { color:#6a80ad !important;
  font-size:0.6rem !important; text-transform:uppercase; letter-spacing:0.07em; }
div[data-testid="metric-container"] [data-testid="metric-value"] { color:#1B2D6B !important;
  font-family:'DM Mono',monospace !important; font-size:1.2rem !important;
  font-weight:700 !important; }
.stDateInput>div>div>input { background:#fafbff !important;
  border:1px solid #c9d6f0 !important; color:#1a2740 !important; border-radius:8px !important; }
.legend-row { font-size:0.6rem; color:#8095bd; margin-top:0.45rem; }
</style>
""", unsafe_allow_html=True)


# ═════════════════════════════════════════════════════════════
# GOOGLE SHEETS
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


def load_tab(tab, date_col=None):
    try:
        rows = get_sheet().worksheet(tab).get_all_records()
        df = pd.DataFrame(rows) if rows else pd.DataFrame()
        if date_col and not df.empty and date_col in df.columns:
            df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
        return df
    except Exception:
        return pd.DataFrame()


FEEDBACK_HDRS = ["Timestamp", "Route", "Flight No.", "Departure Time", "Departure Date",
                 "Cabin Class", "Passenger Type", "Trip Type", "Days to Departure",
                 "Load Factor", "Arithmetic Fare", "AI Decision", "AI Suggested Fare",
                 "AI Rationale", "Engine", "Manager Decision", "Final Fare Used",
                 "Strategic Direction", "Manager Notes"]

AILOG_HDRS = ["Log Date", "Route", "Flight No.", "Departure Time", "Departure Date",
              "Cabin Class", "Days to Departure", "Load Factor", "Arithmetic Fare",
              "AI Decision", "AI Suggested Fare", "Engine",
              "Manager Decision", "Final Fare Used"]


def _append(tab, hdrs, row):
    sh = get_sheet()
    try:
        ws = sh.worksheet(tab)
    except Exception:
        ws = sh.add_worksheet(tab, rows=5000, cols=len(hdrs) + 2)
        ws.append_row(hdrs)
    if not ws.row_values(1):
        ws.append_row(hdrs)
    ws.append_row([row.get(h, "") for h in hdrs])


def save_feedback(row): _append(FEEDBACK_TAB, FEEDBACK_HDRS, row)
def save_ai_log(row):   _append(AI_LOG_TAB, AILOG_HDRS, row)


# ═════════════════════════════════════════════════════════════
# HELPERS
# ═════════════════════════════════════════════════════════════
def inr(v):
    try:
        return f"₹{int(round(float(v))):,}"
    except Exception:
        return "—"


def lf_cls(lf):
    if lf <= 0.70: return "lf-g", "●"
    if lf <= 0.85: return "lf-a", "●"
    return "lf-r", "●"


def lf_kpi(lf):
    if lf <= 0.70: return "k-green"
    if lf <= 0.85: return "k-amber"
    return "k-red"


def deph(t):
    try:
        return int(str(t).split(":")[0])
    except Exception:
        return 10


def comp_cls(v, base):
    try:
        v = int(v)
        if v < base * 0.97: return "f-cheap"
        if v > base * 1.03: return "f-exp"
        return "f-sim"
    except Exception:
        return ""


def style_chart(fig, height=260, legend=True):
    """Single place for chart styling. Deliberately avoids yaxis2/overlaying,
    barmode, titlefont and top-level font_color — all break on Plotly 6."""
    fig.update_layout(
        plot_bgcolor="#ffffff", paper_bgcolor="#ffffff",
        font=dict(color="#2a4060", family="DM Sans", size=11),
        margin=dict(l=10, r=10, t=10, b=10),
        height=height, showlegend=legend,
        legend=dict(bgcolor="rgba(255,255,255,0.9)", bordercolor="#dde3f0",
                    borderwidth=1, font=dict(size=9), orientation="h", y=1.14, x=0),
        hovermode="x unified",
    )
    fig.update_xaxes(gridcolor="#f0f3fa", linecolor="#dde3f0", zeroline=False)
    fig.update_yaxes(gridcolor="#f0f3fa", linecolor="#dde3f0", zeroline=False)
    return fig


# ═════════════════════════════════════════════════════════════
# PRICING ENGINE  (mirrors the team's Excel MVP)
# ═════════════════════════════════════════════════════════════
def calc_fare(route, cabin, days_to_dep, load_factor, best_comp_fare,
              is_holiday, dep_hour, passenger_type="Adult", trip_type="One Way"):
    base = BASE_FARES.get(route, 5000)

    if   days_to_dep <= 3:  adv, adv_l = 0.20, "Last minute"
    elif days_to_dep <= 7:  adv, adv_l = 0.15, "Near date"
    elif days_to_dep <= 14: adv, adv_l = 0.10, "Short advance"
    elif days_to_dep <= 30: adv, adv_l = 0.00, "Normal window"
    elif days_to_dep <= 60: adv, adv_l = -0.05, "Early booking"
    else:                   adv, adv_l = -0.10, "Very early"

    if   load_factor <= 0.40: lfa, lf_l = -0.10, "Low demand"
    elif load_factor <= 0.70: lfa, lf_l =  0.00, "Normal demand"
    elif load_factor <= 0.85: lfa, lf_l =  0.15, "High demand"
    else:                     lfa, lf_l =  0.30, "Very high demand"

    cab  = CABIN_ADJ.get(cabin, 0.0)
    pax  = PASSENGER_ADJ.get(passenger_type, 0.0)
    trip = -0.05 if trip_type == "Round Trip" else 0.0

    comp, comp_l = 0.0, "Within range"
    if best_comp_fare and best_comp_fare > 0:
        ratio = (base * (1 + cab)) / best_comp_fare
        if   ratio > 1.10: comp, comp_l = -0.05, "We're pricier"
        elif ratio < 0.90: comp, comp_l =  0.05, "We're cheaper"

    h = int(dep_hour)
    if   0 <= h <= 5:   tim, tim_l = -0.05, "Red-eye"
    elif 6 <= h <= 8:   tim, tim_l =  0.12, "Morning peak"
    elif 9 <= h <= 11:  tim, tim_l =  0.18, "Business peak"
    elif 12 <= h <= 15: tim, tim_l =  0.00, "Afternoon"
    elif 16 <= h <= 20: tim, tim_l =  0.15, "Evening peak"
    else:               tim, tim_l = -0.03, "Late night"

    hol   = 0.15 if is_holiday else 0.0
    hol_l = "Festival day" if is_holiday else "No holiday"

    total = max(-0.30, min(1.00, adv + lfa + cab + pax + trip + comp + tim + hol))
    final = int(base * (1 + total))

    breakdown = [
        ("Base Fare",        None,  inr(base)),
        ("Advance Booking",  adv,   adv_l),
        ("Load Factor",      lfa,   lf_l),
        ("Cabin Class",      cab,   cabin),
        ("Passenger Type",   pax,   passenger_type),
        ("Trip Type",        trip,  trip_type),
        ("Competition",      comp,  comp_l),
        ("Time Slot",        tim,   tim_l),
        ("Holiday",          hol,   hol_l),
        ("Total Adjustment", total, f"{total*100:.1f}%"),
        ("Final Fare",       None,  inr(final)),
    ]
    return final, breakdown


# ═════════════════════════════════════════════════════════════
# LLM LAYER  (Groq / Gemini / rules-based fallback)
# ═════════════════════════════════════════════════════════════
def build_prompt(route, flight_no, dep_time, cabin, dep_date, days_to_dep,
                 load_factor, arithmetic_fare, comp_fares, strategy,
                 history, pax_type, trip_type):
    comp_text = "\n".join(
        f"  {a} ({fn} {ft}): Rs {fare:,}" for a, fn, ft, fare in comp_fares
    ) or "  No competitor data available"

    strat_text = ""
    if strategy and "None" not in strategy:
        strat_text = (f"\nSTRATEGIC DIRECTION FROM PRICING MANAGER: {strategy}\n"
                      "This must strongly influence your recommendation.\n")

    hist_text = ""
    if history:
        hist_text = "\nRecent manager decisions on this route:\n"
        for h in history[-3:]:
            hist_text += (f"  - {h.get('Departure Date','')}: AI suggested "
                          f"Rs {h.get('AI Suggested Fare','')}, manager "
                          f"{h.get('Manager Decision','')}, final "
                          f"Rs {h.get('Final Fare Used','')}\n")

    return f"""You are a senior pricing analyst at IndiGo Airlines.

Flight: {flight_no} | Route: {route} | Departs {dep_time} on {dep_date}
Cabin: {cabin} | Passenger: {pax_type} | Trip: {trip_type}
Days to departure: {days_to_dep} | Load factor: {round(load_factor*100,1)}%

Our arithmetic pricing engine calculated: Rs {arithmetic_fare:,}

Competing flights on the same route and date:
{comp_text}
{strat_text}{hist_text}
Rules you must follow:
- If load factor is above 85%, do not recommend any discount.
- Compare like with like: morning against morning, evening against evening.
- Give one precise fare, never a range.

Reply in exactly this format and nothing else:
Decision: Approve OR Override
Suggested Fare: Rs [number]
Rationale: [2-3 plain English sentences]"""


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


def fallback_rationale(breakdown, fare, base_fare, best_comp, load_factor):
    """Plain-English explanation built purely from the arithmetic rules.
    Used only when no AI engine is reachable, and always labelled as such."""
    ups   = [(l, v) for k, v, l in breakdown
             if isinstance(v, (int, float)) and v > 0
             and k not in ("Total Adjustment", "Base Fare", "Final Fare")]
    downs = [(l, v) for k, v, l in breakdown
             if isinstance(v, (int, float)) and v < 0
             and k not in ("Total Adjustment", "Base Fare", "Final Fare")]
    ups.sort(key=lambda x: -x[1])
    downs.sort(key=lambda x: x[1])

    pct = (fare / base_fare - 1) * 100 if base_fare else 0
    parts = [f"Rules-based pricing: {inr(fare)}, {abs(pct):.0f}% "
             f"{'above' if pct >= 0 else 'below'} the {inr(base_fare)} base fare."]
    if ups:
        parts.append("Upward pressure from " +
                     ", ".join(f"{l} (+{v*100:.0f}%)" for l, v in ups[:3]) + ".")
    if downs:
        parts.append("Offset by " +
                     ", ".join(f"{l} ({v*100:.0f}%)" for l, v in downs[:2]) + ".")
    if best_comp:
        gap = fare - best_comp
        parts.append(f"Cheapest competitor is {inr(best_comp)}, putting us "
                     f"{inr(abs(gap))} {'above' if gap > 0 else 'below'} them.")
    if load_factor > 0.85:
        parts.append("Load factor above 85%, so no discount was applied.")
    return " ".join(parts)


def call_llm(route, flight_no, dep_time, cabin, dep_date, days_to_dep, load_factor,
             arithmetic_fare, comp_fares, strategy, history, pax_type, trip_type,
             breakdown, base_fare, best_comp):
    """Tries the configured provider, then the other, then the rules fallback.
    Returns (decision, fare, rationale, engine_name, diagnostics)."""
    prompt = build_prompt(route, flight_no, dep_time, cabin, dep_date, days_to_dep,
                          load_factor, arithmetic_fare, comp_fares, strategy,
                          history, pax_type, trip_type)

    order = ["groq", "gemini"] if LLM_PROVIDER == "groq" else ["gemini", "groq"]
    names = {"groq": "Groq", "gemini": "Gemini"}
    errors = []

    for prov in order:
        fn = call_groq if prov == "groq" else call_gemini
        d, f, rat, err = fn(prompt, arithmetic_fare)
        if err is None:
            return d, f, rat, names[prov], ("; ".join(errors) if errors else None)
        errors.append(f"{names[prov]} — {err}")

    rat = fallback_rationale(breakdown, arithmetic_fare, base_fare,
                             best_comp, load_factor)
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

    feedback_df = load_tab(FEEDBACK_TAB)
    ai_log_df   = load_tab(AI_LOG_TAB, "Log Date")

    today = pd.Timestamp.today().normalize()
    dmin, dmax = today - timedelta(days=30), today + timedelta(days=90)

    # ── SIDEBAR ──────────────────────────────────────────────
    with st.sidebar:
        st.markdown('<div class="sb-brand">'
                    '<span style="color:#E91E8C;font-weight:800">6E</span>'
                    '&nbsp; IndiGo · Pricing Intelligence</div>', unsafe_allow_html=True)

        routes    = sorted(indigo_df["Route"].dropna().unique().tolist())
        sel_route = st.selectbox("Route", routes)
        cabins    = sorted(indigo_df["Cabin Class"].dropna().unique().tolist())
        sel_cabin = st.selectbox("Cabin Class", cabins)
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

    # ── FILTER ───────────────────────────────────────────────
    dcol = "Date" if "Date" in indigo_df.columns else "Scrape Date"

    indigo_f = indigo_df[(indigo_df["Route"] == sel_route) &
                         (indigo_df["Cabin Class"] == sel_cabin) &
                         (indigo_df["Departure Date"].isin(sel_dates))].copy()
    comp_f = comp_df[(comp_df["Route"] == sel_route) &
                     (comp_df["Cabin Class"] == sel_cabin) &
                     (comp_df["Departure Date"].isin(sel_dates))].copy()

    if sel_time != "All Times" and "Departure Time" in indigo_f.columns:
        indigo_f = indigo_f[indigo_f["Departure Time"].astype(str) == sel_time]

    if not comp_f.empty and "Scrape Date" in comp_f.columns:
        comp_f = (comp_f.sort_values("Scrape Date")
                  .groupby(["Airline", "Flight No.", "Departure Date"],
                           as_index=False).last())
    if not indigo_f.empty and dcol in indigo_f.columns:
        indigo_f = (indigo_f.sort_values(dcol)
                    .groupby(["Flight No.", "Departure Date"], as_index=False).last())

    # ── HEADER ───────────────────────────────────────────────
    avg_lf = float(indigo_f["Load Factor"].mean()) if not indigo_f.empty else 0.0
    date_disp = " & ".join(d.strftime("%d %b %Y") for d in sel_dates)

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
          <div class="pid-ctx-val">{sel_route.replace(' to ',' → ')}</div>
          <div class="pid-ctx-lbl">{sel_cabin} · {pax_type} · {trip_type}</div>
        </div>
        <div class="pid-div"></div>
        <div>
          <div class="pid-ctx-val">{date_disp}</div>
          <div class="pid-ctx-lbl">Departure</div>
        </div>
        <div class="pid-div"></div>
        <div>
          <div class="pid-ctx-val">{round(avg_lf*100,1)}%</div>
          <div class="pid-ctx-lbl">Avg load factor</div>
        </div>
        <div class="pid-div"></div>
        <div>
          <div class="pid-ctx-val">{len(indigo_f)}</div>
          <div class="pid-ctx-lbl">Flights shown</div>
        </div>
        <div class="live-pill"><div class="live-dot"></div>LIVE</div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    if indigo_f.empty:
        st.info("No IndiGo flights found for this route, cabin and date. "
                "Pick a departure date covered by your data, or widen the filters.")
        return

    # ── FLIGHT SELECTOR ──────────────────────────────────────
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
    f_sold  = int(frow["Seats Sold"].iloc[0]) if not frow.empty else 0
    f_total = int(frow["Total Seats"].iloc[0]) if not frow.empty else 180
    f_hol   = str(frow["Holiday / Festival"].iloc[0]) if not frow.empty else "No"
    f_slot  = str(frow["Time Slot"].iloc[0]) if not frow.empty else ""

    comp_same = comp_f[comp_f["Departure Date"] == f_date]
    comp_list = [(str(c["Airline"]), str(c["Flight No."]),
                  str(c["Departure Time"]), int(c["Fare (INR)"]))
                 for _, c in comp_same.iterrows() if pd.notna(c.get("Fare (INR)"))]
    best_comp = min([c[3] for c in comp_list], default=0)

    base_fare = BASE_FARES.get(sel_route, 5000)
    arith, breakdown = calc_fare(sel_route, sel_cabin, f_days, f_lf, best_comp,
                                 f_hol == "Yes", deph(f_time), pax_type, trip_type)

    # Latest AI log entry for this flight
    ai_today, mgr_today = "—", "Pending"
    if not ai_log_df.empty and "Flight No." in ai_log_df.columns:
        tl = ai_log_df[(ai_log_df["Flight No."].astype(str) == f_no) &
                       (ai_log_df["Departure Date"].astype(str).str[:10]
                        == str(f_date)[:10])]
        if not tl.empty:
            ai_today  = inr(tl.iloc[-1].get("AI Suggested Fare", ""))
            mgr_today = str(tl.iloc[-1].get("Manager Decision", "Pending") or "Pending")

    # Manager override, today only
    ov_today = "—"
    if not feedback_df.empty and "Timestamp" in feedback_df.columns:
        fb = feedback_df.copy()
        fb["_ts"] = pd.to_datetime(fb["Timestamp"], errors="coerce")
        m = fb[(fb.get("Flight No.", pd.Series(dtype=str)).astype(str) == f_no) &
               (fb["_ts"] >= today) &
               (fb.get("Manager Decision", pd.Series(dtype=str)) == "Overridden")]
        if not m.empty:
            ov_today = inr(m.iloc[-1].get("Final Fare Used", ""))

    cost_seat     = COST_PER_SEAT.get(sel_route, 3000)
    profit_seat   = arith - cost_seat
    flight_profit = profit_seat * f_sold

    # ══════════ 1 · KPI STRIP ══════════
    st.markdown('<div class="sec-hd">Flight snapshot</div>', unsafe_allow_html=True)
    st.markdown(f"""
    <div class="kpi-strip">
      <div class="kpi-card">
        <div class="kpi-val {lf_kpi(f_lf)}">{round(f_lf*100,1)}%</div>
        <div class="kpi-lbl">Load factor</div>
        <div class="kpi-sub">{f_sold}/{f_total} seats booked</div>
      </div>
      <div class="kpi-card accent">
        <div class="kpi-val k-mag">{inr(arith)}</div>
        <div class="kpi-lbl">Arithmetic fare</div>
        <div class="kpi-sub">Base {inr(base_fare)}</div>
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
        <div class="kpi-sub">{inr(profit_seat)} per seat</div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    # ══════════ 2 · AI PANEL ══════════
    st.markdown('<div class="sec-hd">AI pricing recommendation</div>',
                unsafe_allow_html=True)

    lfc, lfd = lf_cls(f_lf)
    st.markdown(f"""
    <div class="flt-pill">
      <span class="flt-pill-t">{f_no} · {f_time} · {f_slot}</span>
      &nbsp;&nbsp;Load <span class="{lfc}">{lfd} {round(f_lf*100,1)}%</span>
      &nbsp;·&nbsp; {f_sold}/{f_total} seats
      &nbsp;·&nbsp; {f_days} days to departure
      &nbsp;·&nbsp; cheapest competitor {inr(best_comp) if best_comp else 'n/a'}
    </div>""", unsafe_allow_html=True)

    a_left, a_right = st.columns([1, 1.25], gap="large")

    with a_left:
        st.markdown('<div style="font-size:0.6rem;font-weight:700;color:#1B2D6B;'
                    'text-transform:uppercase;letter-spacing:0.09em;'
                    'margin-bottom:0.35rem;">How the arithmetic fare is built</div>',
                    unsafe_allow_html=True)
        bd = "<div class='arith-box'>"
        for k, v, lbl in breakdown:
            if k == "Base Fare":
                bd += f'<div class="bd-row"><span>Base fare</span><span>{lbl}</span></div>'
            elif k == "Final Fare":
                bd += ('<div class="bd-row"><span>Arithmetic fare</span>'
                       f'<span style="color:#E91E8C">{lbl}</span></div>')
            elif k == "Total Adjustment":
                bd += f'<div class="bd-row"><span>Total adjustment</span><span>{lbl}</span></div>'
            else:
                p = float(v)
                cls = "bd-pos" if p > 0 else ("bd-neg" if p < 0 else "bd-neu")
                sign = "+" if p > 0 else ""
                bd += (f'<div class="bd-row"><span class="bd-neu">{lbl}</span>'
                       f'<span class="{cls}">{sign}{p*100:.0f}%</span></div>')
        bd += "</div>"
        st.markdown(bd, unsafe_allow_html=True)

    with a_right:
        strategy = st.selectbox("Strategic direction (sent to the AI)", STRATEGIC_OPTIONS)

        if st.button("🤖  Get AI recommendation"):
            hist = []
            if not feedback_df.empty and "Route" in feedback_df.columns:
                hist = feedback_df[feedback_df["Route"] == sel_route].to_dict("records")
            with st.spinner("Asking the pricing analyst..."):
                dec, fare, rat, engine, note = call_llm(
                    sel_route, f_no, f_time, sel_cabin, str(f_date)[:10],
                    f_days, f_lf, arith, comp_list, strategy, hist,
                    pax_type, trip_type, breakdown, base_fare, best_comp)
            try:
                save_ai_log({"Log Date": datetime.now().strftime("%Y-%m-%d"),
                             "Route": sel_route, "Flight No.": f_no,
                             "Departure Time": f_time,
                             "Departure Date": str(f_date)[:10],
                             "Cabin Class": sel_cabin, "Days to Departure": f_days,
                             "Load Factor": round(f_lf * 100, 1),
                             "Arithmetic Fare": arith, "AI Decision": dec,
                             "AI Suggested Fare": fare, "Engine": engine,
                             "Manager Decision": "Pending", "Final Fare Used": ""})
            except Exception as e:
                st.warning(f"Recommendation received but could not be logged: {e}")
            st.session_state["ai"] = {
                "dec": dec, "fare": fare, "rat": rat, "arith": arith,
                "flt": f_no, "time": f_time, "date": str(f_date)[:10],
                "days": f_days, "lf": f_lf, "strategy": strategy,
                "engine": engine, "note": note}

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

            st.markdown("**Manager decision** — applies to today only")
            strat2 = st.selectbox(
                "Revise strategic direction (optional)", STRATEGIC_OPTIONS,
                index=STRATEGIC_OPTIONS.index(r["strategy"])
                      if r["strategy"] in STRATEGIC_OPTIONS else 0, key="strat2")

            def commit(kind, final_fare):
                save_feedback({
                    "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "Route": sel_route, "Flight No.": r["flt"],
                    "Departure Time": r["time"], "Departure Date": r["date"],
                    "Cabin Class": sel_cabin, "Passenger Type": pax_type,
                    "Trip Type": trip_type, "Days to Departure": r["days"],
                    "Load Factor": round(r["lf"] * 100, 1),
                    "Arithmetic Fare": r["arith"], "AI Decision": r["dec"],
                    "AI Suggested Fare": r["fare"], "AI Rationale": r["rat"],
                    "Engine": r.get("engine", ""), "Manager Decision": kind,
                    "Final Fare Used": final_fare,
                    "Strategic Direction": strat2, "Manager Notes": ""})
                save_ai_log({
                    "Log Date": datetime.now().strftime("%Y-%m-%d"),
                    "Route": sel_route, "Flight No.": r["flt"],
                    "Departure Time": r["time"], "Departure Date": r["date"],
                    "Cabin Class": sel_cabin, "Days to Departure": r["days"],
                    "Load Factor": round(r["lf"] * 100, 1),
                    "Arithmetic Fare": r["arith"], "AI Decision": r["dec"],
                    "AI Suggested Fare": r["fare"], "Engine": r.get("engine", ""),
                    "Manager Decision": kind, "Final Fare Used": final_fare})

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
                ov = st.number_input("Your fare (₹)", min_value=500, max_value=300000,
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
    st.markdown('<div class="sec-hd">Fare comparison — all airlines</div>',
                unsafe_allow_html=True)

    acc_lookup = {}
    if not feedback_df.empty and "Manager Decision" in feedback_df.columns:
        for _, x in feedback_df[feedback_df["Manager Decision"]
                                .isin(["Accepted", "Overridden"])].iterrows():
            k = (str(x.get("Flight No.", "")), str(x.get("Departure Date", ""))[:10])
            try:
                acc_lookup[k] = int(x.get("Final Fare Used", 0))
            except Exception:
                pass

    log_lookup = {}
    if not ai_log_df.empty and "Flight No." in ai_log_df.columns:
        for _, x in ai_log_df.iterrows():
            k = (str(x.get("Flight No.", "")), str(x.get("Departure Date", ""))[:10])
            try:
                log_lookup[k] = int(x.get("AI Suggested Fare", 0))
            except Exception:
                pass

    ai_c = comp_f[comp_f["Airline"] == "Air India"] if not comp_f.empty else pd.DataFrame()
    qp_c = comp_f[comp_f["Airline"] == "Akasa Air"] if not comp_f.empty else pd.DataFrame()

    html = ("""<table class="fare-tbl"><colgroup>
    <col style="width:13%"><col style="width:11%"><col style="width:8%">
    <col style="width:9%"><col style="width:9%"><col style="width:10%">
    <col style="width:10%"><col style="width:10%"><col style="width:10%">
    <col style="width:10%"></colgroup>
    <thead><tr>
      <th>IndiGo flight</th><th>Slot</th><th>Load</th><th>Seats</th>
      <th>Base</th><th>Arithmetic</th><th>AI rec</th>
      <th>Air India</th><th>AI fare</th><th>Akasa fare</th>
    </tr></thead><tbody>""")

    cur_date = None
    for _, row in indigo_f.sort_values(["Departure Date", "Departure Time"]).iterrows():
        dd   = row["Departure Date"]
        ds   = str(dd)[:10]
        fno  = str(row.get("Flight No.", ""))
        ftm  = str(row.get("Departure Time", ""))
        slot = str(row.get("Time Slot", ""))
        dout = row.get("Days to Departure", 30)
        lf   = float(row.get("Load Factor", 0) or 0)
        sold = int(row.get("Seats Sold", 0) or 0)
        tot  = int(row.get("Total Seats", 180) or 180)
        hol  = str(row.get("Holiday / Festival", "No"))
        c, dot = lf_cls(lf)

        if cur_date is None or dd != cur_date:
            cur_date = dd
            lbl = dd.strftime("%A, %d %B %Y") if hasattr(dd, "strftime") else ds
            html += (f'<tr class="date-sep"><td colspan="10">✈ {lbl}'
                     f' &nbsp;—&nbsp; {int(dout)} days to departure</td></tr>')

        sd = comp_f[comp_f["Departure Date"] == dd] if not comp_f.empty else pd.DataFrame()
        bc = int(sd["Fare (INR)"].min()) if not sd.empty and sd["Fare (INR)"].notna().any() else 0
        ar, _ = calc_fare(sel_route, sel_cabin, int(dout), lf, bc,
                          hol == "Yes", deph(ftm), pax_type, trip_type)

        rec = acc_lookup.get((fno, ds)) or log_lookup.get((fno, ds))
        rec_cls = "f-ai" if acc_lookup.get((fno, ds)) else "f-ailog"

        aif, aifl = "—", "—"
        if not ai_c.empty:
            m = ai_c[ai_c["Departure Date"] == dd]
            if not m.empty:
                aif  = int(m.iloc[0]["Fare (INR)"])
                aifl = f"{m.iloc[0]['Flight No.']} {m.iloc[0]['Departure Time']}"
        qpf = "—"
        if not qp_c.empty:
            m = qp_c[qp_c["Departure Date"] == dd]
            if not m.empty:
                qpf = int(m.iloc[0]["Fare (INR)"])

        html += f"""<tr>
          <td class="f-navy">{fno} {ftm}</td>
          <td style="color:#8095bd;font-size:0.67rem">{slot}</td>
          <td><span class="{c}">{dot} {round(lf*100,1)}%</span></td>
          <td style="color:#8095bd">{sold}/{tot}</td>
          <td class="f-navy">{inr(base_fare)}</td>
          <td class="f-mag">{inr(ar)}</td>
          <td class="{rec_cls}">{inr(rec) if rec else '—'}</td>
          <td style="color:#8095bd;font-size:0.67rem">{aifl}</td>
          <td><span class="{comp_cls(aif, base_fare)}">{inr(aif)}</span></td>
          <td><span class="{comp_cls(qpf, base_fare)}">{inr(qpf)}</span></td>
        </tr>"""

    html += "</tbody></table>"
    st.markdown(html, unsafe_allow_html=True)
    st.markdown(f"""<div class="legend-row">
      <span style="color:{NAVY}">■</span> IndiGo base &nbsp;
      <span style="color:{MAGENTA}">■</span> Arithmetic fare (your filters) &nbsp;
      <span style="color:{SKY}">■</span> Accepted by manager &nbsp;
      <span style="color:#0891b2">■</span> Suggested, not yet accepted &nbsp;
      <span style="color:{GREEN}">■</span> Competitor cheaper &nbsp;
      <span style="color:{RED}">■</span> Competitor pricier
    </div>""", unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ══════════ 4 · CHARTS ══════════
    c_left, c_right = st.columns([1, 1.15], gap="large")

    # 4a — Booking build-up
    with c_left:
        st.markdown('<div class="sec-hd">Booking build-up over time</div>',
                    unsafe_allow_html=True)

        flights = []
        if not indigo_df.empty:
            sub = indigo_df[indigo_df["Route"] == sel_route]
            if not sub.empty:
                flights = sorted(
                    sub.apply(lambda r: f"{r['Flight No.']} {r['Departure Time']}",
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

                fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                                    vertical_spacing=0.08, row_heights=[0.42, 0.58])
                fig.add_trace(go.Bar(
                    x=hist[dcol], y=hist["New bookings"],
                    name="New bookings that day",
                    marker_color="rgba(233,30,140,0.55)",
                    hovertemplate="%{y:.0f} seats<extra></extra>"), row=1, col=1)
                fig.add_trace(go.Scatter(
                    x=hist[dcol], y=hist["LF%"], name="Cumulative load factor",
                    mode="lines+markers", line=dict(color=NAVY, width=2.5),
                    marker=dict(size=5, color=NAVY), fill="tozeroy",
                    fillcolor="rgba(27,45,107,0.07)",
                    hovertemplate="%{y:.1f}%<extra></extra>"), row=2, col=1)
                fig.add_hline(y=85, line_dash="dot", line_color=RED,
                              line_width=1, row=2, col=1)
                fig.update_yaxes(title_text="Seats", row=1, col=1)
                fig.update_yaxes(title_text="Load %", range=[0, 105], row=2, col=1)
                fig.update_xaxes(title_text="Booking date", row=2, col=1)
                style_chart(fig, height=330)
                st.plotly_chart(fig, use_container_width=True)
                st.caption(f"{pick} · tracked from "
                           f"{hist[dcol].min().strftime('%d %b %Y')} "
                           f"({len(hist)} days of data) toward "
                           f"{pd.Timestamp(sel_dates[0]).strftime('%d %b %Y')}")

    # 4b — Price trend, four lines
    with c_right:
        st.markdown('<div class="sec-hd">Price trend — competitors vs IndiGo</div>',
                    unsafe_allow_html=True)
        frames = []

        if not comp_df.empty and "Scrape Date" in comp_df.columns:
            ct = comp_df[(comp_df["Route"] == sel_route) &
                         (comp_df["Cabin Class"] == sel_cabin) &
                         (comp_df["Departure Date"].isin(sel_dates)) &
                         (comp_df["Scrape Date"] >= dmin)]
            if not ct.empty:
                g = ct.groupby(["Scrape Date", "Airline"])["Fare (INR)"].mean().reset_index()
                g.columns = ["Date", "Series", "Fare"]
                frames.append(g)

        ih = indigo_df[(indigo_df["Route"] == sel_route) &
                       (indigo_df["Cabin Class"] == sel_cabin) &
                       (indigo_df["Departure Date"].isin(sel_dates))].copy()
        if not ih.empty and dcol in ih.columns:
            ih = ih.dropna(subset=[dcol])
            ih = ih[ih[dcol] >= dmin]
            rows = []
            for d, grp in ih.groupby(dcol):
                cd = pd.DataFrame()
                if not comp_df.empty and "Scrape Date" in comp_df.columns:
                    cd = comp_df[(comp_df["Route"] == sel_route) &
                                 (comp_df["Cabin Class"] == sel_cabin) &
                                 (comp_df["Scrape Date"] == d) &
                                 (comp_df["Departure Date"].isin(sel_dates))]
                bc = int(cd["Fare (INR)"].min()) if not cd.empty and cd["Fare (INR)"].notna().any() else 0
                vals = []
                for _, g2 in grp.iterrows():
                    v, _ = calc_fare(sel_route, sel_cabin,
                                     int(g2.get("Days to Departure", 30) or 30),
                                     float(g2.get("Load Factor", 0.6) or 0.6), bc,
                                     str(g2.get("Holiday / Festival", "No")) == "Yes",
                                     deph(g2.get("Departure Time", "10:00")),
                                     pax_type, trip_type)
                    vals.append(v)
                if vals:
                    rows.append({"Date": d, "Series": "IndiGo arithmetic",
                                 "Fare": sum(vals) / len(vals)})
            if rows:
                frames.append(pd.DataFrame(rows))

        if not ai_log_df.empty and "Log Date" in ai_log_df.columns:
            al = ai_log_df.copy()
            if "Route" in al.columns:
                al = al[al["Route"] == sel_route]
            if not al.empty and "AI Suggested Fare" in al.columns:
                al["AI Suggested Fare"] = pd.to_numeric(al["AI Suggested Fare"],
                                                        errors="coerce")
                al = al.dropna(subset=["Log Date", "AI Suggested Fare"])
                if not al.empty:
                    g = al.groupby("Log Date")["AI Suggested Fare"].mean().reset_index()
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
            style_chart(fig2, height=330)
            st.plotly_chart(fig2, use_container_width=True)
            st.caption("Solid lines are competitor fares scraped daily. "
                       "Dashed lines are IndiGo's own arithmetic and AI fares.")
        else:
            st.info("No trend data yet for this route, cabin and departure date.")

    st.markdown("<br>", unsafe_allow_html=True)

    # ══════════ 5 · PROFITABILITY ══════════
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
            acc["Final Fare Used"] = pd.to_numeric(acc["Final Fare Used"], errors="coerce")
            acc["Cost Per Seat"]   = acc["Route"].map(COST_PER_SEAT).fillna(3000)
            acc["Profit Per Seat"] = acc["Final Fare Used"] - acc["Cost Per Seat"]
            acc["LF"]              = pd.to_numeric(acc["Load Factor"], errors="coerce") / 100
            acc["Seats"]           = acc["Route"].map(TOTAL_SEATS_MAP).fillna(180)
            acc["Flight Profit"]   = acc["Profit Per Seat"] * acc["Seats"] * acc["LF"]
            acc["Base"]            = acc["Route"].map(BASE_FARES).fillna(5000)
            acc["Revenue Uplift"]  = (acc["Final Fare Used"] - acc["Base"]) * acc["Seats"] * acc["LF"]

            p1, p2, p3, p4 = st.columns(4)
            p1.metric("Decisions recorded", len(acc))
            p2.metric("Revenue uplift vs base", inr(acc["Revenue Uplift"].sum()))
            p3.metric("Avg profit per seat", inr(acc["Profit Per Seat"].mean()))
            p4.metric("Est. total flight profit", inr(acc["Flight Profit"].sum()))

            st.markdown("<br>", unsafe_allow_html=True)
            rp = (acc.groupby("Route")["Flight Profit"].sum()
                  .reset_index().sort_values("Flight Profit"))
            fig3 = go.Figure(go.Bar(
                x=rp["Flight Profit"], y=rp["Route"], orientation="h",
                marker_color=[GREEN if x > 0 else RED for x in rp["Flight Profit"]],
                text=[inr(x) for x in rp["Flight Profit"]],
                textposition="outside", textfont=dict(size=11)))
            fig3.update_xaxes(title_text="Estimated flight profit (₹)")
            style_chart(fig3, height=210, legend=False)
            fig3.update_layout(margin=dict(l=10, r=90, t=10, b=10))
            st.plotly_chart(fig3, use_container_width=True)

            cols = [c for c in ["Route", "Flight No.", "Departure Date", "Cabin Class",
                                "Passenger Type", "Trip Type", "Load Factor",
                                "Arithmetic Fare", "AI Suggested Fare", "Engine",
                                "Final Fare Used", "Manager Decision",
                                "Profit Per Seat", "Flight Profit"]
                    if c in acc.columns]
            st.dataframe(acc[cols].sort_values("Departure Date", ascending=False),
                         use_container_width=True, hide_index=True)

    st.markdown("""<div style="margin-top:1.6rem;padding:0.6rem 0;
      border-top:1px solid #dde3f0;text-align:center;font-size:0.6rem;
      color:#8095bd;letter-spacing:0.08em;">
      IndiGo Pricing Intelligence · Team 5 · ISB Action Learning Project 2026 · Confidential
    </div>""", unsafe_allow_html=True)


if __name__ == "__main__":
    main()
