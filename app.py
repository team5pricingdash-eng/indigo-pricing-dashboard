"""
IndiGo Pricing Intelligence Dashboard
Team 5 — ISB Action Learning Project 2026

HOW TO RUN:
1. Install: pip install streamlit gspread google-auth google-auth-oauthlib pandas plotly requests
2. Set up credentials (see README below)
3. Run: streamlit run app.py

GOOGLE SHEETS CREDENTIALS:
- Go to console.cloud.google.com
- Create a Service Account → download JSON key
- Share your Google Sheet with the service account email
- Save the JSON file as 'credentials.json' in the same folder as this file
"""

import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import json
from datetime import datetime, timedelta, date

# ============================================================
# !! FILL THESE IN !!
# ============================================================
GOOGLE_SHEET_NAME = "Pricing Intelligence"
GEMINI_API_KEY    = "PASTE_YOUR_GEMINI_API_KEY_HERE"
# ============================================================

COMPETITOR_TAB   = "Competitor Prices"
INDIGO_OPS_TAB   = "IndiGo Operations"
FEEDBACK_TAB     = "Feedback"

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
    page_title="IndiGo Pricing Intelligence",
    page_icon="✈️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ── STYLES ───────────────────────────────────────────────────
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;600&display=swap');

    /* Base */
    html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
    .main { background: #0a0e1a; }
    .block-container { padding: 1.5rem 2rem; max-width: 100%; }

    /* Header */
    .dash-header {
        background: linear-gradient(135deg, #0f1829 0%, #1a2744 100%);
        border: 1px solid #1e3a5f;
        border-radius: 12px;
        padding: 1.5rem 2rem;
        margin-bottom: 1.5rem;
        display: flex;
        align-items: center;
        justify-content: space-between;
    }
    .dash-title {
        font-size: 1.6rem;
        font-weight: 700;
        color: #ffffff;
        letter-spacing: -0.02em;
    }
    .dash-subtitle {
        font-size: 0.8rem;
        color: #6b8cba;
        margin-top: 0.2rem;
        font-weight: 400;
    }
    .live-badge {
        background: #0d2e1a;
        border: 1px solid #1a6b3a;
        color: #2ecc71;
        padding: 0.3rem 0.8rem;
        border-radius: 20px;
        font-size: 0.72rem;
        font-weight: 600;
        letter-spacing: 0.05em;
        font-family: 'JetBrains Mono', monospace;
    }

    /* Section headers */
    .section-label {
        font-size: 0.68rem;
        font-weight: 700;
        letter-spacing: 0.12em;
        text-transform: uppercase;
        color: #4a7ab5;
        margin-bottom: 0.8rem;
        margin-top: 0.2rem;
        padding-left: 0.5rem;
        border-left: 2px solid #1e6bb8;
    }

    /* KPI cards */
    .kpi-row { display: flex; gap: 1rem; margin-bottom: 1.5rem; }
    .kpi-card {
        flex: 1;
        background: #0f1829;
        border: 1px solid #1e3a5f;
        border-radius: 10px;
        padding: 1rem 1.2rem;
    }
    .kpi-label { font-size: 0.7rem; color: #6b8cba; font-weight: 500; text-transform: uppercase; letter-spacing: 0.08em; }
    .kpi-value { font-size: 1.6rem; font-weight: 700; color: #ffffff; font-family: 'JetBrains Mono', monospace; margin-top: 0.2rem; }
    .kpi-delta { font-size: 0.75rem; margin-top: 0.2rem; font-weight: 500; }
    .kpi-up   { color: #2ecc71; }
    .kpi-down { color: #e74c3c; }
    .kpi-neu  { color: #6b8cba; }

    /* Fare table */
    .fare-table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
    .fare-table th {
        background: #0f1829;
        color: #4a7ab5;
        font-weight: 600;
        font-size: 0.68rem;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        padding: 0.6rem 0.8rem;
        border-bottom: 1px solid #1e3a5f;
        text-align: left;
    }
    .fare-table td {
        padding: 0.55rem 0.8rem;
        border-bottom: 1px solid #0f1829;
        color: #c8d8e8;
        font-family: 'JetBrains Mono', monospace;
        font-size: 0.8rem;
    }
    .fare-table tr:hover td { background: #0f1829; }
    .cell-cheapest { color: #2ecc71 !important; font-weight: 600; }
    .cell-expensive { color: #e74c3c !important; }
    .cell-mid { color: #f39c12 !important; }
    .lf-green { color: #2ecc71; font-weight: 600; }
    .lf-amber { color: #f39c12; font-weight: 600; }
    .lf-red   { color: #e74c3c; font-weight: 600; }

    /* AI recommendation panel */
    .ai-panel {
        background: #0a1628;
        border: 1px solid #1e3a5f;
        border-radius: 10px;
        padding: 1.2rem 1.4rem;
        margin-top: 1rem;
    }
    .ai-approve {
        background: #0d2e1a;
        border: 1px solid #1a6b3a;
        color: #2ecc71;
        padding: 0.4rem 1rem;
        border-radius: 6px;
        font-size: 0.8rem;
        font-weight: 700;
        display: inline-block;
        margin-bottom: 0.8rem;
    }
    .ai-override {
        background: #2e1a0d;
        border: 1px solid #6b3a1a;
        color: #f39c12;
        padding: 0.4rem 1rem;
        border-radius: 6px;
        font-size: 0.8rem;
        font-weight: 700;
        display: inline-block;
        margin-bottom: 0.8rem;
    }
    .ai-fare {
        font-size: 2rem;
        font-weight: 700;
        color: #ffffff;
        font-family: 'JetBrains Mono', monospace;
    }
    .ai-rationale {
        font-size: 0.82rem;
        color: #8ba8c8;
        line-height: 1.6;
        margin-top: 0.6rem;
        border-left: 2px solid #1e6bb8;
        padding-left: 0.8rem;
    }

    /* Profit cells */
    .profit-pos { color: #2ecc71; font-weight: 600; }
    .profit-neg { color: #e74c3c; font-weight: 600; }

    /* Sidebar */
    .css-1d391kg { background: #0a0e1a; }
    section[data-testid="stSidebar"] { background: #0a0e1a; border-right: 1px solid #1e3a5f; }
    section[data-testid="stSidebar"] .block-container { padding: 1rem; }

    /* Streamlit overrides */
    .stSelectbox label, .stMultiSelect label, .stDateInput label { color: #6b8cba !important; font-size: 0.75rem !important; font-weight: 500 !important; text-transform: uppercase; letter-spacing: 0.06em; }
    .stButton > button {
        background: #1e4a8f;
        color: white;
        border: none;
        border-radius: 8px;
        font-weight: 600;
        font-size: 0.82rem;
        padding: 0.5rem 1.2rem;
        width: 100%;
        transition: background 0.2s;
    }
    .stButton > button:hover { background: #2560b5; }
    div[data-testid="metric-container"] {
        background: #0f1829;
        border: 1px solid #1e3a5f;
        border-radius: 10px;
        padding: 0.8rem 1rem;
    }
    div[data-testid="metric-container"] label { color: #6b8cba !important; }
    div[data-testid="metric-container"] div { color: #ffffff !important; }
    .stTabs [data-baseweb="tab"] { color: #6b8cba; font-size: 0.8rem; font-weight: 500; }
    .stTabs [aria-selected="true"] { color: #ffffff !important; }
</style>
""", unsafe_allow_html=True)


# ── GOOGLE SHEETS CONNECTION ─────────────────────────────────

@st.cache_resource
def get_sheet_client():
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"],
        scopes=scope
    )
    client = gspread.authorize(creds)
    return client.open(GOOGLE_SHEET_NAME)

@st.cache_data(ttl=300)  # refresh every 5 minutes
def load_data():
    sheet    = get_sheet_client()
    comp_df  = pd.DataFrame(sheet.worksheet(COMPETITOR_TAB).get_all_records())
    indigo_df = pd.DataFrame(sheet.worksheet(INDIGO_OPS_TAB).get_all_records())

    # Parse dates
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
        sheet = get_sheet_client()
        ws    = sheet.worksheet(FEEDBACK_TAB)
        data  = ws.get_all_records()
        return pd.DataFrame(data) if data else pd.DataFrame()
    except:
        return pd.DataFrame()

def save_feedback(row_dict):
    sheet = get_sheet_client()
    try:
        ws = sheet.worksheet(FEEDBACK_TAB)
    except:
        ws = sheet.add_worksheet(FEEDBACK_TAB, rows=1000, cols=20)
        ws.append_row([
            "Timestamp", "Route", "Flight No.", "Departure Time",
            "Departure Date", "Cabin Class", "Days to Departure",
            "Load Factor", "Arithmetic Fare", "AI Decision",
            "AI Suggested Fare", "AI Rationale",
            "Manager Decision", "Final Fare Used",
            "Strategic Direction", "Manager Notes"
        ])
    ws.append_row(list(row_dict.values()))


# ── PRICING LOGIC ────────────────────────────────────────────

def calculate_arithmetic_fare(route, cabin, days_to_dep, load_factor,
                               best_comp_fare, is_holiday, dep_hour):
    base = BASE_FARES.get(route, 5000)

    # Advance booking
    if days_to_dep <= 3:    adv = 0.20
    elif days_to_dep <= 7:  adv = 0.15
    elif days_to_dep <= 14: adv = 0.10
    elif days_to_dep <= 30: adv = 0.00
    elif days_to_dep <= 60: adv = -0.05
    else:                    adv = -0.10

    # Load factor
    if load_factor <= 0.40:   lf_adj = -0.10
    elif load_factor <= 0.70: lf_adj = 0.00
    elif load_factor <= 0.85: lf_adj = 0.15
    else:                      lf_adj = 0.30

    # Cabin
    cabin_adj = {"Economy": 0.0, "Premium Economy": 0.50, "Business": 0.80}.get(cabin, 0)

    # Competition
    comp_adj = 0.0
    if best_comp_fare and best_comp_fare > 0:
        ratio = (base * (1 + cabin_adj)) / best_comp_fare
        if ratio > 1.10:   comp_adj = -0.05
        elif ratio < 0.90: comp_adj = 0.05

    # Time slot
    h = int(dep_hour)
    if 0  <= h <= 5:  time_adj = -0.05
    elif 6 <= h <= 8: time_adj = 0.12
    elif 9 <= h <= 11: time_adj = 0.18
    elif 12 <= h <= 15: time_adj = 0.00
    elif 16 <= h <= 20: time_adj = 0.15
    else:               time_adj = -0.03

    # Holiday
    hol_adj = 0.15 if is_holiday else 0.0

    total = max(-0.30, min(1.0, adv + lf_adj + cabin_adj + comp_adj + time_adj + hol_adj))
    return int(base * (1 + total)), {
        "Advance Booking": f"{adv*100:+.0f}%",
        "Load Factor":     f"{lf_adj*100:+.0f}%",
        "Cabin Class":     f"{cabin_adj*100:+.0f}%",
        "Competition":     f"{comp_adj*100:+.0f}%",
        "Time Slot":       f"{time_adj*100:+.0f}%",
        "Holiday":         f"{hol_adj*100:+.0f}%",
        "Total":           f"{total*100:.1f}%",
    }


# ── GEMINI API ───────────────────────────────────────────────

def call_gemini(route, flight_no, dep_time, cabin, dep_date,
                days_to_dep, load_factor, arithmetic_fare,
                comp_fares, strategic_direction, feedback_history):

    comp_text = "\n".join([
        f"  {airline} ({ft} {ft_time}): ₹{fare:,}"
        for airline, ft, ft_time, fare in comp_fares
    ]) or "  No competitor data available"

    strategy_text = ""
    if strategic_direction and "None" not in strategic_direction:
        strategy_text = f"\n⚡ STRATEGIC DIRECTION FROM PRICING MANAGER: {strategic_direction}\nThis must heavily influence your recommendation.\n"

    history_text = ""
    if feedback_history:
        history_text = "\nRecent manager-validated outcomes on this route:\n"
        for h in feedback_history[-3:]:
            history_text += (
                f"  • {h.get('Departure Date','')}: AI suggested ₹{h.get('AI Suggested Fare','')}, "
                f"Manager {h.get('Manager Decision','')}, "
                f"Final fare ₹{h.get('Final Fare Used','')}\n"
            )

    prompt = f"""You are a senior pricing analyst at IndiGo Airlines.

Flight: {flight_no} | Route: {route} | Departure: {dep_time} on {dep_date}
Cabin: {cabin} | Days to Departure: {days_to_dep}
Current Load Factor: {round(load_factor * 100, 1)}%

Our arithmetic engine calculated: ₹{arithmetic_fare:,}

Competing flights on same route (similar time slots):
{comp_text}
{strategy_text}{history_text}
Rules:
- If load factor > 85%, do not recommend any discounts
- Match competitor time slots when comparing (morning vs morning)
- Be specific — one precise fare, not a range
- If strategic direction is set, follow it strongly

Reply in EXACTLY this format:
Decision: Approve OR Override
Suggested Fare: ₹[number]
Rationale: [2-3 plain English sentences explaining the recommendation]"""

    url     = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent?key={GEMINI_API_KEY}"
    payload = {"contents": [{"parts": [{"text": prompt}]}]}

    try:
        resp = requests.post(url, json=payload, timeout=30)
        if resp.status_code != 200:
            return "Approve", arithmetic_fare, f"API error {resp.status_code} — arithmetic fare used."
        text = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        decision, fare, rationale = "Approve", arithmetic_fare, ""
        for line in text.split("\n"):
            if line.startswith("Decision:"):
                decision = line.replace("Decision:", "").strip()
            elif line.startswith("Suggested Fare:"):
                try:
                    fare = int(line.replace("Suggested Fare:", "")
                                   .replace("₹", "").replace(",", "").strip())
                except:
                    pass
            elif line.startswith("Rationale:"):
                rationale = line.replace("Rationale:", "").strip()
        return decision, fare, rationale
    except Exception as e:
        return "Approve", arithmetic_fare, f"Connection error — arithmetic fare used. ({e})"


# ── HELPERS ──────────────────────────────────────────────────

def lf_color(lf):
    if lf <= 0.70: return "lf-green", "🟢"
    if lf <= 0.85: return "lf-amber", "🟡"
    return "lf-red", "🔴"

def dep_hour_from_time(time_str):
    try: return int(str(time_str).split(":")[0])
    except: return 10

def format_inr(val):
    try: return f"₹{int(val):,}"
    except: return "—"


# ── MAIN APP ─────────────────────────────────────────────────

def main():
    # Header
    st.markdown("""
    <div class="dash-header">
        <div>
            <div class="dash-title">✈️ IndiGo Pricing Intelligence</div>
            <div class="dash-subtitle">Real-Time Competitive Fare Monitor · AI Recommendation Engine · ISB ALP 2026</div>
        </div>
        <div class="live-badge">● LIVE</div>
    </div>
    """, unsafe_allow_html=True)

    # Load data
    with st.spinner("Loading pricing data..."):
        try:
            comp_df, indigo_df = load_data()
            feedback_df        = load_feedback()
        except Exception as e:
            st.error(f"Could not connect to Google Sheets: {e}")
            st.info("Make sure credentials.json is in the same folder as app.py and the sheet is shared with the service account.")
            return

    today      = pd.Timestamp.today().normalize()
    date_min   = today - timedelta(days=30)
    date_max   = today + timedelta(days=30)

    # ── SIDEBAR SLICERS ─────────────────────────────────────
    with st.sidebar:
        st.markdown("### 🎛️ Filters")

        routes_available = sorted(indigo_df["Route"].dropna().unique().tolist())
        selected_route   = st.selectbox("Route", routes_available)

        cabins_available = sorted(indigo_df["Cabin Class"].dropna().unique().tolist())
        selected_cabin   = st.selectbox("Cabin Class", cabins_available)

        date_range = st.date_input(
            "Date Range (Departure)",
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
        st.markdown("### 🔄 Data")
        if st.button("Refresh Data"):
            st.cache_data.clear()
            st.rerun()
        st.caption(f"Last loaded: {datetime.now().strftime('%H:%M:%S')}")

    # ── FILTER DATA ─────────────────────────────────────────
    comp_f   = comp_df[
        (comp_df["Route"] == selected_route) &
        (comp_df["Cabin Class"] == selected_cabin) &
        (comp_df["Departure Date"] >= d_from) &
        (comp_df["Departure Date"] <= d_to)
    ].copy()

    indigo_f = indigo_df[
        (indigo_df["Route"] == selected_route) &
        (indigo_df["Cabin Class"] == selected_cabin) &
        (indigo_df["Departure Date"] >= d_from) &
        (indigo_df["Departure Date"] <= d_to)
    ].copy()

    # Use most recent scrape per flight + departure date
    if not comp_f.empty and "Scrape Date" in comp_f.columns:
        comp_f = (comp_f.sort_values("Scrape Date")
                        .groupby(["Airline", "Flight No.", "Departure Date"], as_index=False)
                        .last())
    if not indigo_f.empty:
        date_col = "Date" if "Date" in indigo_f.columns else "Scrape Date"
        if date_col in indigo_f.columns:
            indigo_f = (indigo_f.sort_values(date_col)
                                .groupby(["Flight No.", "Departure Date"], as_index=False)
                                .last())

    # ── KPI SUMMARY ROW ─────────────────────────────────────
    st.markdown('<div class="section-label">Summary</div>', unsafe_allow_html=True)
    k1, k2, k3, k4 = st.columns(4)

    avg_indigo_fare = indigo_f["Load Factor"].mean() if not indigo_f.empty else 0
    cost_seat       = COST_PER_SEAT.get(selected_route, 3000)
    base_fare       = BASE_FARES.get(selected_route, 5000)

    with k1:
        st.metric("Route", selected_route.replace(" to ", " → "))
    with k2:
        avg_lf = indigo_f["Load Factor"].mean() if not indigo_f.empty else 0
        st.metric("Avg Load Factor", f"{avg_lf*100:.1f}%",
                  delta="High demand" if avg_lf > 0.75 else "Normal")
    with k3:
        n_flights = indigo_f["Flight No."].nunique() if not indigo_f.empty else 0
        st.metric("IndiGo Flights Tracked", n_flights)
    with k4:
        if not feedback_df.empty and "Final Fare Used" in feedback_df.columns:
            accepted = feedback_df[
                (feedback_df.get("Route", "") == selected_route) &
                (feedback_df.get("Manager Decision", "") == "Accepted")
            ]
            if not accepted.empty:
                try:
                    rev_uplift = (pd.to_numeric(accepted["Final Fare Used"], errors="coerce") - base_fare).sum()
                    st.metric("Revenue Uplift (Accepted Recs)", format_inr(rev_uplift))
                except:
                    st.metric("Revenue Uplift", "—")
            else:
                st.metric("Revenue Uplift", "No data yet")
        else:
            st.metric("Feedback Records", "0")

    st.markdown("<br>", unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════
    # SECTION 1 — FARE COMPARISON TABLE
    # ══════════════════════════════════════════════════════════
    st.markdown('<div class="section-label">Fare Comparison — All Airlines</div>', unsafe_allow_html=True)

    if indigo_f.empty and comp_f.empty:
        st.info("No data for this selection. Try adjusting the filters.")
    else:
        # Build pivot: rows = departure date + time slot, cols = airline fares
        indigo_fares = indigo_f[["Flight No.", "Departure Time", "Time Slot",
                                  "Departure Date", "Days to Departure"]].copy()
        indigo_fares["IndiGo Fare"] = BASE_FARES.get(selected_route, 5000)

        if not comp_f.empty:
            ai_fares  = comp_f[comp_f["Airline"] == "Air India"][
                ["Flight No.", "Departure Date", "Fare (INR)", "Departure Time"]
            ].rename(columns={"Fare (INR)": "Air India Fare", "Flight No.": "AI Flight", "Departure Time": "AI Time"})

            qp_fares  = comp_f[comp_f["Airline"] == "Akasa Air"][
                ["Flight No.", "Departure Date", "Fare (INR)", "Departure Time"]
            ].rename(columns={"Fare (INR)": "Akasa Fare", "Flight No.": "QP Flight", "Departure Time": "QP Time"})
        else:
            ai_fares = pd.DataFrame()
            qp_fares = pd.DataFrame()

        # Build display table
        rows = []
        for _, row in indigo_f.iterrows():
            dep_date   = row["Departure Date"]
            flight_no  = row.get("Flight No.", "")
            dep_time   = row.get("Departure Time", "")
            time_slot  = row.get("Time Slot", "")
            days_to_dep = row.get("Days to Departure", "")
            lf          = float(row.get("Load Factor", 0))
            seats_sold  = int(row.get("Seats Sold", 0))
            total_seats = int(row.get("Total Seats", 180))
            lf_class, lf_icon = lf_color(lf)

            indigo_base = BASE_FARES.get(selected_route, 5000)

            # Match competitor flights by closest time slot
            ai_fare, ai_flight, ai_time_val = "—", "—", "—"
            qp_fare_val, qp_flight, qp_time_val = "—", "—", "—"

            if not ai_fares.empty:
                ai_match = ai_fares[ai_fares["Departure Date"] == dep_date]
                if not ai_match.empty:
                    ai_fare    = int(ai_match.iloc[0]["Air India Fare"])
                    ai_flight  = ai_match.iloc[0]["AI Flight"]
                    ai_time_val = ai_match.iloc[0]["AI Time"]

            if not qp_fares.empty:
                qp_match = qp_fares[qp_fares["Departure Date"] == dep_date]
                if not qp_match.empty:
                    qp_fare_val = int(qp_match.iloc[0]["Akasa Fare"])
                    qp_flight   = qp_match.iloc[0]["QP Flight"]
                    qp_time_val = qp_match.iloc[0]["QP Time"]

            rows.append({
                "Departure Date": dep_date.strftime("%d %b %Y") if hasattr(dep_date, "strftime") else str(dep_date),
                "Days Out":       days_to_dep,
                "IndiGo Flight":  f"{flight_no} {dep_time}",
                "Time Slot":      time_slot,
                "Load Factor":    lf,
                "Seats":          f"{seats_sold}/{total_seats}",
                "IndiGo ₹":       indigo_base,
                "Air India Flight": f"{ai_flight} {ai_time_val}",
                "Air India ₹":    ai_fare,
                "Akasa Flight":   f"{qp_flight} {qp_time_val}",
                "Akasa ₹":        qp_fare_val,
                "_lf_class":      lf_class,
                "_lf_icon":       lf_icon,
            })

        if rows:
            # Render as HTML table for full control
            html = '<table class="fare-table"><thead><tr>'
            cols = ["Departure Date", "Days Out", "IndiGo Flight", "Time Slot",
                    "Load Factor", "Seats",
                    "IndiGo ₹", "Air India Flight", "Air India ₹",
                    "Akasa Flight", "Akasa ₹"]
            for c in cols:
                html += f"<th>{c}</th>"
            html += "</tr></thead><tbody>"

            for r in rows:
                html += "<tr>"
                for c in cols:
                    val = r[c]
                    if c == "Load Factor":
                        html += f'<td><span class="{r["_lf_class"]}">{r["_lf_icon"]} {round(val*100, 1)}%</span></td>'
                    elif c == "IndiGo ₹":
                        html += f"<td>{format_inr(val)}</td>"
                    elif c in ("Air India ₹", "Akasa ₹"):
                        # Colour vs IndiGo
                        indigo_fare = r["IndiGo ₹"]
                        try:
                            v = int(val)
                            if v < indigo_fare:
                                css = "cell-cheapest"
                            elif v > indigo_fare * 1.05:
                                css = "cell-expensive"
                            else:
                                css = "cell-mid"
                            html += f'<td><span class="{css}">{format_inr(v)}</span></td>'
                        except:
                            html += f"<td>{val}</td>"
                    else:
                        html += f"<td>{val}</td>"
                html += "</tr>"
            html += "</tbody></table>"
            st.markdown(html, unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════
    # SECTION 2 + 3 — LOAD FACTOR CHART & AI RECOMMENDATION
    # ══════════════════════════════════════════════════════════
    col_lf, col_ai = st.columns([1, 1], gap="large")

    with col_lf:
        st.markdown('<div class="section-label">Load Factor — IndiGo Flights</div>', unsafe_allow_html=True)

        if not indigo_f.empty:
            lf_data = indigo_f.groupby(["Flight No.", "Departure Time"])["Load Factor"].mean().reset_index()
            lf_data["LF %"] = (lf_data["Load Factor"] * 100).round(1)
            lf_data["Label"] = lf_data["Flight No."].astype(str) + " " + lf_data["Departure Time"].astype(str)
            lf_data["Color"] = lf_data["Load Factor"].apply(
                lambda x: "#2ecc71" if x <= 0.70 else ("#f39c12" if x <= 0.85 else "#e74c3c")
            )

            fig = go.Figure()
            fig.add_trace(go.Bar(
                x=lf_data["Label"],
                y=lf_data["LF %"],
                marker_color=lf_data["Color"],
                text=lf_data["LF %"].apply(lambda x: f"{x}%"),
                textposition="outside",
                textfont=dict(color="white", size=11),
            ))
            fig.add_hline(y=70, line_dash="dot", line_color="#f39c12",
                          annotation_text="70% threshold", annotation_font_color="#f39c12")
            fig.add_hline(y=85, line_dash="dot", line_color="#e74c3c",
                          annotation_text="85% threshold", annotation_font_color="#e74c3c")
            fig.update_layout(
                plot_bgcolor="#0a0e1a",
                paper_bgcolor="#0a0e1a",
                font=dict(color="#8ba8c8", family="Inter"),
                xaxis=dict(gridcolor="#1e3a5f", tickfont=dict(size=10)),
                yaxis=dict(gridcolor="#1e3a5f", range=[0, 110], title="Load Factor %"),
                margin=dict(l=10, r=10, t=10, b=10),
                height=280,
                showlegend=False,
            )
            st.plotly_chart(fig, use_container_width=True)

            # Price trend chart
            st.markdown('<div class="section-label" style="margin-top:1rem">Competitor Price Trend — Last 30 Days</div>',
                        unsafe_allow_html=True)

            if not comp_f.empty and "Scrape Date" in comp_df.columns:
                trend_data = comp_df[
                    (comp_df["Route"] == selected_route) &
                    (comp_df["Cabin Class"] == selected_cabin) &
                    (comp_df["Scrape Date"] >= date_min)
                ].groupby(["Scrape Date", "Airline"])["Fare (INR)"].mean().reset_index()

                if not trend_data.empty:
                    fig2 = px.line(
                        trend_data,
                        x="Scrape Date", y="Fare (INR)", color="Airline",
                        color_discrete_map={
                            "Air India": "#3498db",
                            "Akasa Air": "#e74c3c",
                        },
                        markers=True,
                    )
                    fig2.update_layout(
                        plot_bgcolor="#0a0e1a",
                        paper_bgcolor="#0a0e1a",
                        font=dict(color="#8ba8c8", family="Inter"),
                        xaxis=dict(gridcolor="#1e3a5f"),
                        yaxis=dict(gridcolor="#1e3a5f", title="Avg Fare (₹)"),
                        legend=dict(bgcolor="#0a0e1a", bordercolor="#1e3a5f"),
                        margin=dict(l=10, r=10, t=10, b=10),
                        height=220,
                    )
                    st.plotly_chart(fig2, use_container_width=True)
        else:
            st.info("No IndiGo data for this selection.")

    with col_ai:
        st.markdown('<div class="section-label">AI Pricing Recommendation</div>', unsafe_allow_html=True)

        # Flight selector
        if not indigo_f.empty:
            flight_options = (
                indigo_f[["Flight No.", "Departure Time", "Departure Date", "Days to Departure"]]
                .drop_duplicates()
                .sort_values("Departure Date")
            )
            flight_labels = [
                f"{r['Flight No.']} {r['Departure Time']} — {pd.Timestamp(r['Departure Date']).strftime('%d %b') if hasattr(r['Departure Date'], 'strftime') else r['Departure Date']} ({r['Days to Departure']}d out)"
                for _, r in flight_options.iterrows()
            ]
            selected_label = st.selectbox("Select Flight", flight_labels)
            sel_idx        = flight_labels.index(selected_label)
            sel_row        = flight_options.iloc[sel_idx]
            sel_flight     = sel_row["Flight No."]
            sel_dep_time   = sel_row["Departure Time"]
            sel_dep_date   = sel_row["Departure Date"]
            sel_days       = int(sel_row["Days to Departure"])

            # Get IndiGo ops for selected flight
            indigo_sel = indigo_f[
                (indigo_f["Flight No."] == sel_flight) &
                (indigo_f["Departure Date"] == sel_dep_date)
            ]
            sel_lf          = float(indigo_sel["Load Factor"].iloc[0]) if not indigo_sel.empty else 0.6
            sel_seats_sold  = int(indigo_sel["Seats Sold"].iloc[0]) if not indigo_sel.empty else 0
            sel_total_seats = int(indigo_sel["Total Seats"].iloc[0]) if not indigo_sel.empty else 180
            sel_holiday     = str(indigo_sel["Holiday / Festival"].iloc[0]) if not indigo_sel.empty else "No"
            sel_time_slot   = str(indigo_sel["Time Slot"].iloc[0]) if not indigo_sel.empty else ""
            sel_hour        = dep_hour_from_time(sel_dep_time)

            # Get matching competitor fares (same dep date)
            comp_sel = comp_f[comp_f["Departure Date"] == sel_dep_date]
            comp_list = []
            for _, cr in comp_sel.iterrows():
                comp_list.append((
                    cr["Airline"],
                    cr["Flight No."],
                    cr["Departure Time"],
                    int(cr["Fare (INR)"])
                ))
            best_comp = min([c[3] for c in comp_list], default=0)

            # Arithmetic fare
            arith_fare, breakdown = calculate_arithmetic_fare(
                selected_route, selected_cabin, sel_days,
                sel_lf, best_comp, sel_holiday == "Yes", sel_hour
            )

            # Show flight summary
            lf_cls, lf_ico = lf_color(sel_lf)
            st.markdown(f"""
            <div style="background:#0f1829;border:1px solid #1e3a5f;border-radius:8px;padding:0.8rem 1rem;margin-bottom:0.8rem;font-size:0.8rem;color:#8ba8c8;">
                <span style="color:#ffffff;font-weight:600;">{sel_flight}</span> &nbsp;·&nbsp; {sel_dep_time} &nbsp;·&nbsp; {sel_time_slot}<br>
                Load Factor: <span class="{lf_cls}">{lf_ico} {round(sel_lf*100,1)}%</span>
                &nbsp;&nbsp; Seats: {sel_seats_sold}/{sel_total_seats}
                &nbsp;&nbsp; Days out: {sel_days}<br>
                Arithmetic Fare: <span style="color:#ffffff;font-family:'JetBrains Mono',monospace;">₹{arith_fare:,}</span>
            </div>
            """, unsafe_allow_html=True)

            # Strategic direction — set BEFORE AI call
            st.markdown("**Strategic Direction** *(influences AI recommendation)*")
            strategic = st.selectbox("", STRATEGIC_OPTIONS, label_visibility="collapsed")

            # Get AI recommendation button
            if st.button("🤖 Get AI Recommendation", key="get_ai"):
                feedback_hist = []
                if not feedback_df.empty:
                    feedback_hist = feedback_df[
                        (feedback_df.get("Route", pd.Series(dtype=str)) == selected_route) &
                        (feedback_df.get("Cabin Class", pd.Series(dtype=str)) == selected_cabin)
                    ].to_dict("records")

                with st.spinner("Calling Gemini..."):
                    decision, ai_fare, rationale = call_gemini(
                        selected_route, sel_flight, sel_dep_time,
                        selected_cabin, sel_dep_date, sel_days,
                        sel_lf, arith_fare, comp_list,
                        strategic if "None" not in strategic else "",
                        feedback_hist
                    )

                st.session_state["ai_result"] = {
                    "decision": decision, "fare": ai_fare,
                    "rationale": rationale, "arith_fare": arith_fare,
                    "flight": sel_flight, "dep_time": sel_dep_time,
                    "dep_date": str(sel_dep_date)[:10],
                    "days": sel_days, "lf": sel_lf,
                    "strategic": strategic,
                }

            # Show AI result if available
            if "ai_result" in st.session_state:
                r = st.session_state["ai_result"]
                badge = "ai-approve" if r["decision"] == "Approve" else "ai-override"
                icon  = "✅" if r["decision"] == "Approve" else "⚠️"

                st.markdown(f"""
                <div class="ai-panel">
                    <span class="{badge}">{icon} {r['decision']}</span><br>
                    <div class="ai-fare">{format_inr(r['fare'])}</div>
                    <div class="ai-rationale">{r['rationale']}</div>
                </div>
                """, unsafe_allow_html=True)

                st.markdown("<br>**Manager Decision**", unsafe_allow_html=True)

                # Strategic direction AFTER — can change
                strategic_after = st.selectbox(
                    "Revise Strategic Direction (optional)",
                    STRATEGIC_OPTIONS,
                    index=STRATEGIC_OPTIONS.index(r["strategic"]) if r["strategic"] in STRATEGIC_OPTIONS else 0,
                    key="strategic_after"
                )

                mgr_col1, mgr_col2, mgr_col3 = st.columns(3)
                with mgr_col1:
                    if st.button("✅ Accept", key="accept"):
                        save_feedback({
                            "Timestamp":          datetime.now().strftime("%Y-%m-%d %H:%M"),
                            "Route":              selected_route,
                            "Flight No.":         r["flight"],
                            "Departure Time":     r["dep_time"],
                            "Departure Date":     r["dep_date"],
                            "Cabin Class":        selected_cabin,
                            "Days to Departure":  r["days"],
                            "Load Factor":        round(r["lf"] * 100, 1),
                            "Arithmetic Fare":    r["arith_fare"],
                            "AI Decision":        r["decision"],
                            "AI Suggested Fare":  r["fare"],
                            "AI Rationale":       r["rationale"],
                            "Manager Decision":   "Accepted",
                            "Final Fare Used":    r["fare"],
                            "Strategic Direction": strategic_after,
                            "Manager Notes":      "",
                        })
                        st.success("Accepted and saved to Feedback tab.")
                        del st.session_state["ai_result"]

                with mgr_col2:
                    override_fare = st.number_input("Override fare ₹", min_value=1000,
                                                     max_value=200000, value=r["fare"],
                                                     step=100, key="override_val")
                    if st.button("✏️ Override", key="override"):
                        save_feedback({
                            "Timestamp":          datetime.now().strftime("%Y-%m-%d %H:%M"),
                            "Route":              selected_route,
                            "Flight No.":         r["flight"],
                            "Departure Time":     r["dep_time"],
                            "Departure Date":     r["dep_date"],
                            "Cabin Class":        selected_cabin,
                            "Days to Departure":  r["days"],
                            "Load Factor":        round(r["lf"] * 100, 1),
                            "Arithmetic Fare":    r["arith_fare"],
                            "AI Decision":        r["decision"],
                            "AI Suggested Fare":  r["fare"],
                            "AI Rationale":       r["rationale"],
                            "Manager Decision":   "Overridden",
                            "Final Fare Used":    override_fare,
                            "Strategic Direction": strategic_after,
                            "Manager Notes":      "",
                        })
                        st.success(f"Overridden to ₹{override_fare:,} and saved.")
                        del st.session_state["ai_result"]

                with mgr_col3:
                    if st.button("❌ Reject", key="reject"):
                        save_feedback({
                            "Timestamp":          datetime.now().strftime("%Y-%m-%d %H:%M"),
                            "Route":              selected_route,
                            "Flight No.":         r["flight"],
                            "Departure Time":     r["dep_time"],
                            "Departure Date":     r["dep_date"],
                            "Cabin Class":        selected_cabin,
                            "Days to Departure":  r["days"],
                            "Load Factor":        round(r["lf"] * 100, 1),
                            "Arithmetic Fare":    r["arith_fare"],
                            "AI Decision":        r["decision"],
                            "AI Suggested Fare":  r["fare"],
                            "AI Rationale":       r["rationale"],
                            "Manager Decision":   "Rejected",
                            "Final Fare Used":    r["arith_fare"],
                            "Strategic Direction": strategic_after,
                            "Manager Notes":      "",
                        })
                        st.warning("Rejected — base fare retained.")
                        del st.session_state["ai_result"]
        else:
            st.info("No IndiGo flights found for this selection.")

    st.markdown("<br>", unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════
    # SECTION 4 — PROFITABILITY
    # ══════════════════════════════════════════════════════════
    st.markdown('<div class="section-label">Profitability — Last 30 Days (Accepted AI Recommendations)</div>',
                unsafe_allow_html=True)

    cost_seat = COST_PER_SEAT.get(selected_route, 3000)

    if not feedback_df.empty and "Manager Decision" in feedback_df.columns:
        accepted_fb = feedback_df[
            feedback_df["Manager Decision"].isin(["Accepted", "Overridden"])
        ].copy()

        if not accepted_fb.empty:
            accepted_fb["Final Fare Used"]   = pd.to_numeric(accepted_fb["Final Fare Used"], errors="coerce")
            accepted_fb["Cost Per Seat"]     = accepted_fb["Route"].map(COST_PER_SEAT).fillna(3000)
            accepted_fb["Profit Per Seat"]   = accepted_fb["Final Fare Used"] - accepted_fb["Cost Per Seat"]
            accepted_fb["Load Factor Num"]   = pd.to_numeric(accepted_fb["Load Factor"], errors="coerce") / 100
            accepted_fb["Total Seats"]       = accepted_fb["Route"].map(TOTAL_SEATS).fillna(180)
            accepted_fb["Est Flight Profit"] = accepted_fb["Profit Per Seat"] * accepted_fb["Total Seats"] * accepted_fb["Load Factor Num"]
            accepted_fb["Base Fare"]         = accepted_fb["Route"].map(BASE_FARES).fillna(5000)
            accepted_fb["Revenue Uplift"]    = (accepted_fb["Final Fare Used"] - accepted_fb["Base Fare"]) * accepted_fb["Total Seats"] * accepted_fb["Load Factor Num"]

            # KPI row
            p1, p2, p3, p4 = st.columns(4)
            with p1:
                st.metric("Total Recommendations Accepted",
                          len(accepted_fb))
            with p2:
                total_uplift = accepted_fb["Revenue Uplift"].sum()
                st.metric("Total Revenue Uplift vs Base",
                          format_inr(total_uplift),
                          delta="vs base fare pricing")
            with p3:
                avg_profit_seat = accepted_fb["Profit Per Seat"].mean()
                st.metric("Avg Profit Per Seat",
                          format_inr(avg_profit_seat))
            with p4:
                total_flight_profit = accepted_fb["Est Flight Profit"].sum()
                st.metric("Est. Total Flight Profit",
                          format_inr(total_flight_profit))

            st.markdown("<br>", unsafe_allow_html=True)

            # Profit by route bar chart
            route_profit = accepted_fb.groupby("Route")["Est Flight Profit"].sum().reset_index()
            route_profit = route_profit.sort_values("Est Flight Profit", ascending=True)

            fig3 = go.Figure(go.Bar(
                x=route_profit["Est Flight Profit"],
                y=route_profit["Route"],
                orientation="h",
                marker_color=["#2ecc71" if x > 0 else "#e74c3c"
                              for x in route_profit["Est Flight Profit"]],
                text=[format_inr(x) for x in route_profit["Est Flight Profit"]],
                textposition="outside",
                textfont=dict(color="white", size=11),
            ))
            fig3.update_layout(
                plot_bgcolor="#0a0e1a",
                paper_bgcolor="#0a0e1a",
                font=dict(color="#8ba8c8", family="Inter"),
                xaxis=dict(gridcolor="#1e3a5f", title="Estimated Flight Profit (₹)"),
                yaxis=dict(gridcolor="#1e3a5f"),
                margin=dict(l=10, r=80, t=10, b=10),
                height=250,
            )
            st.plotly_chart(fig3, use_container_width=True)

            # Detail table
            st.markdown('<div class="section-label" style="margin-top:0.5rem">Recommendation Detail</div>',
                        unsafe_allow_html=True)

            display_cols = ["Route", "Flight No.", "Departure Date", "Cabin Class",
                            "Load Factor", "Arithmetic Fare", "AI Suggested Fare",
                            "Final Fare Used", "Manager Decision", "Cost Per Seat",
                            "Profit Per Seat", "Est Flight Profit"]
            show_cols = [c for c in display_cols if c in accepted_fb.columns]
            st.dataframe(
                accepted_fb[show_cols].sort_values("Departure Date", ascending=False),
                use_container_width=True,
                hide_index=True,
            )

        else:
            st.info("No accepted recommendations yet. Use the AI Recommendation panel above to get started.")
    else:
        st.info("No feedback data yet. Accept or override an AI recommendation above — it will appear here.")

    # Footer
    st.markdown("""
    <div style="margin-top:2rem;padding-top:1rem;border-top:1px solid #1e3a5f;
                text-align:center;font-size:0.7rem;color:#2a4a6b;">
        IndiGo Pricing Intelligence · Team 5 ISB ALP 2026 · Powered by Gemini AI
    </div>
    """, unsafe_allow_html=True)


if __name__ == "__main__":
    main()
