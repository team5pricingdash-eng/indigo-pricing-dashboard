"""
IndiGo Pricing Intelligence Dashboard v3
Team 5 — ISB Action Learning Project 2026
Light theme, impactful design, AI panel on top
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
TOTAL_SEATS_MAP = {
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

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600;700&family=DM+Mono:wght@400;500&display=swap');

/* ── Base ── */
html, body, [class*="css"] {
    font-family: 'DM Sans', sans-serif;
    background: #f0f4f9;
    color: #1a2740;
}
.main { background: #f0f4f9; }
.block-container { padding: 0 1.6rem 2rem; max-width: 100%; }

/* ── Header ── */
.pid-header {
    background: linear-gradient(135deg, #0a2d6e 0%, #1554b0 50%, #0e7dd4 100%);
    padding: 1.4rem 2rem;
    margin: 0 -1.6rem 1.6rem;
    display: flex;
    align-items: center;
    justify-content: space-between;
    box-shadow: 0 4px 24px rgba(10,45,110,0.18);
}
.pid-title {
    font-size: 1.5rem;
    font-weight: 700;
    color: #ffffff;
    letter-spacing: -0.02em;
    line-height: 1;
}
.pid-sub {
    font-size: 0.72rem;
    color: rgba(255,255,255,0.6);
    margin-top: 0.25rem;
    letter-spacing: 0.06em;
    text-transform: uppercase;
}
.pid-stats {
    display: flex;
    gap: 2rem;
    align-items: center;
}
.pid-stat-val {
    font-size: 1.6rem;
    font-weight: 700;
    color: #ffffff;
    font-family: 'DM Mono', monospace;
    line-height: 1;
}
.pid-stat-lbl {
    font-size: 0.62rem;
    color: rgba(255,255,255,0.55);
    text-transform: uppercase;
    letter-spacing: 0.08em;
}
.pid-divider {
    width: 1px; height: 36px;
    background: rgba(255,255,255,0.2);
}
.live-pill {
    background: rgba(255,255,255,0.12);
    border: 1px solid rgba(255,255,255,0.25);
    color: #7fffb0;
    border-radius: 20px;
    padding: 0.3rem 0.9rem;
    font-size: 0.7rem;
    font-weight: 600;
    letter-spacing: 0.08em;
    display: flex;
    align-items: center;
    gap: 0.4rem;
}
.live-dot {
    width: 6px; height: 6px;
    background: #2ecc71;
    border-radius: 50%;
    animation: blink 1.8s infinite;
}
@keyframes blink { 0%,100%{opacity:1} 50%{opacity:0.2} }

/* ── Section labels ── */
.sec-hd {
    font-size: 0.65rem;
    font-weight: 700;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: #1554b0;
    margin: 0 0 0.7rem 0;
    display: flex;
    align-items: center;
    gap: 0.5rem;
}
.sec-hd::after {
    content: '';
    flex: 1;
    height: 1px;
    background: #d4e0f0;
}

/* ── Cards ── */
.card {
    background: #ffffff;
    border: 1px solid #dce8f5;
    border-radius: 12px;
    padding: 1.2rem 1.4rem;
    margin-bottom: 1rem;
    box-shadow: 0 1px 8px rgba(10,45,110,0.06);
}
.card-tight { padding: 0.8rem 1rem; }

/* ── KPI strip ── */
.kpi-strip {
    display: flex;
    gap: 1rem;
    margin-bottom: 1.2rem;
}
.kpi-card {
    flex: 1;
    background: #ffffff;
    border: 1px solid #dce8f5;
    border-radius: 10px;
    padding: 0.9rem 1.2rem;
    box-shadow: 0 1px 6px rgba(10,45,110,0.05);
}
.kpi-val {
    font-size: 1.7rem;
    font-weight: 700;
    color: #0a2d6e;
    font-family: 'DM Mono', monospace;
    line-height: 1;
}
.kpi-lbl {
    font-size: 0.65rem;
    color: #6a90bf;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin-top: 0.3rem;
}
.kpi-delta-pos { font-size: 0.72rem; color: #27ae60; margin-top:0.2rem; }
.kpi-delta-neg { font-size: 0.72rem; color: #e74c3c; margin-top:0.2rem; }
.kpi-delta-neu { font-size: 0.72rem; color: #6a90bf; margin-top:0.2rem; }

/* ── Fare table ── */
.fare-tbl {
    width: 100%;
    border-collapse: separate;
    border-spacing: 0;
    font-size: 0.8rem;
    border: 1px solid #dce8f5;
    border-radius: 10px;
    overflow: hidden;
}
.fare-tbl thead tr { background: #f7faff; }
.fare-tbl th {
    padding: 0.6rem 0.8rem;
    font-size: 0.64rem;
    font-weight: 700;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: #1554b0;
    border-bottom: 2px solid #dce8f5;
    text-align: left;
    white-space: nowrap;
}
.fare-tbl td {
    padding: 0.5rem 0.8rem;
    border-bottom: 1px solid #f0f4f9;
    color: #2a4060;
    font-family: 'DM Mono', monospace;
    font-size: 0.76rem;
    background: #ffffff;
}
.fare-tbl tr:last-child td { border-bottom: none; }
.fare-tbl tr:hover td { background: #f7faff; }
.f-indigo  { color: #1554b0 !important; font-weight: 600; }
.f-arith   { color: #7c3aed !important; font-weight: 600; }
.f-airec   { color: #0891b2 !important; font-weight: 700; }
.f-cheaper { color: #16a34a !important; font-weight: 600; }
.f-pricier { color: #dc2626 !important; }
.f-similar { color: #d97706 !important; }
.lf-g { color: #16a34a; font-weight: 600; }
.lf-a { color: #d97706; font-weight: 600; }
.lf-r { color: #dc2626; font-weight: 600; }

/* ── AI panel ── */
.ai-panel {
    background: linear-gradient(135deg, #f0f7ff 0%, #e8f0fe 100%);
    border: 1.5px solid #1554b0;
    border-radius: 12px;
    padding: 1.2rem 1.4rem;
    margin-bottom: 1.2rem;
}
.ai-header-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 1rem;
}
.ai-label {
    font-size: 0.65rem;
    font-weight: 700;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: #1554b0;
}
.ai-badge-approve {
    background: #dcfce7;
    border: 1px solid #16a34a;
    color: #15803d;
    font-size: 0.75rem;
    font-weight: 700;
    letter-spacing: 0.06em;
    padding: 0.25rem 0.8rem;
    border-radius: 20px;
}
.ai-badge-override {
    background: #fef3c7;
    border: 1px solid #d97706;
    color: #b45309;
    font-size: 0.75rem;
    font-weight: 700;
    letter-spacing: 0.06em;
    padding: 0.25rem 0.8rem;
    border-radius: 20px;
}
.ai-price {
    font-size: 2.4rem;
    font-weight: 700;
    color: #0a2d6e;
    font-family: 'DM Mono', monospace;
    line-height: 1;
}
.ai-rationale {
    font-size: 0.82rem;
    color: #3a5a8a;
    line-height: 1.7;
    margin-top: 0.7rem;
    padding: 0.6rem 0.9rem;
    background: rgba(255,255,255,0.7);
    border-left: 3px solid #1554b0;
    border-radius: 0 6px 6px 0;
}
.arith-breakdown {
    background: #f7faff;
    border: 1px solid #dce8f5;
    border-radius: 8px;
    padding: 0.7rem 1rem;
    font-size: 0.76rem;
    color: #3a5a8a;
    margin-bottom: 0.8rem;
    font-family: 'DM Mono', monospace;
    line-height: 1.9;
}
.breakdown-row {
    display: flex;
    justify-content: space-between;
    border-bottom: 1px dashed #e0eaf5;
    padding: 0.1rem 0;
}
.breakdown-row:last-child { border-bottom: none; font-weight: 600; color: #0a2d6e; }
.breakdown-pos { color: #dc2626; }
.breakdown-neg { color: #16a34a; }
.breakdown-neu { color: #6a90bf; }

.flight-pill {
    background: #f0f7ff;
    border: 1px solid #bdd4f0;
    border-radius: 8px;
    padding: 0.6rem 0.9rem;
    font-size: 0.78rem;
    color: #2a4060;
    margin-bottom: 0.8rem;
    line-height: 1.8;
}
.flight-pill-title {
    font-size: 1rem;
    font-weight: 700;
    color: #0a2d6e;
    font-family: 'DM Mono', monospace;
}

/* ── Sidebar ── */
section[data-testid="stSidebar"] {
    background: #ffffff !important;
    border-right: 1px solid #dce8f5;
}
section[data-testid="stSidebar"] .block-container { padding: 1.2rem 1rem; }
.sidebar-brand {
    font-size: 1rem;
    font-weight: 700;
    color: #0a2d6e;
    letter-spacing: 0.04em;
    padding-bottom: 1rem;
    border-bottom: 2px solid #1554b0;
    margin-bottom: 1.2rem;
}

/* ── Streamlit overrides ── */
.stSelectbox label, .stMultiSelect label,
.stDateInput label, .stRadio > label {
    color: #1554b0 !important;
    font-size: 0.68rem !important;
    font-weight: 700 !important;
    text-transform: uppercase !important;
    letter-spacing: 0.1em !important;
}
.stSelectbox > div > div {
    background: #f7faff !important;
    border: 1px solid #bdd4f0 !important;
    color: #1a2740 !important;
    border-radius: 8px !important;
}
.stSelectbox > div > div:focus-within {
    border-color: #1554b0 !important;
    box-shadow: 0 0 0 2px rgba(21,84,176,0.15) !important;
}
.stRadio > div {
    flex-direction: row !important;
    gap: 1rem !important;
}
.stRadio > div > label {
    color: #2a4060 !important;
    font-size: 0.82rem !important;
    text-transform: none !important;
    letter-spacing: 0 !important;
    font-weight: 500 !important;
    background: #f7faff;
    border: 1px solid #bdd4f0;
    border-radius: 6px;
    padding: 0.3rem 0.8rem;
}
.stButton > button {
    background: linear-gradient(135deg, #0a2d6e 0%, #1554b0 100%);
    color: white;
    border: none;
    border-radius: 8px;
    font-family: 'DM Sans', sans-serif;
    font-size: 0.85rem;
    font-weight: 700;
    letter-spacing: 0.04em;
    padding: 0.55rem 1.2rem;
    width: 100%;
    box-shadow: 0 2px 8px rgba(10,45,110,0.2);
}
.stButton > button:hover {
    background: linear-gradient(135deg, #1554b0 0%, #0e7dd4 100%);
    box-shadow: 0 4px 14px rgba(10,45,110,0.3);
}
div[data-testid="metric-container"] {
    background: #ffffff !important;
    border: 1px solid #dce8f5 !important;
    border-radius: 10px;
    padding: 0.8rem 1rem;
    box-shadow: 0 1px 6px rgba(10,45,110,0.05);
}
div[data-testid="metric-container"] label {
    color: #6a90bf !important;
    font-size: 0.65rem !important;
    text-transform: uppercase;
    letter-spacing: 0.08em;
}
div[data-testid="metric-container"] [data-testid="metric-value"] {
    color: #0a2d6e !important;
    font-family: 'DM Mono', monospace !important;
    font-size: 1.5rem !important;
    font-weight: 700 !important;
}
.stDateInput > div > div > input {
    background: #f7faff !important;
    border: 1px solid #bdd4f0 !important;
    color: #1a2740 !important;
    border-radius: 8px !important;
}
div[data-testid="stExpander"] {
    background: #f7faff;
    border: 1px solid #dce8f5 !important;
    border-radius: 8px;
}
</style>
""", unsafe_allow_html=True)


