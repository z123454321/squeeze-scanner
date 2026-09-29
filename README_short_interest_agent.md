# Short Squeeze Probability Agent

Scans the US equity market for stocks with elevated short interest and assigns a **heuristic near-term squeeze probability** rating.

## What it does

1. Pulls the Finviz Ownership screener (high short float %).
2. Filters for tradable liquidity and size.
3. Optionally enriches top names with:
   - Official FINRA bi-weekly short interest (shares short, days-to-cover, change vs prior period)
   - Recent daily short-volume ratio (short volume ÷ total volume)
4. Computes a transparent 0–100 **Squeeze Score** and maps it to a probability label.
5. Prints a ranked table and writes a CSV snapshot.

## Probability score (heuristic)

| Component            | Max pts | What it captures                          |
|----------------------|---------|-------------------------------------------|
| Short float %        | 40      | Crowding of the short side                |
| Days to cover        | 25      | How long it would take shorts to exit     |
| Low float            | 15      | Scarcity of shares available to trade     |
| Activity (rel vol)   | 10      | Current interest / possible early covering|
| Positioning trend    | 10      | Rising SI or high daily short volume ratio|

**Labels**

| Score   | Label     |
|---------|-----------|
| 70–100  | High      |
| 50–69   | Elevated  |
| 30–49   | Moderate  |
| 0–29    | Low       |

These are research ratings only. The historical base rate of true short squeezes is low. A high score means crowded positioning that *could* produce a violent move if a catalyst appears; it is not a forecast that a squeeze will occur.

## Quick start

```bash
cd /home/workdir/artifacts
python high_short_interest_agent.py
```

### Common flags

```bash
# Stricter screen + enrichment (default)
python high_short_interest_agent.py --min-short 30 --min-vol 1000000 --top 20

# Faster run (skip FINRA / short-volume calls)
python high_short_interest_agent.py --no-enrich

# Inspect score breakdown for one ticker
python high_short_interest_agent.py --detail GRPN
```

| Flag            | Default | Meaning                                      |
|-----------------|---------|----------------------------------------------|
| `--min-short`   | 20      | Minimum short float %                        |
| `--min-vol`     | 500000  | Minimum average daily volume                 |
| `--min-price`   | 1       | Minimum last price                           |
| `--min-mktcap`  | 100     | Minimum market cap ($M)                      |
| `--top`         | 30      | Rows to display                              |
| `--enrich`      | on      | Fetch FINRA + short-volume data              |
| `--no-enrich`   |         | Skip enrichment (faster)                     |
| `--pages`       | 6       | Finviz pages (~20 names each)                |
| `--detail TICK` |         | Print full score breakdown for one ticker    |
| `--out file.csv`| auto    | Custom CSV path                              |

## Data notes

- Short float % and short ratio on Finviz are derived from the latest FINRA report (settlement mid-month / month-end, published ~8 business days later).
- Between settlement dates the official short-interest number is static.
- Daily short-volume ratio is a higher-frequency series and can stay elevated even when the bi-weekly print is unchanged.
- Extremely high short % figures can include convertible arbitrage or other non-directional activity.

## Disclaimer

Research / idea-generation tool only. Not investment advice.  
Short squeezes are rare, hard to time, and frequently reverse sharply.  
High short interest is best treated as a risk factor and research flag, not a buy signal.  
Always do your own due diligence and risk management.
