"""
IndiGo Pricing Intelligence Dashboard v2
Team 5 — ISB Action Learning Project 2026
Aviation-themed redesign with full feature set
"""

import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
from datetime import datetime, timedelta

# ============================================================
GOOGLE_SHEET_NAME = "Price Intelligence"
GEMINI_API_KEY    = "PASTE_YOUR_GEMINI_API_KEY_HERE"
# ============================================================

COMPETITOR_TAB = "Competitor Prices"
INDIGO_OPS_TAB = "IndiGo Operations"
FEEDBACK_TAB   = "Feedback"

COST_PER_SEAT = {
    "Mumbai to Delhi":    2800,
    "Bangalore to Delhi": 3200,
    "Mumbai to Goa":      1200,
    "Mumbai to Dubai":    4500,
    "Mumbai to London":   14000,
}
BASE_FARES = {
    "Mumbai to Delhi":    10000,
    "Bangalore to Delhi": 8000,
    "Mumbai to Goa":      7500,
    "Mumbai to Dubai":    14000,
    "Mumbai to London":   20000,
}
TOTAL_SEATS = {
    "Mumbai to Delhi":    180,
    "Bangalore to Delhi": 180,
    "Mumbai to Goa":      180,
    "Mumbai to Dubai":    220,
    "Mumbai to London":   280,
}
PASSENGER_ADJ = {
    "Adult":          0.00,
    "Corporate":     -0.05,
    "Student":       -0.10,
    "Senior Citizen":-0.08,
    "Child":         -0.15,
}
STRATEGIC_OPTIONS = [
    "None — let AI decide",
    "Grow Traffic — prioritise volume, price competitively",
    "Charge Premium — maximise revenue per seat",
    "Match Competition — stay within 3% of lowest competitor",
    "Holiday Surge — apply festival premium pricing",
    "Fill Last Seats — aggressive discounting to maximise load",
]