# ── SHEETS ───────────────────────────────────────────────────
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


# ── PRICING LOGIC ────────────────────────────────────────────
def calculate_arithmetic_fare(route, cabin, days_to_dep, load_factor,
                               best_comp_fare, is_holiday, dep_hour,
                               passenger_type="Adult", trip_type="One Way"):
    base = BASE_FARES.get(route, 5000)

    if   days_to_dep <= 3:  adv = 0.20;  adv_lbl = "Last minute +20%"
    elif days_to_dep <= 7:  adv = 0.15;  adv_lbl = "Near date +15%"
    elif days_to_dep <= 14: adv = 0.10;  adv_lbl = "Short advance +10%"
    elif days_to_dep <= 30: adv = 0.00;  adv_lbl = "Normal window 0%"
    elif days_to_dep <= 60: adv = -0.05; adv_lbl = "Early booking −5%"
    else:                    adv = -0.10; adv_lbl = "Very early −10%"

    if   load_factor <= 0.40: lf_adj = -0.10; lf_lbl = "Low demand −10%"
    elif load_factor <= 0.70: lf_adj =  0.00; lf_lbl = "Normal demand 0%"
    elif load_factor <= 0.85: lf_adj =  0.15; lf_lbl = "High demand +15%"
    else:                      lf_adj =  0.30; lf_lbl = "Very high +30%"

    cabin_adj = {"Economy":0.0,"Premium Economy":0.50,"Business":0.80}.get(cabin,0)
    pax_adj   = PASSENGER_ADJ.get(passenger_type, 0.0)
    trip_adj  = -0.05 if trip_type == "Round Trip" else 0.0

    comp_adj = 0.0; comp_lbl = "Within range 0%"
    if best_comp_fare and best_comp_fare > 0:
        ratio = (base * (1 + cabin_adj)) / best_comp_fare
        if ratio > 1.10:   comp_adj = -0.05; comp_lbl = "We're pricier −5%"
        elif ratio < 0.90: comp_adj =  0.05; comp_lbl = "We're cheaper +5%"

    h = int(dep_hour)
    if   0  <= h <= 5:  time_adj = -0.05; time_lbl = "Red-eye −5%"
    elif 6  <= h <= 8:  time_adj =  0.12; time_lbl = "Morning peak +12%"
    elif 9  <= h <= 11: time_adj =  0.18; time_lbl = "Business peak +18%"
    elif 12 <= h <= 15: time_adj =  0.00; time_lbl = "Afternoon 0%"
    elif 16 <= h <= 20: time_adj =  0.15; time_lbl = "Evening peak +15%"
    else:                time_adj = -0.03; time_lbl = "Late night −3%"

    hol_adj = 0.15 if is_holiday else 0.0
    hol_lbl = "Festival +15%" if is_holiday else "No holiday 0%"

    total = max(-0.30, min(1.0,
        adv + lf_adj + cabin_adj + pax_adj + trip_adj + comp_adj + time_adj + hol_adj
    ))
    final = int(base * (1 + total))

    breakdown = {
        "Base Fare":        (base, ""),
        "Advance Booking":  (adv,  adv_lbl),
        "Load Factor":      (lf_adj, lf_lbl),
        "Cabin Class":      (cabin_adj, f"{cabin} +{int(cabin_adj*100)}%"),
        "Passenger Type":   (pax_adj, f"{passenger_type} {int(pax_adj*100):+}%"),
        "Trip Type":        (trip_adj, f"{trip_type} {int(trip_adj*100):+}%"),
        "Competition":      (comp_adj, comp_lbl),
        "Time Slot":        (time_adj, time_lbl),
        "Holiday":          (hol_adj, hol_lbl),
        "Total Adjustment": (total, f"{total*100:.1f}%"),
        "Final Fare":       (final, ""),
    }
    return final, breakdown


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
        history_text = "\nRecent manager outcomes:\n"
        for h in feedback_history[-3:]:
            history_text += (
                f"  • {h.get('Departure Date','')}: AI ₹{h.get('AI Suggested Fare','')}, "
                f"Manager {h.get('Manager Decision','')}, Final ₹{h.get('Final Fare Used','')}\n"
            )

    prompt = f"""You are a senior pricing analyst at IndiGo Airlines.

Flight: {flight_no} | Route: {route} | Departure: {dep_time} on {dep_date}
Cabin: {cabin} | Passenger: {passenger_type} | Trip: {trip_type}
Days to Departure: {days_to_dep} | Load Factor: {round(load_factor*100,1)}%
Arithmetic fare: ₹{arithmetic_fare:,}

Competing flights (same route, similar time slot):
{comp_text}
{strategy_text}{history_text}
Rules:
- Load >85% → no discounts
- Compare morning vs morning, evening vs evening
- One precise fare only
- Follow strategic direction strongly if set

Reply in EXACTLY this format:
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
                decision = line.replace("Decision:","").strip()
            elif line.startswith("Suggested Fare:"):
                try:
                    fare = int(line.replace("Suggested Fare:","")
                                   .replace("₹","").replace(",","").strip())
                except: pass
            elif line.startswith("Rationale:"):
                rationale = line.replace("Rationale:","").strip()
        return decision, fare, rationale
    except Exception as e:
        return "Approve", arithmetic_fare, f"Connection error: {e}"


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

PLOT_BG = dict(
    plot_bgcolor="#ffffff",
    paper_bgcolor="#ffffff",
    font_color="#2a4060",
    font_family="DM Sans",
)


# ── MAIN ─────────────────────────────────────────────────────
def main():
    try:
        comp_df, indigo_df = load_data()
        feedback_df        = load_feedback()
    except Exception as e:
        st.error(f"Could not connect to Google Sheets: {e}")
        st.info("Check Streamlit Secrets and that the sheet is shared with the service account.")
        return

    today    = pd.Timestamp.today().normalize()
    date_min = today - timedelta(days=30)
    date_max = today + timedelta(days=30)

    # ── SIDEBAR ─────────────────────────────────────────────
    with st.sidebar:
        st.markdown('<div class="sidebar-brand">✈ IndiGo · Pricing Intelligence</div>',
                    unsafe_allow_html=True)

        routes    = sorted(indigo_df["Route"].dropna().unique().tolist())
        sel_route = st.selectbox("Route", routes)

        cabins    = sorted(indigo_df["Cabin Class"].dropna().unique().tolist())
        sel_cabin = st.selectbox("Cabin Class", cabins)

        trip_type = st.radio("Trip Type", ["One Way","Round Trip"], index=0)

        pax_type  = st.selectbox(
            "Passenger Type",
            ["Adult","Corporate","Student","Senior Citizen","Child"]
        )

        date_range = st.date_input(
            "Departure Date Range",
            value=(today.date(), (today + timedelta(days=14)).date()),
            min_value=date_min.date(),
            max_value=date_max.date(),
        )
        if isinstance(date_range, (list,tuple)) and len(date_range)==2:
            d_from = pd.Timestamp(date_range[0])
            d_to   = pd.Timestamp(date_range[1])
        else:
            d_from = today
            d_to   = today + timedelta(days=14)

        st.markdown("---")
        if st.button("⟳  Refresh Data"):
            st.cache_data.clear()
            st.rerun()
        st.caption(f"Last loaded {datetime.now().strftime('%H:%M:%S')}")

    # ── FILTER ──────────────────────────────────────────────
    # Filter competitor data
    comp_f = comp_df[
        (comp_df["Route"] == sel_route) &
        (comp_df["Cabin Class"] == sel_cabin) &
        (comp_df["Departure Date"] >= d_from) &
        (comp_df["Departure Date"] <= d_to)
    ].copy()

    # Filter IndiGo data — most recent scrape per flight + departure
    indigo_f = indigo_df[
        (indigo_df["Route"] == sel_route) &
        (indigo_df["Cabin Class"] == sel_cabin) &
        (indigo_df["Departure Date"] >= d_from) &
        (indigo_df["Departure Date"] <= d_to)
    ].copy()

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

    # ── HEADER ──────────────────────────────────────────────
    avg_lf    = indigo_f["Load Factor"].mean() if not indigo_f.empty else 0
    n_flights = indigo_f["Flight No."].nunique() if not indigo_f.empty else 0
    n_fb      = len(feedback_df) if not feedback_df.empty else 0
    n_rows    = len(indigo_f)

    st.markdown(f"""
    <div class="pid-header">
        <div>
            <div class="pid-title">✈ IndiGo Pricing Intelligence Dashboard</div>
            <div class="pid-sub">Real-Time Fare Monitor · AI Recommendation Engine · ISB ALP 2026</div>
        </div>
        <div class="pid-stats">
            <div class="pid-stat-val" style="font-size:1rem;color:rgba(255,255,255,0.8)">
                {sel_route.replace(' to ',' → ')}<br>
                <span style="font-size:0.6rem;color:rgba(255,255,255,0.5);font-family:'DM Sans',sans-serif;">
                {sel_cabin} · {pax_type} · {trip_type}
                </span>
            </div>
            <div class="pid-divider"></div>
            <div>
                <div class="pid-stat-val">{round(avg_lf*100,1)}%</div>
                <div class="pid-stat-lbl">Avg Load Factor</div>
            </div>
            <div class="pid-divider"></div>
            <div>
                <div class="pid-stat-val">{n_rows}</div>
                <div class="pid-stat-lbl">Departures Shown</div>
            </div>
            <div class="pid-divider"></div>
            <div>
                <div class="pid-stat-val">{n_fb}</div>
                <div class="pid-stat-lbl">Manager Decisions</div>
            </div>
            <div class="live-pill"><div class="live-dot"></div>LIVE</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════
    # TOP ROW: AI PANEL (left) + FARE TABLE (right)
    # ══════════════════════════════════════════════════════════
    col_ai, col_table = st.columns([1, 1.6], gap="large")

    # ── AI RECOMMENDATION PANEL ─────────────────────────────
    with col_ai:
        st.markdown('<div class="sec-hd">AI Pricing Recommendation</div>',
                    unsafe_allow_html=True)

        if not indigo_f.empty:
            flight_opts = (indigo_f[["Flight No.","Departure Time","Departure Date","Days to Departure"]]
                           .drop_duplicates()
                           .sort_values("Departure Date"))

            labels = [
                f"{r['Flight No.']}  {r['Departure Time']}  —  "
                f"{pd.Timestamp(r['Departure Date']).strftime('%d %b %Y')}  "
                f"({r['Days to Departure']}d out)"
                for _, r in flight_opts.iterrows()
            ]
            sel_label = st.selectbox("Select Flight", labels)
            sel_idx   = labels.index(sel_label)
            sel_r     = flight_opts.iloc[sel_idx]
            sel_flt   = sel_r["Flight No."]
            sel_time  = sel_r["Departure Time"]
            sel_date  = sel_r["Departure Date"]
            sel_days  = int(sel_r["Days to Departure"])

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
            comp_list = [
                (cr["Airline"], str(cr["Flight No."]), str(cr["Departure Time"]), int(cr["Fare (INR)"]))
                for _, cr in comp_sel.iterrows()
            ]
            best_comp = min([c[3] for c in comp_list], default=0)

            arith_fare, breakdown = calculate_arithmetic_fare(
                sel_route, sel_cabin, sel_days, sel_lf,
                best_comp, sel_holiday=="Yes", sel_hour, pax_type, trip_type
            )

            lf_c, lf_dot = lf_cls(sel_lf)

            # Flight summary pill
            st.markdown(f"""
            <div class="flight-pill">
                <div class="flight-pill-title">{sel_flt} &nbsp;·&nbsp; {sel_time} &nbsp;·&nbsp; {sel_slot}</div>
                Load: <span class="{lf_c}">{lf_dot} {round(sel_lf*100,1)}%</span>
                &nbsp;·&nbsp; {sel_sold}/{sel_total} seats
                &nbsp;·&nbsp; {sel_days} days out
            </div>
            """, unsafe_allow_html=True)

            # Arithmetic breakdown
            st.markdown("**Arithmetic Fare Calculation**")
            bd_html = '<div class="arith-breakdown">'
            for k, (v, lbl) in breakdown.items():
                if k == "Base Fare":
                    bd_html += f'<div class="breakdown-row"><span>{k}</span><span>{inr(v)}</span></div>'
                elif k == "Final Fare":
                    bd_html += f'<div class="breakdown-row"><span><b>{k}</b></span><span><b style="color:#7c3aed">{inr(v)}</b></span></div>'
                elif k == "Total Adjustment":
                    bd_html += f'<div class="breakdown-row"><span>{k}</span><span>{lbl}</span></div>'
                else:
                    pct = float(v)
                    cls = "breakdown-pos" if pct > 0 else ("breakdown-neg" if pct < 0 else "breakdown-neu")
                    sign = "+" if pct > 0 else ""
                    bd_html += f'<div class="breakdown-row"><span style="color:#6a90bf">{lbl}</span><span class="{cls}">{sign}{round(pct*100,0):.0f}%</span></div>'
            bd_html += '</div>'
            st.markdown(bd_html, unsafe_allow_html=True)

            # Strategic direction BEFORE
            strategic = st.selectbox("Strategic Direction *(influences AI)*",
                                     STRATEGIC_OPTIONS)

            if st.button("🤖  Get AI Recommendation"):
                fb_hist = []
                if not feedback_df.empty and "Route" in feedback_df.columns:
                    fb_hist = feedback_df[
                        feedback_df["Route"] == sel_route
                    ].to_dict("records")

                with st.spinner("AI engine analysing..."):
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
                icon  = "✔ Approved" if r["decision"]=="Approve" else "⚡ Override"

                st.markdown(f"""
                <div class="ai-panel">
                    <div class="ai-header-row">
                        <div class="ai-label">AI Recommendation</div>
                        <span class="{badge}">{icon}</span>
                    </div>
                    <div class="ai-price">{inr(r['fare'])}</div>
                    <div class="ai-rationale">{r['rationale']}</div>
                </div>
                """, unsafe_allow_html=True)

                st.markdown("**Manager Decision**")

                strategic_after = st.selectbox(
                    "Revise Strategic Direction (optional)",
                    STRATEGIC_OPTIONS,
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
                            "Departure Time": r["dep_time"],
                            "Departure Date": r["dep_date"],
                            "Cabin Class": sel_cabin,
                            "Passenger Type": pax_type,
                            "Trip Type": trip_type,
                            "Days to Departure": r["days"],
                            "Load Factor": round(r["lf"]*100,1),
                            "Arithmetic Fare": r["arith_fare"],
                            "AI Decision": r["decision"],
                            "AI Suggested Fare": r["fare"],
                            "AI Rationale": r["rationale"],
                            "Manager Decision": "Accepted",
                            "Final Fare Used": r["fare"],
                            "Strategic Direction": strategic_after,
                            "Manager Notes": "",
                        })
                        st.success("Accepted ✔")
                        del st.session_state["ai_result"]
                        st.rerun()

                with c2:
                    ov = st.number_input("₹", min_value=500, max_value=300000,
                                         value=r["fare"], step=100,
                                         label_visibility="collapsed", key="ov_val")
                    if st.button("✏ Override", key="ovr"):
                        save_feedback({
                            "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
                            "Route": sel_route, "Flight No.": r["flight"],
                            "Departure Time": r["dep_time"],
                            "Departure Date": r["dep_date"],
                            "Cabin Class": sel_cabin,
                            "Passenger Type": pax_type,
                            "Trip Type": trip_type,
                            "Days to Departure": r["days"],
                            "Load Factor": round(r["lf"]*100,1),
                            "Arithmetic Fare": r["arith_fare"],
                            "AI Decision": r["decision"],
                            "AI Suggested Fare": r["fare"],
                            "AI Rationale": r["rationale"],
                            "Manager Decision": "Overridden",
                            "Final Fare Used": ov,
                            "Strategic Direction": strategic_after,
                            "Manager Notes": "",
                        })
                        st.success(f"Overridden → {inr(ov)}")
                        del st.session_state["ai_result"]
                        st.rerun()

                with c3:
                    if st.button("✕ Reject", key="rej"):
                        save_feedback({
                            "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
                            "Route": sel_route, "Flight No.": r["flight"],
                            "Departure Time": r["dep_time"],
                            "Departure Date": r["dep_date"],
                            "Cabin Class": sel_cabin,
                            "Passenger Type": pax_type,
                            "Trip Type": trip_type,
                            "Days to Departure": r["days"],
                            "Load Factor": round(r["lf"]*100,1),
                            "Arithmetic Fare": r["arith_fare"],
                            "AI Decision": r["decision"],
                            "AI Suggested Fare": r["fare"],
                            "AI Rationale": r["rationale"],
                            "Manager Decision": "Rejected",
                            "Final Fare Used": r["arith_fare"],
                            "Strategic Direction": strategic_after,
                            "Manager Notes": "",
                        })
                        st.warning("Rejected — base fare kept.")
                        del st.session_state["ai_result"]
                        st.rerun()
        else:
            st.info("No flights for selected filters.")

    # ── FARE COMPARISON TABLE ────────────────────────────────
    with col_table:
        st.markdown('<div class="sec-hd">Fare Comparison — All Airlines</div>',
                    unsafe_allow_html=True)

        if indigo_f.empty:
            st.info("No data for this selection. Try adjusting date range or filters.")
        else:
            ai_fb = pd.DataFrame()
            if not feedback_df.empty and "Manager Decision" in feedback_df.columns:
                ai_fb = feedback_df[
                    feedback_df["Manager Decision"].isin(["Accepted","Overridden"])
                ].copy()

            ai_fares_df = comp_f[comp_f["Airline"]=="Air India"][
                ["Flight No.","Departure Date","Fare (INR)","Departure Time"]
            ].rename(columns={"Fare (INR)":"AI_F","Flight No.":"AI_Flt","Departure Time":"AI_T"}) \
             if not comp_f.empty else pd.DataFrame()

            qp_fares_df = comp_f[comp_f["Airline"]=="Akasa Air"][
                ["Flight No.","Departure Date","Fare (INR)","Departure Time"]
            ].rename(columns={"Fare (INR)":"QP_F","Flight No.":"QP_Flt","Departure Time":"QP_T"}) \
             if not comp_f.empty else pd.DataFrame()

            rows = []
            for _, row in indigo_f.iterrows():
                dep_date    = row["Departure Date"]
                dep_str     = str(dep_date)[:10]
                flight_no   = str(row.get("Flight No.",""))
                dep_time    = str(row.get("Departure Time",""))
                time_slot   = str(row.get("Time Slot",""))
                days_out    = row.get("Days to Departure","")
                lf          = float(row.get("Load Factor",0))
                seats_sold  = int(row.get("Seats Sold",0))
                total_seats = int(row.get("Total Seats",180))
                holiday     = str(row.get("Holiday / Festival","No"))
                lf_c, lf_dot = lf_cls(lf)

                same_date = comp_f[comp_f["Departure Date"]==dep_date] if not comp_f.empty else pd.DataFrame()
                best_comp = int(same_date["Fare (INR)"].min()) if not same_date.empty else 0

                arith, _ = calculate_arithmetic_fare(
                    sel_route, sel_cabin,
                    int(days_out) if days_out else 30,
                    lf, best_comp, holiday=="Yes",
                    dep_hour(dep_time), pax_type, trip_type
                )

                # AI rec lookup
                ai_rec = None
                if not ai_fb.empty and "Flight No." in ai_fb.columns:
                    match = ai_fb[
                        (ai_fb["Flight No."].astype(str)==flight_no) &
                        (ai_fb["Departure Date"].astype(str).str[:10]==dep_str)
                    ]
                    if not match.empty:
                        try: ai_rec = int(match.iloc[-1]["Final Fare Used"])
                        except: pass

                ai_f, ai_flt, ai_t = "—","—","—"
                qp_f, qp_flt, qp_t = "—","—","—"

                if not ai_fares_df.empty:
                    m = ai_fares_df[ai_fares_df["Departure Date"]==dep_date]
                    if not m.empty:
                        ai_f   = int(m.iloc[0]["AI_F"])
                        ai_flt = str(m.iloc[0]["AI_Flt"])
                        ai_t   = str(m.iloc[0]["AI_T"])
                if not qp_fares_df.empty:
                    m = qp_fares_df[qp_fares_df["Departure Date"]==dep_date]
                    if not m.empty:
                        qp_f   = int(m.iloc[0]["QP_F"])
                        qp_flt = str(m.iloc[0]["QP_Flt"])
                        qp_t   = str(m.iloc[0]["QP_T"])

                rows.append({
                    "date": dep_date.strftime("%d %b %Y") if hasattr(dep_date,"strftime") else dep_str,
                    "days": days_out, "flt": f"{flight_no} {dep_time}",
                    "slot": time_slot, "lf": lf, "lf_c": lf_c, "lf_dot": lf_dot,
                    "seats": f"{seats_sold}/{total_seats}",
                    "base": BASE_FARES.get(sel_route,5000),
                    "arith": arith, "ai_rec": ai_rec,
                    "ai_flt": f"{ai_flt} {ai_t}", "ai_f": ai_f,
                    "qp_flt": f"{qp_flt} {qp_t}", "qp_f": qp_f,
                })

            if rows:
                def comp_style(v, base):
                    try:
                        v = int(v)
                        if v < base*0.97: return "f-cheaper"
                        if v > base*1.03: return "f-pricier"
                        return "f-similar"
                    except: return ""

                html = """<table class="fare-tbl"><thead><tr>
                <th>Departure</th><th>Days</th><th>IndiGo Flight</th><th>Slot</th>
                <th>Load Factor</th><th>Seats</th>
                <th>Base ₹</th><th>Arithmetic ₹</th><th>AI Rec ₹</th>
                <th>Air India</th><th>Air India ₹</th>
                <th>Akasa</th><th>Akasa ₹</th>
                </tr></thead><tbody>"""

                for r in rows:
                    html += f"""<tr>
                    <td>{r['date']}</td><td>{r['days']}</td>
                    <td class="f-indigo">{r['flt']}</td>
                    <td style="color:#6a90bf;font-size:0.72rem">{r['slot']}</td>
                    <td><span class="{r['lf_c']}">{r['lf_dot']} {round(r['lf']*100,1)}%</span></td>
                    <td style="color:#6a90bf">{r['seats']}</td>
                    <td class="f-indigo">{inr(r['base'])}</td>
                    <td class="f-arith">{inr(r['arith'])}</td>
                    <td class="f-airec">{inr(r['ai_rec']) if r['ai_rec'] else '—'}</td>
                    <td style="color:#6a90bf;font-size:0.7rem">{r['ai_flt']}</td>
                    <td><span class="{comp_style(r['ai_f'],r['base'])}">{inr(r['ai_f'])}</span></td>
                    <td style="color:#6a90bf;font-size:0.7rem">{r['qp_flt']}</td>
                    <td><span class="{comp_style(r['qp_f'],r['base'])}">{inr(r['qp_f'])}</span></td>
                    </tr>"""
                html += "</tbody></table>"
                st.markdown(html, unsafe_allow_html=True)
                st.markdown("""
                <div style="font-size:0.66rem;color:#6a90bf;margin-top:0.5rem;">
                <span style="color:#1554b0">■</span> Base &nbsp;
                <span style="color:#7c3aed">■</span> Arithmetic (your filters) &nbsp;
                <span style="color:#0891b2">■</span> AI Rec (accepted) &nbsp;
                <span style="color:#16a34a">■</span> Competitor cheaper &nbsp;
                <span style="color:#dc2626">■</span> Competitor pricier
                </div>""", unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════
    # CHARTS ROW
    # ══════════════════════════════════════════════════════════
    ch1, ch2, ch3 = st.columns(3, gap="medium")

    # ── Load Factor by Flight ────────────────────────────────
    with ch1:
        st.markdown('<div class="sec-hd">Load Factor by Flight</div>', unsafe_allow_html=True)
        if not indigo_f.empty:
            lf_agg = (indigo_f
                      .groupby(["Flight No.","Departure Time"])["Load Factor"]
                      .mean().reset_index())
            lf_agg["LF%"]   = (lf_agg["Load Factor"]*100).round(1)
            lf_agg["Label"] = lf_agg["Flight No."].astype(str) + "\n" + lf_agg["Departure Time"].astype(str)
            lf_agg["Color"] = lf_agg["Load Factor"].apply(
                lambda x: "#16a34a" if x<=0.70 else ("#d97706" if x<=0.85 else "#dc2626"))

            fig = go.Figure(go.Bar(
                x=lf_agg["Label"], y=lf_agg["LF%"],
                marker_color=lf_agg["Color"].tolist(),
                text=lf_agg["LF%"].apply(lambda x: f"{x}%"),
                textposition="outside",
                textfont=dict(size=11),
            ))
            fig.add_hline(y=70, line_dash="dot", line_color="#d97706", line_width=1)
            fig.add_hline(y=85, line_dash="dot", line_color="#dc2626", line_width=1)
            fig.update_layout(
                plot_bgcolor="#ffffff", paper_bgcolor="#ffffff",
                font=dict(color="#2a4060", family="DM Sans", size=11),
                margin=dict(l=8, r=8, t=8, b=8),
                xaxis=dict(gridcolor="#f0f4f9", linecolor="#dce8f5"),
                yaxis=dict(gridcolor="#f0f4f9", range=[0,115],
                           title="Load %", linecolor="#dce8f5"),
                height=240, showlegend=False,
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No data")

    # ── Load Factor Over Time ────────────────────────────────
    with ch2:
        st.markdown('<div class="sec-hd">Load Factor Over Time</div>', unsafe_allow_html=True)
        dc = "Date" if "Date" in indigo_df.columns else "Scrape Date"
        lf_time = indigo_df[
            (indigo_df["Route"]==sel_route) &
            (indigo_df["Cabin Class"]==sel_cabin) &
            (indigo_df[dc]>=date_min) &
            (indigo_df["Departure Date"]>=d_from) &
            (indigo_df["Departure Date"]<=d_to)
        ].copy()

        if not lf_time.empty and dc in lf_time.columns:
            lf_time["Load Factor"] = pd.to_numeric(lf_time["Load Factor"], errors="coerce")
            lf_time["LF%"]  = (lf_time["Load Factor"]*100).round(1)
            lf_time["Flt"]  = lf_time["Flight No."].astype(str) + " " + lf_time["Departure Time"].astype(str)
            lf_pivot = lf_time.groupby([dc,"Flt"])["LF%"].mean().reset_index()

            fig2 = px.line(lf_pivot, x=dc, y="LF%", color="Flt", markers=True,
                           color_discrete_sequence=["#1554b0","#16a34a","#d97706",
                                                     "#dc2626","#7c3aed","#0891b2"])
            fig2.add_hline(y=85, line_dash="dot", line_color="#dc2626", line_width=1)
            fig2.update_layout(
                plot_bgcolor="#ffffff", paper_bgcolor="#ffffff",
                font=dict(color="#2a4060", family="DM Sans", size=11),
                margin=dict(l=8, r=8, t=8, b=8),
                xaxis=dict(gridcolor="#f0f4f9", linecolor="#dce8f5", title=""),
                yaxis=dict(gridcolor="#f0f4f9", title="Load %",
                           range=[0,105], linecolor="#dce8f5"),
                legend=dict(bgcolor="#ffffff", bordercolor="#dce8f5",
                            font=dict(size=9)),
                height=240,
            )
            st.plotly_chart(fig2, use_container_width=True)
        else:
            st.info("No load factor history for selected dates.")

    # ── Competitor Price Trend ───────────────────────────────
    with ch3:
        st.markdown('<div class="sec-hd">Competitor Price Trend — 30 Days</div>',
                    unsafe_allow_html=True)
        if not comp_df.empty and "Scrape Date" in comp_df.columns:
            trend = comp_df[
                (comp_df["Route"]==sel_route) &
                (comp_df["Cabin Class"]==sel_cabin) &
                (comp_df["Scrape Date"]>=date_min)
            ].groupby(["Scrape Date","Airline"])["Fare (INR)"].mean().reset_index()

            if not trend.empty:
                fig3 = px.line(trend, x="Scrape Date", y="Fare (INR)",
                               color="Airline", markers=True,
                               color_discrete_map={
                                   "Air India":"#1554b0",
                                   "Akasa Air":"#dc2626"
                               })
                fig3.update_layout(
                    plot_bgcolor="#ffffff", paper_bgcolor="#ffffff",
                    font=dict(color="#2a4060", family="DM Sans", size=11),
                    margin=dict(l=8, r=8, t=8, b=8),
                    xaxis=dict(gridcolor="#f0f4f9", linecolor="#dce8f5", title=""),
                    yaxis=dict(gridcolor="#f0f4f9", title="Avg Fare ₹",
                               linecolor="#dce8f5"),
                    legend=dict(bgcolor="#ffffff", bordercolor="#dce8f5"),
                    height=240,
                )
                st.plotly_chart(fig3, use_container_width=True)
            else:
                st.info("No trend data.")
        else:
            st.info("No competitor data.")

    st.markdown("<br>", unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════
    # PROFITABILITY
    # ══════════════════════════════════════════════════════════
    st.markdown('<div class="sec-hd">Profitability — Accepted AI Recommendations</div>',
                unsafe_allow_html=True)

    if not feedback_df.empty and "Manager Decision" in feedback_df.columns:
        acc = feedback_df[feedback_df["Manager Decision"].isin(["Accepted","Overridden"])].copy()
        if not acc.empty:
            acc["Final Fare Used"]   = pd.to_numeric(acc["Final Fare Used"], errors="coerce")
            acc["Cost Per Seat"]     = acc["Route"].map(COST_PER_SEAT).fillna(3000)
            acc["Profit Per Seat"]   = acc["Final Fare Used"] - acc["Cost Per Seat"]
            acc["Load Factor Num"]   = pd.to_numeric(acc["Load Factor"], errors="coerce") / 100
            acc["Total Seats"]       = acc["Route"].map(TOTAL_SEATS_MAP).fillna(180)
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
            fig4 = go.Figure(go.Bar(
                x=rp["Est Flight Profit"], y=rp["Route"], orientation="h",
                marker_color=["#16a34a" if x>0 else "#dc2626" for x in rp["Est Flight Profit"]],
                text=[inr(x) for x in rp["Est Flight Profit"]],
                textposition="outside",
                textfont=dict(size=11),
            ))
            fig4.update_layout(
                plot_bgcolor="#ffffff", paper_bgcolor="#ffffff",
                font=dict(color="#2a4060", family="DM Sans", size=11),
                margin=dict(l=8, r=80, t=8, b=8),
                xaxis=dict(gridcolor="#f0f4f9", title="Estimated Profit (₹)", linecolor="#dce8f5"),
                yaxis=dict(gridcolor="#f0f4f9", linecolor="#dce8f5"),
                height=220,
            )
            st.plotly_chart(fig4, use_container_width=True)

            show = [c for c in [
                "Route","Flight No.","Departure Date","Cabin Class",
                "Passenger Type","Trip Type","Load Factor",
                "Arithmetic Fare","AI Suggested Fare","Final Fare Used",
                "Manager Decision","Profit Per Seat","Est Flight Profit"
            ] if c in acc.columns]
            st.dataframe(acc[show].sort_values("Departure Date", ascending=False),
                         use_container_width=True, hide_index=True)
        else:
            st.info("No accepted recommendations yet — use the AI panel to get started.")
    else:
        st.info("No feedback data yet. Accept or override a recommendation above.")

    # Footer
    st.markdown("""
    <div style="margin-top:2rem;padding:0.8rem 0;border-top:1px solid #dce8f5;
                text-align:center;font-size:0.68rem;color:#6a90bf;letter-spacing:0.08em;">
        IndiGo Pricing Intelligence &nbsp;·&nbsp; Team 5 ISB ALP 2026
        &nbsp;·&nbsp; Powered by Gemini AI &nbsp;·&nbsp; Confidential
    </div>
    """, unsafe_allow_html=True)


if __name__ == "__main__":
    main()
