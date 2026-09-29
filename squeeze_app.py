#!/usr/bin/env python3
"""
Short Squeeze Probability — Mobile Web App
==========================================
Run locally:  streamlit run squeeze_app.py
Deploy free:  push to GitHub → share.streamlit.io

Designed for iPhone Safari. Add to Home Screen for an app-like experience.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Dict

import requests
import streamlit as st
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Page config — mobile friendly
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Squeeze Scanner",
    page_icon="📈",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# Custom CSS for iPhone / touch
st.markdown("""
<style>
    /* Larger touch targets & readable text on phone */
    .stButton > button {
        width: 100%;
        height: 3.2rem;
        font-size: 1.1rem;
        border-radius: 12px;
        font-weight: 600;
    }
    /* Card-like metric containers */
    div[data-testid="stMetric"] {
        background: #1e1e2e;
        padding: 12px 16px;
        border-radius: 12px;
        border: 1px solid #333;
    }
    /* Tighten vertical space on mobile */
    .block-container { padding-top: 1.2rem; padding-bottom: 2rem; }
    h1 { font-size: 1.6rem !important; margin-bottom: 0.2rem !important; }
    h2, h3 { font-size: 1.2rem !important; }
    /* Badge styles */
    .badge-high { background:#c0392b; color:white; padding:4px 10px; border-radius:20px; font-weight:700; font-size:0.85rem; }
    .badge-elevated { background:#d35400; color:white; padding:4px 10px; border-radius:20px; font-weight:700; font-size:0.85rem; }
    .badge-moderate { background:#2980b9; color:white; padding:4px 10px; border-radius:20px; font-weight:700; font-size:0.85rem; }
    .badge-low { background:#7f8c8d; color:white; padding:4px 10px; border-radius:20px; font-weight:700; font-size:0.85rem; }
    .score-big { font-size:1.8rem; font-weight:800; }
    .ticker { font-size:1.3rem; font-weight:700; letter-spacing:0.5px; }
    .subtle { color:#aaa; font-size:0.85rem; }
    /* Hide Streamlit branding a bit */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Core data & logic (self-contained)
# ---------------------------------------------------------------------------
FINVIZ_BASE = "https://finviz.com/screener.ashx"
XOOMAR_SI = "https://xoomar.com/api/markets/short-interest"
XOOMAR_SV = "https://xoomar.com/api/markets/short-volume"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


@dataclass
class Candidate:
    ticker: str
    market_cap: str = ""
    float_shares: str = ""
    float_num: float = 0.0
    short_float_pct: float = 0.0
    short_ratio: float = 0.0
    avg_volume: float = 0.0
    price: float = 0.0
    change_pct: float = 0.0
    volume: float = 0.0
    rel_volume: float = 0.0
    finra_days_to_cover: Optional[float] = None
    finra_change_pct: Optional[float] = None
    recent_short_vol_ratio: Optional[float] = None
    squeeze_score: float = 0.0
    probability_label: str = "Low"
    score_breakdown: Dict[str, float] = field(default_factory=dict)

    def compute_score(self) -> float:
        pts: Dict[str, float] = {}
        sf = self.short_float_pct
        if sf >= 50:
            pts["short_float"] = 40.0
        elif sf >= 40:
            pts["short_float"] = 32.0 + (sf - 40) * 0.8
        elif sf >= 30:
            pts["short_float"] = 22.0 + (sf - 30) * 1.0
        elif sf >= 20:
            pts["short_float"] = 10.0 + (sf - 20) * 1.2
        else:
            pts["short_float"] = max(0.0, sf * 0.5)

        dtc = self.finra_days_to_cover if self.finra_days_to_cover is not None else self.short_ratio
        dtc = dtc or 0.0
        if dtc >= 15:
            pts["days_to_cover"] = 25.0
        elif dtc >= 10:
            pts["days_to_cover"] = 20.0 + (dtc - 10) * 1.0
        elif dtc >= 5:
            pts["days_to_cover"] = 12.0 + (dtc - 5) * 1.6
        elif dtc >= 3:
            pts["days_to_cover"] = 6.0 + (dtc - 3) * 3.0
        else:
            pts["days_to_cover"] = max(0.0, dtc * 2.0)

        fl = self.float_num
        if 0 < fl < 10e6:
            pts["low_float"] = 15.0
        elif fl < 25e6:
            pts["low_float"] = 12.0
        elif fl < 50e6:
            pts["low_float"] = 8.0
        elif fl < 100e6:
            pts["low_float"] = 4.0
        else:
            pts["low_float"] = 0.0

        rv = self.rel_volume or 0.0
        if rv >= 3.0:
            pts["activity"] = 10.0
        elif rv >= 2.0:
            pts["activity"] = 7.0
        elif rv >= 1.5:
            pts["activity"] = 4.0
        elif rv >= 1.0:
            pts["activity"] = 2.0
        else:
            pts["activity"] = 0.0
        if self.change_pct > 5:
            pts["activity"] = min(10.0, pts["activity"] + 3.0)
        elif self.change_pct > 2:
            pts["activity"] = min(10.0, pts["activity"] + 1.5)

        trend = 0.0
        if self.finra_change_pct is not None:
            if self.finra_change_pct > 10:
                trend += 5.0
            elif self.finra_change_pct > 0:
                trend += 2.5
            elif self.finra_change_pct < -15:
                trend -= 2.0
        if self.recent_short_vol_ratio is not None:
            if self.recent_short_vol_ratio >= 0.55:
                trend += 5.0
            elif self.recent_short_vol_ratio >= 0.45:
                trend += 3.0
            elif self.recent_short_vol_ratio >= 0.35:
                trend += 1.0
        pts["trend"] = max(0.0, min(10.0, trend))

        self.squeeze_score = round(min(100.0, sum(pts.values())), 1)
        self.score_breakdown = {k: round(v, 1) for k, v in pts.items()}
        if self.squeeze_score >= 70:
            self.probability_label = "High"
        elif self.squeeze_score >= 50:
            self.probability_label = "Elevated"
        elif self.squeeze_score >= 30:
            self.probability_label = "Moderate"
        else:
            self.probability_label = "Low"
        return self.squeeze_score


def parse_number(s: str) -> float:
    if not s or s in ("-", "N/A", ""):
        return 0.0
    s = s.strip().replace(",", "").replace("%", "")
    mult = 1.0
    if s.endswith("B"):
        mult = 1e9
        s = s[:-1]
    elif s.endswith("M"):
        mult = 1e6
        s = s[:-1]
    elif s.endswith("K"):
        mult = 1e3
        s = s[:-1]
    try:
        return float(s) * mult
    except ValueError:
        return 0.0


def market_cap_m(s: str) -> float:
    return parse_number(s) / 1e6 if s else 0.0


@st.cache_data(ttl=900, show_spinner=False)  # cache 15 min
def fetch_finviz(min_short: float = 20.0, max_pages: int = 4) -> List[dict]:
    threshold = "o20" if min_short <= 20 else "o30" if min_short <= 30 else "o40"
    all_rows = []
    for page in range(1, max_pages + 1):
        params = {
            "v": "131",
            "f": f"sh_short_{threshold}",
            "o": "-shortinterest",
            "r": str((page - 1) * 20 + 1),
        }
        try:
            r = requests.get(FINVIZ_BASE, params=params, headers=HEADERS, timeout=25)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            target = None
            for table in soup.find_all("table"):
                if "Short Float" in table.get_text(" ", strip=True)[:200]:
                    target = table
                    break
            if not target:
                break
            for row in target.find_all("tr")[1:]:
                tds = row.find_all("td")
                if len(tds) < 15:
                    continue
                ticker = ""
                for td in tds[:3]:
                    a = td.find("a", href=True)
                    if a and "t=" in a["href"]:
                        m = re.search(r"[?&]t=([A-Za-z0-9.\-]+)", a["href"])
                        if m:
                            ticker = m.group(1).upper()
                            break
                if not ticker or len(ticker) > 6:
                    continue
                cells = [c.get_text(strip=True) for c in tds]
                all_rows.append({
                    "ticker": ticker,
                    "market_cap": cells[2],
                    "float_shares": cells[4],
                    "short_float_pct": parse_number(cells[9]),
                    "short_ratio": parse_number(cells[10]),
                    "avg_volume": parse_number(cells[11]),
                    "price": parse_number(cells[12]),
                    "change_pct": parse_number(cells[13]),
                    "volume": parse_number(cells[14]),
                })
            time.sleep(0.6)
        except Exception:
            break
    return all_rows


def enrich_one(ticker: str) -> dict:
    out = {}
    try:
        r = requests.get(XOOMAR_SI, params={"symbol": ticker}, headers=HEADERS, timeout=10)
        if r.status_code == 200:
            data = r.json().get("data") or []
            if data:
                latest = data[0]
                dtc = latest.get("daysToCover")
                out["finra_days_to_cover"] = float(dtc) if dtc not in (None, "") else None
                chg = latest.get("changePct")
                out["finra_change_pct"] = float(chg) if chg not in (None, "") else None
    except Exception:
        pass
    try:
        r = requests.get(XOOMAR_SV, params={"symbol": ticker, "days": 5}, headers=HEADERS, timeout=10)
        if r.status_code == 200:
            rows = r.json().get("data") or []
            ratios = [float(x["shortRatio"]) for x in rows if x.get("shortRatio") is not None]
            if ratios:
                out["recent_short_vol_ratio"] = sum(ratios) / len(ratios)
    except Exception:
        pass
    return out


def build_candidates(
    raw: List[dict],
    min_short: float,
    min_vol: float,
    min_price: float,
    min_mktcap: float,
    do_enrich: bool,
    enrich_limit: int = 18,
) -> List[Candidate]:
    cands = []
    for r in raw:
        if r["short_float_pct"] < min_short:
            continue
        if r["avg_volume"] < min_vol:
            continue
        if r["price"] < min_price:
            continue
        if market_cap_m(r["market_cap"]) < min_mktcap:
            continue
        float_num = parse_number(r["float_shares"])
        c = Candidate(
            ticker=r["ticker"],
            market_cap=r["market_cap"],
            float_shares=r["float_shares"],
            float_num=float_num,
            short_float_pct=r["short_float_pct"],
            short_ratio=r["short_ratio"],
            avg_volume=r["avg_volume"],
            price=r["price"],
            change_pct=r["change_pct"],
            volume=r["volume"],
        )
        if c.avg_volume > 0:
            c.rel_volume = c.volume / c.avg_volume
        cands.append(c)

    cands.sort(key=lambda x: x.short_float_pct, reverse=True)

    if do_enrich:
        for c in cands[:enrich_limit]:
            extra = enrich_one(c.ticker)
            c.finra_days_to_cover = extra.get("finra_days_to_cover")
            c.finra_change_pct = extra.get("finra_change_pct")
            c.recent_short_vol_ratio = extra.get("recent_short_vol_ratio")
            time.sleep(0.25)

    for c in cands:
        c.compute_score()
    cands.sort(key=lambda x: (x.squeeze_score, x.short_float_pct), reverse=True)
    return cands


def badge_html(label: str) -> str:
    cls = {
        "High": "badge-high",
        "Elevated": "badge-elevated",
        "Moderate": "badge-moderate",
        "Low": "badge-low",
    }.get(label, "badge-low")
    return f'<span class="{cls}">{label}</span>'


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
st.title("📈 Squeeze Scanner")
st.caption("High short-interest stocks · heuristic near-term squeeze probability")

with st.expander("⚙️ Filters", expanded=False):
    col1, col2 = st.columns(2)
    with col1:
        min_short = st.slider("Min short float %", 15, 50, 25, 5)
        min_vol = st.select_slider(
            "Min avg volume",
            options=[200_000, 500_000, 1_000_000, 2_000_000],
            value=500_000,
            format_func=lambda x: f"{x/1e6:.1f}M" if x >= 1e6 else f"{x/1e3:.0f}K",
        )
    with col2:
        min_price = st.slider("Min price $", 1.0, 10.0, 1.0, 0.5)
        min_mktcap = st.select_slider(
            "Min market cap ($M)",
            options=[50, 100, 200, 500, 1000],
            value=100,
        )
    do_enrich = st.toggle("Enrich with FINRA + short volume (slower)", value=True)
    top_n = st.slider("Show top N", 10, 40, 20, 5)

scan = st.button("🔍 Scan Market", type="primary", use_container_width=True)

if "last_results" not in st.session_state:
    st.session_state.last_results = None
    st.session_state.last_time = None

if scan:
    with st.spinner("Scanning Finviz + optional FINRA data…"):
        raw = fetch_finviz(min_short=min_short, max_pages=4)
        results = build_candidates(
            raw,
            min_short=min_short,
            min_vol=min_vol,
            min_price=min_price,
            min_mktcap=min_mktcap,
            do_enrich=do_enrich,
            enrich_limit=18,
        )
        st.session_state.last_results = results
        st.session_state.last_time = datetime.now(timezone.utc).strftime("%H:%M UTC")

results: Optional[List[Candidate]] = st.session_state.last_results

if results is None:
    st.info("Tap **Scan Market** to load the latest high short-interest names.")
    st.markdown("""
**How the score works**
- **Short float %** (up to 40 pts) — crowding
- **Days to cover** (up to 25) — exit difficulty  
- **Low float** (up to 15) — share scarcity  
- **Activity** (up to 10) — relative volume & momentum  
- **Trend** (up to 10) — rising SI or high daily short volume  

**Labels:** High ≥70 · Elevated 50–69 · Moderate 30–49 · Low <30  

This is a research heuristic only. Squeezes are rare and hard to time.
""")
else:
    st.success(f"Found **{len(results)}** candidates · updated {st.session_state.last_time}")

    # Summary chips
    high = sum(1 for c in results if c.probability_label == "High")
    elev = sum(1 for c in results if c.probability_label == "Elevated")
    c1, c2, c3 = st.columns(3)
    c1.metric("High", high)
    c2.metric("Elevated", elev)
    c3.metric("Total", len(results))

    st.divider()

    for i, c in enumerate(results[:top_n], 1):
        dtc = c.finra_days_to_cover if c.finra_days_to_cover is not None else c.short_ratio
        chg_color = "normal"
        if c.change_pct > 2:
            chg_color = "off"
        elif c.change_pct < -2:
            chg_color = "inverse"

        with st.container():
            left, right = st.columns([2.2, 1])
            with left:
                st.markdown(
                    f'<span class="ticker">{i}. {c.ticker}</span>  {badge_html(c.probability_label)}',
                    unsafe_allow_html=True,
                )
                st.caption(
                    f"Short {c.short_float_pct:.1f}% · DTC {dtc:.1f} · "
                    f"Float {c.float_shares} · {c.market_cap}"
                )
            with right:
                st.markdown(
                    f'<div style="text-align:right"><span class="score-big">{c.squeeze_score:.0f}</span>'
                    f'<div class="subtle">score</div></div>',
                    unsafe_allow_html=True,
                )

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Price", f"${c.price:.2f}", f"{c.change_pct:+.1f}%")
            m2.metric("Rel Vol", f"{c.rel_volume:.1f}x")
            si_txt = f"{c.finra_change_pct:+.1f}%" if c.finra_change_pct is not None else "—"
            m3.metric("SI Δ", si_txt)
            svr = f"{c.recent_short_vol_ratio:.2f}" if c.recent_short_vol_ratio is not None else "—"
            m4.metric("SVolR", svr)

            with st.expander("Score breakdown"):
                for k, v in c.score_breakdown.items():
                    st.write(f"**{k.replace('_', ' ').title()}**: {v:.1f} pts")
                st.caption(
                    "Max: Short float 40 · Days-to-cover 25 · Low float 15 · "
                    "Activity 10 · Trend 10"
                )

            st.divider()

    st.markdown("""
---
**Disclaimer**  
Research tool only — not financial advice.  
Short squeezes are rare, difficult to time, and can reverse sharply.  
High short interest is a risk factor, not a buy signal.
""")

# Footer tip for iPhone
st.markdown("""
<div class="subtle" style="text-align:center; margin-top:1.5rem;">
On iPhone: Safari → Share → <b>Add to Home Screen</b> for an app-like icon.
</div>
""", unsafe_allow_html=True)