# ── PAGE CONFIG ──────────────────────────────────────────────
st.set_page_config(
    page_title="IndiGo · Pricing Intelligence",
    page_icon="✈️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ── DESIGN SYSTEM ────────────────────────────────────────────
# Aviation palette: deep sky navy, horizon blue, contrail white,
# altitude teal accent, warning amber, descent red
# Typography: Barlow Condensed for display (cockpit instrument feel),
# Inter for body, JetBrains Mono for data

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@400;600;700;800&family=Inter:wght@300;400;500;600&family=JetBrains+Mono:wght@400;600&display=swap');

/* ── Reset & base ── */
html, body, [class*="css"] {
    font-family: 'Inter', sans-serif;
    background: #05101f;
    color: #c8dff0;
}
.main { background: #05101f; }
.block-container { padding: 1.2rem 1.8rem 2rem; max-width: 100%; }

/* ── Runway header — the signature element ── */
/* A horizontal "runway" strip with flight path dots — aviation vernacular */
.runway-header {
    background: linear-gradient(90deg, #05101f 0%, #0b1f3a 40%, #0d2a4f 70%, #05101f 100%);
    border-top: 1px solid #1a4a7a;
    border-bottom: 1px solid #1a4a7a;
    padding: 1.2rem 2rem;
    margin-bottom: 1.6rem;
    display: flex;
    align-items: center;
    justify-content: space-between;
    position: relative;
    overflow: hidden;
}
.runway-header::before {
    content: '';
    position: absolute;
    bottom: 0; left: 0; right: 0;
    height: 2px;
    background: repeating-linear-gradient(
        90deg,
        #1e6bb8 0px, #1e6bb8 20px,
        transparent 20px, transparent 40px
    );
    opacity: 0.4;
}
.rh-left { display: flex; align-items: center; gap: 1.2rem; }
.rh-callsign {
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 2rem;
    font-weight: 800;
    color: #ffffff;
    letter-spacing: 0.04em;
    line-height: 1;
}
.rh-sub {
    font-size: 0.72rem;
    color: #4a8abf;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    margin-top: 0.2rem;
}
.rh-divider {
    width: 1px;
    height: 36px;
    background: #1a4a7a;
}
.rh-stat { text-align: center; }
.rh-stat-val {
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 1.4rem;
    font-weight: 700;
    color: #5bc4f5;
    line-height: 1;
}
.rh-stat-label {
    font-size: 0.62rem;
    color: #4a8abf;
    text-transform: uppercase;
    letter-spacing: 0.08em;
}
.transponder {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    background: #051828;
    border: 1px solid #1a4a7a;
    border-radius: 4px;
    padding: 0.4rem 0.8rem;
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.7rem;
    color: #5bc4f5;
}
.transponder-dot {
    width: 7px; height: 7px;
    background: #2ecc71;
    border-radius: 50%;
    animation: pulse 2s infinite;
}
@keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.3; }
}

/* ── Section labels ── */
.sec-label {
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 0.75rem;
    font-weight: 700;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    color: #3a7abf;
    margin: 0 0 0.8rem 0;
    padding: 0.4rem 0.8rem;
    background: #071828;
    border-left: 3px solid #1e6bb8;
    border-radius: 0 4px 4px 0;
    display: inline-block;
}

/* ── KPI strip ── */
.kpi-strip {
    display: flex;
    gap: 1px;
    background: #0d2035;
    border: 1px solid #1a4a7a;
    border-radius: 8px;
    overflow: hidden;
    margin-bottom: 1.4rem;
}
.kpi-cell {
    flex: 1;
    padding: 0.9rem 1.2rem;
    background: #071828;
}
.kpi-cell:hover { background: #0b2038; }
.kpi-val {
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 1.8rem;
    font-weight: 700;
    color: #ffffff;
    line-height: 1;
}
.kpi-lbl {
    font-size: 0.65rem;
    color: #3a7abf;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    margin-top: 0.3rem;
}
.kpi-delta-up   { font-size: 0.72rem; color: #2ecc71; margin-top: 0.2rem; }
.kpi-delta-down { font-size: 0.72rem; color: #e74c3c; margin-top: 0.2rem; }
.kpi-delta-neu  { font-size: 0.72rem; color: #4a8abf; margin-top: 0.2rem; }

/* ── Fare table ── */
.altimeter {
    width: 100%;
    border-collapse: separate;
    border-spacing: 0;
    font-size: 0.8rem;
    border: 1px solid #0d2035;
    border-radius: 8px;
    overflow: hidden;
}
.altimeter thead tr { background: #071828; }
.altimeter th {
    padding: 0.6rem 0.8rem;
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 0.7rem;
    font-weight: 600;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: #3a7abf;
    border-bottom: 1px solid #0d2035;
    text-align: left;
    white-space: nowrap;
}
.altimeter td {
    padding: 0.55rem 0.8rem;
    border-bottom: 1px solid #071828;
    color: #a8c8e8;
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.76rem;
    background: #05101f;
}
.altimeter tr:last-child td { border-bottom: none; }
.altimeter tr:hover td { background: #071828; }
.fare-indigo   { color: #5bc4f5 !important; font-weight: 600; }
.fare-cheaper  { color: #27ae60 !important; font-weight: 600; }
.fare-pricier  { color: #e74c3c !important; }
.fare-similar  { color: #f39c12 !important; }
.fare-ai       { color: #a855f7 !important; font-weight: 600; }
.lf-g { color: #2ecc71; font-weight: 600; }
.lf-a { color: #f39c12; font-weight: 600; }
.lf-r { color: #e74c3c; font-weight: 600; }

/* ── AI panel ── */
.ai-cockpit {
    background: #071828;
    border: 1px solid #1a4a7a;
    border-radius: 10px;
    padding: 1.2rem 1.4rem;
    margin-top: 0.8rem;
    position: relative;
}
.ai-cockpit::before {
    content: 'AI ENGINE';
    position: absolute;
    top: -1px; right: 16px;
    background: #1e6bb8;
    color: white;
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 0.62rem;
    letter-spacing: 0.12em;
    padding: 0.15rem 0.6rem;
    border-radius: 0 0 4px 4px;
}
.ai-badge-approve {
    display: inline-block;
    background: #0a2e18;
    border: 1px solid #1a6b3a;
    color: #2ecc71;
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 0.9rem;
    font-weight: 700;
    letter-spacing: 0.1em;
    padding: 0.3rem 0.9rem;
    border-radius: 4px;
    margin-bottom: 0.8rem;
}
.ai-badge-override {
    display: inline-block;
    background: #2e1a08;
    border: 1px solid #7a4a1a;
    color: #f39c12;
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 0.9rem;
    font-weight: 700;
    letter-spacing: 0.1em;
    padding: 0.3rem 0.9rem;
    border-radius: 4px;
    margin-bottom: 0.8rem;
}
.ai-price {
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 2.8rem;
    font-weight: 800;
    color: #ffffff;
    line-height: 1;
    letter-spacing: -0.01em;
}
.ai-rationale {
    font-size: 0.8rem;
    color: #6a9abf;
    line-height: 1.65;
    margin-top: 0.8rem;
    padding: 0.6rem 0.8rem;
    background: #05101f;
    border-left: 2px solid #1e6bb8;
    border-radius: 0 4px 4px 0;
}
.flight-card {
    background: #071828;
    border: 1px solid #0d2035;
    border-radius: 8px;
    padding: 0.8rem 1rem;
    margin-bottom: 0.8rem;
    font-size: 0.78rem;
    color: #6a9abf;
    line-height: 1.8;
}
.flight-card-title {
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 1.1rem;
    font-weight: 700;
    color: #ffffff;
    letter-spacing: 0.05em;
}

/* ── Sidebar ── */
section[data-testid="stSidebar"] {
    background: #030c18 !important;
    border-right: 1px solid #0d2035;
}
section[data-testid="stSidebar"] .block-container { padding: 1rem 0.8rem; }
.sidebar-logo {
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 1.1rem;
    font-weight: 800;
    color: #5bc4f5;
    letter-spacing: 0.1em;
    padding: 0.5rem 0 1rem 0;
    border-bottom: 1px solid #0d2035;
    margin-bottom: 1rem;
}

/* ── Streamlit widget overrides ── */
.stSelectbox label, .stMultiSelect label,
.stDateInput label, .stRadio label {
    color: #3a7abf !important;
    font-size: 0.68rem !important;
    font-weight: 600 !important;
    text-transform: uppercase;
    letter-spacing: 0.1em;
}
.stSelectbox > div > div {
    background: #071828 !important;
    border-color: #0d2035 !important;
    color: #c8dff0 !important;
}
.stButton > button {
    background: linear-gradient(135deg, #1e4a8f 0%, #1e6bb8 100%);
    color: white;
    border: none;
    border-radius: 6px;
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 0.95rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    padding: 0.55rem 1.2rem;
    width: 100%;
}
.stButton > button:hover { background: linear-gradient(135deg, #2560b5 0%, #2880d0 100%); }
div[data-testid="metric-container"] {
    background: #071828 !important;
    border: 1px solid #0d2035 !important;
    border-radius: 8px;
    padding: 0.8rem 1rem;
}
div[data-testid="metric-container"] label { color: #3a7abf !important; font-size: 0.7rem !important; }
div[data-testid="metric-container"] [data-testid="metric-value"] { color: #ffffff !important; font-family: 'Barlow Condensed', sans-serif !important; font-size: 1.6rem !important; }
.stRadio > div { flex-direction: row; gap: 1rem; }
.stRadio > div > label { font-size: 0.8rem !important; color: #a8c8e8 !important; text-transform: none !important; letter-spacing: 0 !important; font-weight: 400 !important; }
div[data-baseweb="notification"] { background: #071828; border-color: #1e6bb8; }
</style>
""", unsafe_allow_html=True)


# ── SHEETS CONNECTION ────────────────────────────────────────
@st.cache_resource
def get_sheet_client():
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"], scopes=scope
    )
    return gspread.authorize(creds).open(GOOGLE_SHEET_NAME)

@st.cache_data(ttl=300)
def load_data():
    sheet     = get_sheet_client()
    comp_df   = pd.DataFrame(sheet.worksheet(COMPETITOR_TAB).get_all_records())
    indigo_df = pd.DataFrame(sheet.worksheet(INDIGO_OPS_TAB).get_all_records())
    for df in [comp_df, indigo_df]:
        for col in ["Departure Date", "Scrape Date", "Date"]:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce")
    if "Load Factor" in indigo_df.columns:
        indigo_df["Load Factor"] = pd.to_numeric(indigo_df["Load Factor"], errors="coerce")
    if "Fare (INR)" in comp_df.columns:
        comp_df["Fare (INR)"] = pd.to_numeric(comp_df["Fare (INR)"], errors="coerce")
    return comp_df, indigo_df

def load_feedback():
    try:
        ws   = get_sheet_client().worksheet(FEEDBACK_TAB)
        data = ws.get_all_records()
        return pd.DataFrame(data) if data else pd.DataFrame()
    except:
        return pd.DataFrame()

def save_feedback(row):
    sheet = get_sheet_client()
    try:
        ws = sheet.worksheet(FEEDBACK_TAB)
    except:
        ws = sheet.add_worksheet(FEEDBACK_TAB, rows=1000, cols=20)
        ws.append_row([
            "Timestamp","Route","Flight No.","Departure Time","Departure Date",
            "Cabin Class","Passenger Type","Trip Type","Days to Departure",
            "Load Factor","Arithmetic Fare","AI Decision","AI Suggested Fare",
            "AI Rationale","Manager Decision","Final Fare Used",
            "Strategic Direction","Manager Notes"
        ])
    ws.append_row(list(row.values()))


# ── PRICING LOGIC (from Excel MVP) ──────────────────────────
def calculate_arithmetic_fare(route, cabin, days_to_dep, load_factor,
                               best_comp_fare, is_holiday, dep_hour,
                               passenger_type="Adult", trip_type="One Way"):
    base = BASE_FARES.get(route, 5000)

    # Advance booking
    if   days_to_dep <= 3:  adv = 0.20
    elif days_to_dep <= 7:  adv = 0.15
    elif days_to_dep <= 14: adv = 0.10
    elif days_to_dep <= 30: adv = 0.00
    elif days_to_dep <= 60: adv = -0.05
    else:                    adv = -0.10

    # Load factor
    if   load_factor <= 0.40: lf_adj = -0.10
    elif load_factor <= 0.70: lf_adj =  0.00
    elif load_factor <= 0.85: lf_adj =  0.15
    else:                      lf_adj =  0.30

    # Cabin
    cabin_adj = {"Economy": 0.0, "Premium Economy": 0.50, "Business": 0.80}.get(cabin, 0)

    # Passenger type (from Excel MVP)
    pax_adj = PASSENGER_ADJ.get(passenger_type, 0.0)

    # Trip type
    trip_adj = -0.05 if trip_type == "Round Trip" else 0.0

    # Competition
    comp_adj = 0.0
    if best_comp_fare and best_comp_fare > 0:
        ratio = (base * (1 + cabin_adj)) / best_comp_fare
        if ratio > 1.10:   comp_adj = -0.05
        elif ratio < 0.90: comp_adj =  0.05

    # Time slot
    h = int(dep_hour)
    if   0  <= h <= 5:  time_adj = -0.05
    elif 6  <= h <= 8:  time_adj =  0.12
    elif 9  <= h <= 11: time_adj =  0.18
    elif 12 <= h <= 15: time_adj =  0.00
    elif 16 <= h <= 20: time_adj =  0.15
    else:                time_adj = -0.03

    # Holiday
    hol_adj = 0.15 if is_holiday else 0.0

    total = max(-0.30, min(1.0,
        adv + lf_adj + cabin_adj + pax_adj + trip_adj
        + comp_adj + time_adj + hol_adj
    ))
    return int(base * (1 + total)), {
        "Advance Booking": f"{adv*100:+.0f}%",
        "Load Factor":     f"{lf_adj*100:+.0f}%",
        "Cabin Class":     f"{cabin_adj*100:+.0f}%",
        "Passenger Type":  f"{pax_adj*100:+.0f}%",
        "Trip Type":       f"{trip_adj*100:+.0f}%",
        "Competition":     f"{comp_adj*100:+.0f}%",
        "Time Slot":       f"{time_adj*100:+.0f}%",
        "Holiday":         f"{hol_adj*100:+.0f}%",
        "Total":           f"{total*100:.1f}%",
    }


# ── GEMINI ───────────────────────────────────────────────────
def call_gemini(route, flight_no, dep_time, cabin, dep_date,
                days_to_dep, load_factor, arithmetic_fare,
                comp_fares, strategic_direction, feedback_history,
                passenger_type, trip_type):

    comp_text = "\n".join([
        f"  {a} ({fn} {ft}): ₹{fare:,}"
        for a, fn, ft, fare in comp_fares
    ]) or "  No competitor data"

    strategy_text = (
        f"\n⚡ STRATEGIC DIRECTION: {strategic_direction}\n"
        if strategic_direction and "None" not in strategic_direction else ""
    )
    history_text = ""
    if feedback_history:
        history_text = "\nRecent outcomes on this route:\n"
        for h in feedback_history[-3:]:
            history_text += (
                f"  • {h.get('Departure Date','')}: AI ₹{h.get('AI Suggested Fare','')}, "
                f"Manager {h.get('Manager Decision','')}, "
                f"Final ₹{h.get('Final Fare Used','')}\n"
            )

    prompt = f"""You are a senior pricing analyst at IndiGo Airlines.

Flight: {flight_no} | Route: {route} | Departure: {dep_time} on {dep_date}
Cabin: {cabin} | Passenger: {passenger_type} | Trip: {trip_type}
Days to Departure: {days_to_dep} | Load Factor: {round(load_factor*100,1)}%

Arithmetic fare calculated: ₹{arithmetic_fare:,}

Competing flights (same route, similar time slot):
{comp_text}
{strategy_text}{history_text}
Rules:
- Load factor >85% → no discounts whatsoever
- Match morning vs morning, evening vs evening
- One precise fare — no ranges
- Follow strategic direction strongly if set

Reply in EXACTLY this format only:
Decision: Approve OR Override
Suggested Fare: ₹[number]
Rationale: [2-3 plain English sentences]"""

    url     = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent?key={GEMINI_API_KEY}"
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    try:
        resp = requests.post(url, json=payload, timeout=30)
        if resp.status_code != 200:
            return "Approve", arithmetic_fare, f"API error {resp.status_code}."
        text = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        decision, fare, rationale = "Approve", arithmetic_fare, ""
        for line in text.split("\n"):
            if line.startswith("Decision:"):
                decision = line.replace("Decision:", "").strip()
            elif line.startswith("Suggested Fare:"):
                try:
                    fare = int(line.replace("Suggested Fare:","")
                                   .replace("₹","").replace(",","").strip())
                except: pass
            elif line.startswith("Rationale:"):
                rationale = line.replace("Rationale:","").strip()
        return decision, fare, rationale
    except Exception as e:
        return "Approve", arithmetic_fare, f"Connection error. ({e})"


# ── HELPERS ──────────────────────────────────────────────────
def lf_cls(lf):
    if lf <= 0.70: return "lf-g", "●"
    if lf <= 0.85: return "lf-a", "●"
    return "lf-r", "●"

def dep_hour(t):
    try: return int(str(t).split(":")[0])
    except: return 10

def inr(v):
    try: return f"₹{int(v):,}"
    except: return "—"

def plot_cfg():
    return dict(
        plot_bgcolor="#05101f", paper_bgcolor="#05101f",
        font=dict(color="#6a9abf", family="Inter", size=11),
        margin=dict(l=8, r=8, t=8, b=8),
    )


# ── MAIN ─────────────────────────────────────────────────────
def main():

    # Load data
    try:
        comp_df, indigo_df = load_data()
        feedback_df        = load_feedback()
    except Exception as e:
        st.error(f"Could not connect to Google Sheets: {e}")
        st.info("Check that credentials are set in Streamlit Secrets and the sheet is shared with the service account.")
        return

    today    = pd.Timestamp.today().normalize()
    date_min = today - timedelta(days=30)
    date_max = today + timedelta(days=30)

    # ── SIDEBAR ─────────────────────────────────────────────
    with st.sidebar:
        st.markdown('<div class="sidebar-logo">✈ INDIGO · PID</div>', unsafe_allow_html=True)
        st.markdown("**ROUTE FILTERS**")

        routes     = sorted(indigo_df["Route"].dropna().unique().tolist())
        sel_route  = st.selectbox("Route", routes)

        cabins     = sorted(indigo_df["Cabin Class"].dropna().unique().tolist())
        sel_cabin  = st.selectbox("Cabin Class", cabins)

        trip_type  = st.radio("Trip Type", ["One Way", "Round Trip"], index=0)

        pax_type   = st.selectbox(
            "Passenger Type",
            ["Adult", "Corporate", "Student", "Senior Citizen", "Child"],
            index=0
        )

        date_range = st.date_input(
            "Departure Date Range",
            value=(date_min.date(), date_max.date()),
            min_value=date_min.date(),
            max_value=date_max.date(),
        )
        if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
            d_from = pd.Timestamp(date_range[0])
            d_to   = pd.Timestamp(date_range[1])
        else:
            d_from, d_to = date_min, date_max

        st.markdown("---")
        if st.button("⟳  Refresh Data"):
            st.cache_data.clear()
            st.rerun()
        st.caption(f"Updated {datetime.now().strftime('%H:%M:%S')}")

    # ── FILTER ──────────────────────────────────────────────
    comp_f   = comp_df[
        (comp_df["Route"] == sel_route) &
        (comp_df["Cabin Class"] == sel_cabin) &
        (comp_df["Departure Date"] >= d_from) &
        (comp_df["Departure Date"] <= d_to)
    ].copy()

    indigo_f = indigo_df[
        (indigo_df["Route"] == sel_route) &
        (indigo_df["Cabin Class"] == sel_cabin) &
        (indigo_df["Departure Date"] >= d_from) &
        (indigo_df["Departure Date"] <= d_to)
    ].copy()

    # Most recent scrape per flight + departure
    if not comp_f.empty and "Scrape Date" in comp_f.columns:
        comp_f = (comp_f.sort_values("Scrape Date")
                        .groupby(["Airline","Flight No.","Departure Date"], as_index=False)
                        .last())
    if not indigo_f.empty:
        dc = "Date" if "Date" in indigo_f.columns else "Scrape Date"
        if dc in indigo_f.columns:
            indigo_f = (indigo_f.sort_values(dc)
                                .groupby(["Flight No.","Departure Date"], as_index=False)
                                .last())

    # ── RUNWAY HEADER ────────────────────────────────────────
    avg_lf      = indigo_f["Load Factor"].mean() if not indigo_f.empty else 0
    n_flights   = indigo_f["Flight No."].nunique() if not indigo_f.empty else 0
    base_fare   = BASE_FARES.get(sel_route, 5000)
    cost_seat   = COST_PER_SEAT.get(sel_route, 3000)
    n_fb        = len(feedback_df) if not feedback_df.empty else 0

    st.markdown(f"""
    <div class="runway-header">
        <div class="rh-left">
            <div>
                <div class="rh-callsign">✈ IndiGo Pricing Intelligence</div>
                <div class="rh-sub">Real-Time Fare Monitor · AI Recommendation Engine · ISB ALP 2026</div>
            </div>
            <div class="rh-divider"></div>
            <div class="rh-stat">
                <div class="rh-stat-val">{sel_route.replace(' to ',' → ')}</div>
                <div class="rh-stat-label">Active Route</div>
            </div>
            <div class="rh-divider"></div>
            <div class="rh-stat">
                <div class="rh-stat-val">{round(avg_lf*100,1)}%</div>
                <div class="rh-stat-label">Avg Load Factor</div>
            </div>
            <div class="rh-divider"></div>
            <div class="rh-stat">
                <div class="rh-stat-val">{n_flights}</div>
                <div class="rh-stat-label">Flights Tracked</div>
            </div>
            <div class="rh-divider"></div>
            <div class="rh-stat">
                <div class="rh-stat-val">{n_fb}</div>
                <div class="rh-stat-label">Manager Decisions</div>
            </div>
        </div>
        <div class="transponder">
            <div class="transponder-dot"></div>
            SQUAWK · LIVE
        </div>
    </div>
    """, unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════
    # SECTION 1 — FARE COMPARISON TABLE
    # ══════════════════════════════════════════════════════════
    st.markdown('<div class="sec-label">▸ Fare Comparison — All Airlines</div>', unsafe_allow_html=True)

    if indigo_f.empty and comp_f.empty:
        st.info("No data for this selection. Adjust the filters.")
    else:
        ai_fares = comp_f[comp_f["Airline"]=="Air India"][
            ["Flight No.","Departure Date","Fare (INR)","Departure Time"]
        ].rename(columns={"Fare (INR)":"AI_Fare","Flight No.":"AI_Flt","Departure Time":"AI_Time"}) \
         if not comp_f.empty else pd.DataFrame()

        qp_fares = comp_f[comp_f["Airline"]=="Akasa Air"][
            ["Flight No.","Departure Date","Fare (INR)","Departure Time"]
        ].rename(columns={"Fare (INR)":"QP_Fare","Flight No.":"QP_Flt","Departure Time":"QP_Time"}) \
         if not comp_f.empty else pd.DataFrame()

        # Build accepted AI recommendation lookup
        ai_rec_lookup = {}
        if not feedback_df.empty and "Manager Decision" in feedback_df.columns:
            accepted = feedback_df[
                feedback_df["Manager Decision"].isin(["Accepted","Overridden"]) &
                (feedback_df.get("Route", pd.Series(dtype=str)) == sel_route)
            ]
            for _, fr in accepted.iterrows():
                k = (str(fr.get("Flight No.","")), str(fr.get("Departure Date",""))[:10])
                ai_rec_lookup[k] = int(fr.get("Final Fare Used", 0))

        rows = []
        for _, row in indigo_f.iterrows():
            dep_date    = row["Departure Date"]
            dep_str     = str(dep_date)[:10]
            flight_no   = str(row.get("Flight No.",""))
            dep_time    = str(row.get("Departure Time",""))
            time_slot   = str(row.get("Time Slot",""))
            days_out    = row.get("Days to Departure","")
            lf          = float(row.get("Load Factor", 0))
            seats_sold  = int(row.get("Seats Sold", 0))
            total_seats = int(row.get("Total Seats", 180))
            holiday     = str(row.get("Holiday / Festival","No"))
            lf_c, lf_dot = lf_cls(lf)
            indigo_base = BASE_FARES.get(sel_route, 5000)

            # Arithmetic fare with passenger + trip type
            best_comp_fare = 0
            if not comp_f.empty:
                same_date = comp_f[comp_f["Departure Date"]==dep_date]
                if not same_date.empty:
                    best_comp_fare = int(same_date["Fare (INR)"].min())

            arith, _ = calculate_arithmetic_fare(
                sel_route, sel_cabin, int(days_out) if days_out else 30,
                lf, best_comp_fare, holiday=="Yes",
                dep_hour(dep_time), pax_type, trip_type
            )

            ai_rec = ai_rec_lookup.get((flight_no, dep_str), None)

            ai_f, ai_flt, ai_t = "—","—","—"
            qp_f, qp_flt, qp_t = "—","—","—"
            if not ai_fares.empty:
                m = ai_fares[ai_fares["Departure Date"]==dep_date]
                if not m.empty:
                    ai_f   = int(m.iloc[0]["AI_Fare"])
                    ai_flt = m.iloc[0]["AI_Flt"]
                    ai_t   = m.iloc[0]["AI_Time"]
            if not qp_fares.empty:
                m = qp_fares[qp_fares["Departure Date"]==dep_date]
                if not m.empty:
                    qp_f   = int(m.iloc[0]["QP_Fare"])
                    qp_flt = m.iloc[0]["QP_Flt"]
                    qp_t   = m.iloc[0]["QP_Time"]

            rows.append({
                "dep_date": dep_date.strftime("%d %b %Y") if hasattr(dep_date,"strftime") else dep_str,
                "days_out": days_out,
                "flight":   f"{flight_no} {dep_time}",
                "slot":     time_slot,
                "lf": lf, "lf_c": lf_c, "lf_dot": lf_dot,
                "seats":    f"{seats_sold}/{total_seats}",
                "indigo":   indigo_base,
                "arith":    arith,
                "ai_rec":   ai_rec,
                "ai_flt":   f"{ai_flt} {ai_t}",  "ai_f": ai_f,
                "qp_flt":   f"{qp_flt} {qp_t}",  "qp_f": qp_f,
            })

        if rows:
            html = """<table class="altimeter"><thead><tr>
            <th>Departure</th><th>Days</th><th>IndiGo Flight</th><th>Slot</th>
            <th>Load Factor</th><th>Seats</th>
            <th>IndiGo Base ₹</th><th>Arithmetic ₹</th><th>AI Rec ₹</th>
            <th>Air India Flight</th><th>Air India ₹</th>
            <th>Akasa Flight</th><th>Akasa ₹</th>
            </tr></thead><tbody>"""

            for r in rows:
                def comp_cls(v, base):
                    try:
                        v = int(v)
                        if v < base * 0.97:  return "fare-cheaper"
                        if v > base * 1.03:  return "fare-pricier"
                        return "fare-similar"
                    except: return ""

                html += f"""<tr>
                <td>{r['dep_date']}</td>
                <td>{r['days_out']}</td>
                <td class="fare-indigo">{r['flight']}</td>
                <td>{r['slot']}</td>
                <td><span class="{r['lf_c']}">{r['lf_dot']} {round(r['lf']*100,1)}%</span></td>
                <td>{r['seats']}</td>
                <td class="fare-indigo">{inr(r['indigo'])}</td>
                <td style="color:#a855f7;font-weight:600">{inr(r['arith'])}</td>
                <td class="fare-ai">{inr(r['ai_rec']) if r['ai_rec'] else '—'}</td>
                <td style="color:#6a9abf">{r['ai_flt']}</td>
                <td><span class="{comp_cls(r['ai_f'], r['indigo'])}">{inr(r['ai_f'])}</span></td>
                <td style="color:#6a9abf">{r['qp_flt']}</td>
                <td><span class="{comp_cls(r['qp_f'], r['indigo'])}">{inr(r['qp_f'])}</span></td>
                </tr>"""
            html += "</tbody></table>"
            st.markdown(html, unsafe_allow_html=True)

            st.markdown("""
            <div style="font-size:0.68rem;color:#2a5a8f;margin-top:0.5rem;padding-left:0.5rem;">
            🔵 IndiGo Base &nbsp;·&nbsp;
            <span style="color:#a855f7">■</span> Arithmetic Fare (with your filters) &nbsp;·&nbsp;
            <span style="color:#a855f7">■</span> AI Rec (accepted decisions) &nbsp;·&nbsp;
            <span style="color:#27ae60">■</span> Competitor cheaper &nbsp;·&nbsp;
            <span style="color:#e74c3c">■</span> Competitor pricier
            </div>""", unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════
    # SECTION 2 — LOAD FACTOR + PRICE TREND + AI PANEL
    # ══════════════════════════════════════════════════════════
    col_charts, col_ai = st.columns([1.1, 0.9], gap="large")

    with col_charts:
        # ── Load Factor by Flight ────────────────────────────
        st.markdown('<div class="sec-label">▸ Load Factor — IndiGo Flights</div>', unsafe_allow_html=True)

        if not indigo_f.empty:
            lf_agg = (indigo_f
                      .groupby(["Flight No.","Departure Time"])["Load Factor"]
                      .mean().reset_index())
            lf_agg["LF%"]   = (lf_agg["Load Factor"]*100).round(1)
            lf_agg["Label"] = lf_agg["Flight No."].astype(str) + " " + lf_agg["Departure Time"].astype(str)
            lf_agg["Color"] = lf_agg["Load Factor"].apply(
                lambda x: "#2ecc71" if x<=0.70 else ("#f39c12" if x<=0.85 else "#e74c3c"))

            fig = go.Figure()
            fig.add_trace(go.Bar(
                x=lf_agg["Label"], y=lf_agg["LF%"],
                marker_color=lf_agg["Color"],
                text=lf_agg["LF%"].apply(lambda x: f"{x}%"),
                textposition="outside",
                textfont=dict(color="#c8dff0", size=11, family="Barlow Condensed"),
            ))
            fig.add_hline(y=70, line_dash="dot", line_color="#f39c12", line_width=1,
                          annotation_text="70%", annotation_font_color="#f39c12", annotation_font_size=10)
            fig.add_hline(y=85, line_dash="dot", line_color="#e74c3c", line_width=1,
                          annotation_text="85%", annotation_font_color="#e74c3c", annotation_font_size=10)
            cfg = plot_cfg()
            fig.update_layout(**cfg,
                xaxis=dict(gridcolor="#0d2035", tickfont=dict(size=10, family="JetBrains Mono"), tickcolor="#0d2035"),
                yaxis=dict(gridcolor="#0d2035", range=[0,115], title="Load %", titlefont=dict(size=10)),
                height=220, showlegend=False,
            )
            st.plotly_chart(fig, use_container_width=True)

            # ── Load Factor Over Time ────────────────────────
            st.markdown('<div class="sec-label">▸ Load Factor Over Time</div>', unsafe_allow_html=True)

            dc = "Date" if "Date" in indigo_df.columns else "Scrape Date"
            lf_time = indigo_df[
                (indigo_df["Route"] == sel_route) &
                (indigo_df["Cabin Class"] == sel_cabin) &
                (indigo_df[dc] >= date_min)
            ].copy()

            if not lf_time.empty and dc in lf_time.columns:
                lf_time["Load Factor"] = pd.to_numeric(lf_time["Load Factor"], errors="coerce")
                lf_time["LF%"] = (lf_time["Load Factor"]*100).round(1)
                lf_time["Label"] = lf_time["Flight No."].astype(str) + " " + lf_time["Departure Time"].astype(str)
                lf_pivot = lf_time.groupby([dc,"Label"])["LF%"].mean().reset_index()

                fig_lft = px.line(
                    lf_pivot, x=dc, y="LF%", color="Label",
                    markers=True,
                    color_discrete_sequence=["#5bc4f5","#2ecc71","#f39c12","#e74c3c",
                                              "#a855f7","#3498db","#1abc9c","#e67e22"],
                )
                fig_lft.add_hline(y=85, line_dash="dot", line_color="#e74c3c", line_width=1)
                fig_lft.update_layout(**plot_cfg(),
                    xaxis=dict(gridcolor="#0d2035", title=""),
                    yaxis=dict(gridcolor="#0d2035", title="Load %", range=[0,105]),
                    legend=dict(bgcolor="#05101f", bordercolor="#0d2035",
                                font=dict(size=9, family="JetBrains Mono")),
                    height=220,
                )
                st.plotly_chart(fig_lft, use_container_width=True)

            # ── Competitor Price Trend ───────────────────────
            st.markdown('<div class="sec-label">▸ Competitor Price Trend — Last 30 Days</div>', unsafe_allow_html=True)

            if not comp_f.empty and "Scrape Date" in comp_df.columns:
                trend = comp_df[
                    (comp_df["Route"]==sel_route) &
                    (comp_df["Cabin Class"]==sel_cabin) &
                    (comp_df["Scrape Date"]>=date_min)
                ].groupby(["Scrape Date","Airline"])["Fare (INR)"].mean().reset_index()

                if not trend.empty:
                    fig2 = px.line(trend, x="Scrape Date", y="Fare (INR)",
                                   color="Airline", markers=True,
                                   color_discrete_map={
                                       "Air India":"#3498db",
                                       "Akasa Air":"#e74c3c"
                                   })
                    fig2.update_layout(**plot_cfg(),
                        xaxis=dict(gridcolor="#0d2035", title=""),
                        yaxis=dict(gridcolor="#0d2035", title="Avg Fare ₹"),
                        legend=dict(bgcolor="#05101f", bordercolor="#0d2035"),
                        height=200,
                    )
                    st.plotly_chart(fig2, use_container_width=True)
        else:
            st.info("No IndiGo data for this selection.")

    # ── AI RECOMMENDATION PANEL ──────────────────────────────
    with col_ai:
        st.markdown('<div class="sec-label">▸ AI Pricing Recommendation</div>', unsafe_allow_html=True)

        if not indigo_f.empty:
            flight_opts = (indigo_f[["Flight No.","Departure Time","Departure Date","Days to Departure"]]
                           .drop_duplicates()
                           .sort_values("Departure Date"))
            labels = [
                f"{r['Flight No.']} {r['Departure Time']} — "
                f"{pd.Timestamp(r['Departure Date']).strftime('%d %b') if hasattr(r['Departure Date'],'strftime') else r['Departure Date']} "
                f"({r['Days to Departure']}d)"
                for _, r in flight_opts.iterrows()
            ]
            sel_label  = st.selectbox("Select Flight", labels)
            sel_idx    = labels.index(sel_label)
            sel_r      = flight_opts.iloc[sel_idx]
            sel_flt    = sel_r["Flight No."]
            sel_time   = sel_r["Departure Time"]
            sel_date   = sel_r["Departure Date"]
            sel_days   = int(sel_r["Days to Departure"])

            ind_sel = indigo_f[
                (indigo_f["Flight No."]==sel_flt) &
                (indigo_f["Departure Date"]==sel_date)
            ]
            sel_lf      = float(ind_sel["Load Factor"].iloc[0]) if not ind_sel.empty else 0.6
            sel_sold    = int(ind_sel["Seats Sold"].iloc[0]) if not ind_sel.empty else 0
            sel_total   = int(ind_sel["Total Seats"].iloc[0]) if not ind_sel.empty else 180
            sel_holiday = str(ind_sel["Holiday / Festival"].iloc[0]) if not ind_sel.empty else "No"
            sel_slot    = str(ind_sel["Time Slot"].iloc[0]) if not ind_sel.empty else ""
            sel_hour    = dep_hour(sel_time)

            comp_sel  = comp_f[comp_f["Departure Date"]==sel_date]
            comp_list = [(cr["Airline"], cr["Flight No."], cr["Departure Time"], int(cr["Fare (INR)"]))
                         for _, cr in comp_sel.iterrows()]
            best_comp = min([c[3] for c in comp_list], default=0)

            arith_fare, breakdown = calculate_arithmetic_fare(
                sel_route, sel_cabin, sel_days, sel_lf,
                best_comp, sel_holiday=="Yes", sel_hour, pax_type, trip_type
            )

            lf_c, lf_dot = lf_cls(sel_lf)
            st.markdown(f"""
            <div class="flight-card">
                <div class="flight-card-title">{sel_flt} &nbsp;·&nbsp; {sel_time} &nbsp;·&nbsp; {sel_slot}</div>
                Load Factor: <span class="{lf_c}">{lf_dot} {round(sel_lf*100,1)}%</span>
                &nbsp;·&nbsp; Seats: {sel_sold}/{sel_total} &nbsp;·&nbsp; {sel_days} days out<br>
                Passenger: {pax_type} &nbsp;·&nbsp; {trip_type}<br>
                Arithmetic Fare: <span style="color:#a855f7;font-family:'JetBrains Mono',monospace;font-weight:600;">{inr(arith_fare)}</span>
            </div>
            """, unsafe_allow_html=True)

            # Adjustment breakdown expander
            with st.expander("See fare adjustment breakdown"):
                for k, v in breakdown.items():
                    col_a, col_b = st.columns([2,1])
                    col_a.caption(k)
                    col_b.caption(v)

            # Strategic direction BEFORE
            st.markdown("**Set Strategic Direction** *(influences AI)*")
            strategic = st.selectbox("Direction", STRATEGIC_OPTIONS,
                                     label_visibility="collapsed")

            if st.button("🤖  Get AI Recommendation", key="get_ai"):
                fb_hist = []
                if not feedback_df.empty:
                    fb_hist = feedback_df[
                        feedback_df.get("Route", pd.Series(dtype=str)) == sel_route
                    ].to_dict("records") if "Route" in feedback_df.columns else []

                with st.spinner("Contacting AI engine..."):
                    decision, ai_fare, rationale = call_gemini(
                        sel_route, sel_flt, sel_time, sel_cabin,
                        str(sel_date)[:10], sel_days, sel_lf,
                        arith_fare, comp_list, strategic, fb_hist,
                        pax_type, trip_type
                    )
                st.session_state["ai_result"] = {
                    "decision": decision, "fare": ai_fare,
                    "rationale": rationale, "arith_fare": arith_fare,
                    "flight": sel_flt, "dep_time": sel_time,
                    "dep_date": str(sel_date)[:10],
                    "days": sel_days, "lf": sel_lf, "strategic": strategic,
                }

            if "ai_result" in st.session_state:
                r = st.session_state["ai_result"]
                badge = "ai-badge-approve" if r["decision"]=="Approve" else "ai-badge-override"
                icon  = "✔ APPROVE" if r["decision"]=="Approve" else "⚡ OVERRIDE"

                st.markdown(f"""
                <div class="ai-cockpit">
                    <span class="{badge}">{icon}</span><br>
                    <div class="ai-price">{inr(r['fare'])}</div>
                    <div class="ai-rationale">{r['rationale']}</div>
                </div>
                """, unsafe_allow_html=True)

                st.markdown("<br>**Manager Decision**", unsafe_allow_html=True)

                strategic_after = st.selectbox(
                    "Revise Direction (optional)", STRATEGIC_OPTIONS,
                    index=STRATEGIC_OPTIONS.index(r["strategic"])
                          if r["strategic"] in STRATEGIC_OPTIONS else 0,
                    key="s_after"
                )

                c1, c2, c3 = st.columns(3)
                with c1:
                    if st.button("✔ Accept", key="acc"):
                        save_feedback({
                            "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
                            "Route": sel_route, "Flight No.": r["flight"],
                            "Departure Time": r["dep_time"], "Departure Date": r["dep_date"],
                            "Cabin Class": sel_cabin, "Passenger Type": pax_type,
                            "Trip Type": trip_type, "Days to Departure": r["days"],
                            "Load Factor": round(r["lf"]*100,1),
                            "Arithmetic Fare": r["arith_fare"],
                            "AI Decision": r["decision"], "AI Suggested Fare": r["fare"],
                            "AI Rationale": r["rationale"], "Manager Decision": "Accepted",
                            "Final Fare Used": r["fare"],
                            "Strategic Direction": strategic_after, "Manager Notes": "",
                        })
                        st.success("Accepted ✔")
                        del st.session_state["ai_result"]
                        st.rerun()

                with c2:
                    ov_fare = st.number_input("Override ₹", min_value=500,
                                              max_value=300000, value=r["fare"],
                                              step=100, key="ov_val",
                                              label_visibility="collapsed")
                    if st.button("✏ Override", key="ovr"):
                        save_feedback({
                            "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
                            "Route": sel_route, "Flight No.": r["flight"],
                            "Departure Time": r["dep_time"], "Departure Date": r["dep_date"],
                            "Cabin Class": sel_cabin, "Passenger Type": pax_type,
                            "Trip Type": trip_type, "Days to Departure": r["days"],
                            "Load Factor": round(r["lf"]*100,1),
                            "Arithmetic Fare": r["arith_fare"],
                            "AI Decision": r["decision"], "AI Suggested Fare": r["fare"],
                            "AI Rationale": r["rationale"], "Manager Decision": "Overridden",
                            "Final Fare Used": ov_fare,
                            "Strategic Direction": strategic_after, "Manager Notes": "",
                        })
                        st.success(f"Overridden → {inr(ov_fare)}")
                        del st.session_state["ai_result"]
                        st.rerun()

                with c3:
                    if st.button("✕ Reject", key="rej"):
                        save_feedback({
                            "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
                            "Route": sel_route, "Flight No.": r["flight"],
                            "Departure Time": r["dep_time"], "Departure Date": r["dep_date"],
                            "Cabin Class": sel_cabin, "Passenger Type": pax_type,
                            "Trip Type": trip_type, "Days to Departure": r["days"],
                            "Load Factor": round(r["lf"]*100,1),
                            "Arithmetic Fare": r["arith_fare"],
                            "AI Decision": r["decision"], "AI Suggested Fare": r["fare"],
                            "AI Rationale": r["rationale"], "Manager Decision": "Rejected",
                            "Final Fare Used": r["arith_fare"],
                            "Strategic Direction": strategic_after, "Manager Notes": "",
                        })
                        st.warning("Rejected — base fare kept.")
                        del st.session_state["ai_result"]
                        st.rerun()
        else:
            st.info("No flights found. Adjust filters.")

    st.markdown("<br>", unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════
    # SECTION 3 — PROFITABILITY
    # ══════════════════════════════════════════════════════════
    st.markdown('<div class="sec-label">▸ Profitability — Accepted AI Recommendations</div>',
                unsafe_allow_html=True)

    if not feedback_df.empty and "Manager Decision" in feedback_df.columns:
        acc = feedback_df[feedback_df["Manager Decision"].isin(["Accepted","Overridden"])].copy()

        if not acc.empty:
            acc["Final Fare Used"]   = pd.to_numeric(acc["Final Fare Used"], errors="coerce")
            acc["Cost Per Seat"]     = acc["Route"].map(COST_PER_SEAT).fillna(3000)
            acc["Profit Per Seat"]   = acc["Final Fare Used"] - acc["Cost Per Seat"]
            acc["Load Factor Num"]   = pd.to_numeric(acc["Load Factor"], errors="coerce") / 100
            acc["Total Seats"]       = acc["Route"].map(TOTAL_SEATS).fillna(180)
            acc["Est Flight Profit"] = acc["Profit Per Seat"] * acc["Total Seats"] * acc["Load Factor Num"]
            acc["Base Fare"]         = acc["Route"].map(BASE_FARES).fillna(5000)
            acc["Revenue Uplift"]    = (acc["Final Fare Used"] - acc["Base Fare"]) * acc["Total Seats"] * acc["Load Factor Num"]

            p1, p2, p3, p4 = st.columns(4)
            with p1: st.metric("Decisions Recorded", len(acc))
            with p2: st.metric("Total Revenue Uplift", inr(acc["Revenue Uplift"].sum()), delta="vs base fare")
            with p3: st.metric("Avg Profit / Seat", inr(acc["Profit Per Seat"].mean()))
            with p4: st.metric("Est. Total Flight Profit", inr(acc["Est Flight Profit"].sum()))

            st.markdown("<br>", unsafe_allow_html=True)

            rp = acc.groupby("Route")["Est Flight Profit"].sum().reset_index().sort_values("Est Flight Profit")
            fig3 = go.Figure(go.Bar(
                x=rp["Est Flight Profit"], y=rp["Route"], orientation="h",
                marker_color=["#2ecc71" if x>0 else "#e74c3c" for x in rp["Est Flight Profit"]],
                text=[inr(x) for x in rp["Est Flight Profit"]],
                textposition="outside",
                textfont=dict(color="#c8dff0", size=11, family="Barlow Condensed"),
            ))
            fig3.update_layout(**plot_cfg(),
                xaxis=dict(gridcolor="#0d2035", title="Estimated Flight Profit (₹)"),
                yaxis=dict(gridcolor="#0d2035"),
                margin=dict(l=8, r=80, t=8, b=8), height=220,
            )
            st.plotly_chart(fig3, use_container_width=True)

            show = [c for c in ["Route","Flight No.","Departure Date","Cabin Class",
                                 "Passenger Type","Trip Type","Load Factor",
                                 "Arithmetic Fare","AI Suggested Fare","Final Fare Used",
                                 "Manager Decision","Profit Per Seat","Est Flight Profit"]
                    if c in acc.columns]
            st.dataframe(acc[show].sort_values("Departure Date", ascending=False),
                         use_container_width=True, hide_index=True)
        else:
            st.info("No accepted recommendations yet — use the AI panel above to get started.")
    else:
        st.info("No feedback data yet. Accept or override a recommendation — profitability will appear here.")

    # Footer
    st.markdown("""
    <div style="margin-top:2rem;padding:0.8rem 0;border-top:1px solid #0d2035;
                text-align:center;font-family:'Barlow Condensed',sans-serif;
                font-size:0.8rem;letter-spacing:0.12em;color:#1a4a7a;">
        INDIGO PRICING INTELLIGENCE &nbsp;·&nbsp; TEAM 5 ISB ALP 2026
        &nbsp;·&nbsp; POWERED BY GEMINI AI &nbsp;·&nbsp; CONFIDENTIAL
    </div>
    """, unsafe_allow_html=True)


if __name__ == "__main__":
    main()
