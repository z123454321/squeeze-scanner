#!/usr/bin/env python3
"""
Short Squeeze Probability Agent
===============================
Scans US equities for elevated short interest and assigns a heuristic
"near-term squeeze probability" rating based on positioning, liquidity,
and recent activity.

Data sources (free, no API key required):
  - Finviz Ownership screener : short float %, short ratio, float, volume, price
  - Xoomar / FINRA            : official bi-weekly short interest + daily short volume

IMPORTANT
---------
The probability score is a research heuristic only. True short-squeeze
probability is low and largely unknowable in advance. High scores indicate
crowded positioning that *could* produce a squeeze if a catalyst appears;
they are not forecasts. This tool is for idea generation and risk awareness,
not trade signals. Not financial advice.

Usage examples:
  python high_short_interest_agent.py
  python high_short_interest_agent.py --min-short 25 --enrich --top 25
  python high_short_interest_agent.py --min-short 30 --min-vol 1e6 --enrich
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Dict, Any

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
FINVIZ_BASE = "https://finviz.com/screener.ashx"
XOOMAR_SI = "https://xoomar.com/api/markets/short-interest"
XOOMAR_SV = "https://xoomar.com/api/markets/short-volume"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}

DEFAULT_MIN_SHORT_FLOAT = 20.0
DEFAULT_MIN_AVG_VOL = 500_000
DEFAULT_MIN_PRICE = 1.0
DEFAULT_MIN_MKT_CAP_M = 100.0
DEFAULT_TOP_N = 30


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class ShortCandidate:
    ticker: str
    market_cap: str = ""
    float_shares: str = ""          # as reported by Finviz (e.g. "45.2M")
    float_num: float = 0.0          # numeric shares
    short_float_pct: float = 0.0
    short_ratio: float = 0.0        # Finviz days-to-cover
    avg_volume: float = 0.0
    price: float = 0.0
    change_pct: float = 0.0
    volume: float = 0.0
    rel_volume: float = 0.0         # today vol / avg vol (approx)

    # FINRA bi-weekly print
    finra_short_qty: Optional[int] = None
    finra_days_to_cover: Optional[float] = None
    finra_settlement: Optional[str] = None
    finra_change_pct: Optional[float] = None   # change vs prior settlement

    # Daily short volume (recent)
    recent_short_vol_ratio: Optional[float] = None  # avg short/total last few days

    # Scores
    squeeze_score: float = 0.0      # 0–100 heuristic
    probability_label: str = "Low"
    score_breakdown: Dict[str, float] = field(default_factory=dict)

    def compute_squeeze_probability(self) -> float:
        """
        Heuristic 0–100 score for near-term squeeze *potential*.

        Components (max points):
          Short float % .......... 40
          Days to cover .......... 25
          Low float .............. 15
          Volume / activity ...... 10
          Positioning trend ...... 10
        Total possible ........... 100

        Mapping to labels (see probability_label):
          0–29   Low
          30–49  Moderate
          50–69  Elevated
          70–100 High
        """
        pts: Dict[str, float] = {}

        # 1. Short float % (0–40)
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

        # 2. Days to cover (0–25) — prefer FINRA if present
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

        # 3. Low float (0–15) — smaller free float → easier to squeeze
        fl = self.float_num
        if fl <= 0:
            pts["low_float"] = 0.0
        elif fl < 10e6:
            pts["low_float"] = 15.0
        elif fl < 25e6:
            pts["low_float"] = 12.0
        elif fl < 50e6:
            pts["low_float"] = 8.0
        elif fl < 100e6:
            pts["low_float"] = 4.0
        else:
            pts["low_float"] = 0.0

        # 4. Volume / activity (0–10)
        # Relative volume today + absolute liquidity floor already applied upstream
        rv = self.rel_volume or (self.volume / self.avg_volume if self.avg_volume > 0 else 0)
        self.rel_volume = rv
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
        # mild boost if already green today (possible early covering)
        if self.change_pct > 5:
            pts["activity"] = min(10.0, pts["activity"] + 3.0)
        elif self.change_pct > 2:
            pts["activity"] = min(10.0, pts["activity"] + 1.5)

        # 5. Positioning trend (0–10)
        # Rising short interest or persistently high daily short volume ratio
        trend = 0.0
        if self.finra_change_pct is not None:
            if self.finra_change_pct > 10:
                trend += 5.0
            elif self.finra_change_pct > 0:
                trend += 2.5
            elif self.finra_change_pct < -15:
                trend -= 2.0  # shorts already covering — less residual fuel
        if self.recent_short_vol_ratio is not None:
            if self.recent_short_vol_ratio >= 0.55:
                trend += 5.0
            elif self.recent_short_vol_ratio >= 0.45:
                trend += 3.0
            elif self.recent_short_vol_ratio >= 0.35:
                trend += 1.0
        pts["trend"] = max(0.0, min(10.0, trend))

        total = sum(pts.values())
        self.squeeze_score = round(min(100.0, total), 1)
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
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


def market_cap_to_millions(s: str) -> float:
    val = parse_number(s)
    return val / 1e6 if val else 0.0


# ---------------------------------------------------------------------------
# Finviz scraper
# ---------------------------------------------------------------------------
def fetch_finviz_page(page: int = 1, min_short: float = 20.0) -> List[dict]:
    threshold = "o20" if min_short <= 20 else "o30" if min_short <= 30 else "o40"
    params = {
        "v": "131",
        "f": f"sh_short_{threshold}",
        "o": "-shortinterest",
        "r": str((page - 1) * 20 + 1),
    }
    r = requests.get(FINVIZ_BASE, params=params, headers=HEADERS, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    target = None
    for table in soup.find_all("table"):
        header_text = table.get_text(" ", strip=True)[:200]
        if "Short Float" in header_text and "Ticker" in header_text:
            target = table
            break
    if target is None:
        return []

    results = []
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
        if not ticker:
            raw = tds[1].get_text(strip=True)
            ticker = re.sub(r"^[^A-Z]*", "", raw).upper()
        if not ticker or len(ticker) > 6:
            continue
        cells = [c.get_text(strip=True) for c in tds]
        results.append({
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
    return results


def scan_finviz(min_short: float, max_pages: int = 8) -> List[ShortCandidate]:
    all_rows: List[dict] = []
    for page in range(1, max_pages + 1):
        try:
            rows = fetch_finviz_page(page, min_short)
            if not rows:
                break
            all_rows.extend(rows)
            time.sleep(0.7)
        except Exception as e:
            print(f"[warn] Finviz page {page}: {e}", file=sys.stderr)
            break

    candidates = []
    for r in all_rows:
        float_num = parse_number(r["float_shares"])
        c = ShortCandidate(
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
        candidates.append(c)
    return candidates


# ---------------------------------------------------------------------------
# Enrichment (FINRA short interest + recent short volume)
# ---------------------------------------------------------------------------
def enrich_candidates(candidates: List[ShortCandidate], limit: int = 25) -> None:
    """Attach FINRA short-interest print + recent short-volume ratio.
    Limited to top names to respect free-tier rate limits (~10 req/min keyless).
    """
    session = requests.Session()
    session.headers.update(HEADERS)

    for i, c in enumerate(candidates[:limit]):
        # Bi-weekly short interest
        try:
            r = session.get(XOOMAR_SI, params={"symbol": c.ticker}, timeout=10)
            if r.status_code == 200:
                data = r.json().get("data") or []
                if data:
                    latest = data[0]
                    c.finra_short_qty = int(float(latest.get("shortQty") or 0))
                    dtc = latest.get("daysToCover")
                    c.finra_days_to_cover = float(dtc) if dtc not in (None, "") else None
                    c.finra_settlement = latest.get("settlementDate")
                    chg = latest.get("changePct")
                    c.finra_change_pct = float(chg) if chg not in (None, "") else None
        except Exception as e:
            print(f"[warn] SI {c.ticker}: {e}", file=sys.stderr)

        # Recent daily short volume ratio
        try:
            r = session.get(
                XOOMAR_SV,
                params={"symbol": c.ticker, "days": 5},
                timeout=10,
            )
            if r.status_code == 200:
                rows = r.json().get("data") or []
                ratios = [
                    float(x["shortRatio"])
                    for x in rows
                    if x.get("shortRatio") is not None
                ]
                if ratios:
                    c.recent_short_vol_ratio = sum(ratios) / len(ratios)
        except Exception as e:
            print(f"[warn] SV {c.ticker}: {e}", file=sys.stderr)

        # Pace requests (~8–10 / min)
        if (i + 1) % 4 == 0:
            time.sleep(1.2)
        else:
            time.sleep(0.25)


# ---------------------------------------------------------------------------
# Filter + rank
# ---------------------------------------------------------------------------
def filter_and_rank(
    candidates: List[ShortCandidate],
    min_short: float,
    min_vol: float,
    min_price: float,
    min_mkt_cap_m: float,
) -> List[ShortCandidate]:
    filtered = []
    for c in candidates:
        if c.short_float_pct < min_short:
            continue
        if c.avg_volume < min_vol:
            continue
        if c.price < min_price:
            continue
        if market_cap_to_millions(c.market_cap) < min_mkt_cap_m:
            continue
        c.compute_squeeze_probability()
        filtered.append(c)

    filtered.sort(key=lambda x: (x.squeeze_score, x.short_float_pct), reverse=True)
    return filtered


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
def print_table(candidates: List[ShortCandidate], top_n: int) -> None:
    rows = candidates[:top_n]
    if not rows:
        print("No candidates matched the filters.")
        return

    print(
        f"{'#':>3}  {'Ticker':<6}  {'Prob':<9}  {'Score':>5}  "
        f"{'Short%':>6}  {'DTC':>5}  {'Float':>8}  {'RelVol':>6}  "
        f"{'Chg%':>6}  {'Price':>8}  {'SI Δ%':>6}  {'SVolR':>5}"
    )
    print("-" * 95)

    for i, c in enumerate(rows, 1):
        dtc = c.finra_days_to_cover if c.finra_days_to_cover is not None else c.short_ratio
        si_chg = f"{c.finra_change_pct:+.1f}" if c.finra_change_pct is not None else "-"
        svr = f"{c.recent_short_vol_ratio:.2f}" if c.recent_short_vol_ratio is not None else "-"
        float_s = c.float_shares or "-"
        print(
            f"{i:>3}  {c.ticker:<6}  {c.probability_label:<9}  {c.squeeze_score:>5.1f}  "
            f"{c.short_float_pct:>5.1f}%  {dtc:>5.1f}  {float_s:>8}  {c.rel_volume:>5.1f}x  "
            f"{c.change_pct:>5.1f}%  {c.price:>8.2f}  {si_chg:>6}  {svr:>5}"
        )

    print()
    print("Score breakdown legend (max points):")
    print("  Short float 40 | Days-to-cover 25 | Low float 15 | Activity 10 | Trend 10")
    print()
    print("Probability labels are heuristic research ratings only.")
    print("Historical base rate of true squeezes is low; a high score is not a forecast.")


def print_detail(c: ShortCandidate) -> None:
    print(f"\n--- {c.ticker} detail ---")
    print(f"  Squeeze score     : {c.squeeze_score:.1f}  ({c.probability_label})")
    for k, v in c.score_breakdown.items():
        print(f"    {k:<16}: {v:.1f}")
    print(f"  Short float %     : {c.short_float_pct:.1f}%")
    print(f"  Days to cover     : {c.finra_days_to_cover or c.short_ratio:.1f}")
    print(f"  Float             : {c.float_shares} ({c.float_num:,.0f})")
    print(f"  Rel volume        : {c.rel_volume:.2f}x")
    print(f"  FINRA SI change   : {c.finra_change_pct}")
    print(f"  Recent short/vol  : {c.recent_short_vol_ratio}")


def save_csv(candidates: List[ShortCandidate], path: Path) -> None:
    if not candidates:
        return
    # Flatten breakdown for CSV
    rows = []
    for c in candidates:
        d = asdict(c)
        bd = d.pop("score_breakdown", {})
        for k, v in bd.items():
            d[f"pts_{k}"] = v
        rows.append(d)
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Saved {len(candidates)} rows → {path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scan for potential short-squeeze candidates and rate near-term probability (heuristic)."
    )
    parser.add_argument("--min-short", type=float, default=DEFAULT_MIN_SHORT_FLOAT,
                        help="Minimum short float %% (default 20)")
    parser.add_argument("--min-vol", type=float, default=DEFAULT_MIN_AVG_VOL,
                        help="Minimum average daily volume")
    parser.add_argument("--min-price", type=float, default=DEFAULT_MIN_PRICE,
                        help="Minimum last price")
    parser.add_argument("--min-mktcap", type=float, default=DEFAULT_MIN_MKT_CAP_M,
                        help="Minimum market cap in $M")
    parser.add_argument("--top", type=int, default=DEFAULT_TOP_N,
                        help="Rows to display")
    parser.add_argument("--enrich", action="store_true", default=True,
                        help="Fetch FINRA SI + recent short volume (default on)")
    parser.add_argument("--no-enrich", action="store_true",
                        help="Skip Xoomar enrichment (faster)")
    parser.add_argument("--pages", type=int, default=6,
                        help="Finviz pages to scrape (~20 names each)")
    parser.add_argument("--detail", type=str, default="",
                        help="Print score breakdown for one ticker after the scan")
    parser.add_argument("--out", type=str, default="",
                        help="CSV output path")
    args = parser.parse_args()

    do_enrich = args.enrich and not args.no_enrich

    print("=" * 78)
    print("  SHORT SQUEEZE PROBABILITY AGENT")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 78)
    print(
        f"Filters: short ≥ {args.min_short}%  |  vol ≥ {args.min_vol:,.0f}  |  "
        f"price ≥ ${args.min_price}  |  mkt cap ≥ ${args.min_mktcap:.0f}M"
    )
    print("Rating is a research heuristic only — not a prediction of future returns.\n")

    print("Scanning Finviz Ownership screener …")
    raw = scan_finviz(args.min_short, max_pages=args.pages)
    print(f"  Retrieved {len(raw)} raw rows")

    # Pre-filter before expensive enrichment
    pre = [
        c for c in raw
        if c.short_float_pct >= args.min_short
        and c.avg_volume >= args.min_vol
        and c.price >= args.min_price
        and market_cap_to_millions(c.market_cap) >= args.min_mktcap
    ]
    pre.sort(key=lambda x: x.short_float_pct, reverse=True)
    print(f"  After basic filters: {len(pre)} candidates")

    if do_enrich and pre:
        print("Enriching top names with FINRA short interest + recent short-volume ratio …")
        enrich_candidates(pre, limit=min(22, len(pre)))

    ranked = filter_and_rank(
        pre,
        min_short=args.min_short,
        min_vol=args.min_vol,
        min_price=args.min_price,
        min_mkt_cap_m=args.min_mktcap,
    )
    print(f"  Final ranked list: {len(ranked)} names\n")

    print_table(ranked, args.top)

    if args.detail:
        t = args.detail.upper()
        match = next((c for c in ranked if c.ticker == t), None)
        if match:
            print_detail(match)
        else:
            print(f"Ticker {t} not in current results.")

    out_path = Path(args.out) if args.out else Path(
        f"/home/workdir/artifacts/squeeze_prob_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    )
    if ranked:
        save_csv(ranked[: max(args.top * 2, 50)], out_path)

    print("\nDisclaimer: Short squeezes are rare, difficult to time, and can reverse")
    print("violently. High short interest is a risk factor, not a buy signal.")
    print("Always perform your own research and risk management.")


if __name__ == "__main__":
    main()
