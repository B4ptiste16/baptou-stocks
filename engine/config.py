"""Central tunables for the screener.

Everything that a human might reasonably want to tweak lives here so the rule
modules stay readable. Values are deliberately conservative: the point of the
liquidity and price floors is to keep setups that cannot actually be traded
(no borrow, no options, 10c spreads) out of the output entirely.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
CACHE_DIR = DATA_DIR / "cache"
OUT_DIR = ROOT / "docs" / "data"

for _d in (DATA_DIR, RAW_DIR, CACHE_DIR, OUT_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- universe --
# Chunk size for yfinance bulk downloads. 250 measured ~11s/chunk; larger
# chunks start dropping tickers silently.
DOWNLOAD_CHUNK = 250
DOWNLOAD_PERIOD = "2y"          # need >252 bars for 52w stats + 1y vol ranks
DOWNLOAD_RETRIES = 2
MAX_DOWNLOAD_WORKERS = 4        # parallel chunks; higher risks throttling

BENCHMARK = "SPY"
SECTOR_ETFS = {
    "Technology": "XLK", "Financial Services": "XLF", "Healthcare": "XLV",
    "Consumer Cyclical": "XLY", "Consumer Defensive": "XLP", "Energy": "XLE",
    "Industrials": "XLI", "Basic Materials": "XLB", "Utilities": "XLU",
    "Real Estate": "XLRE", "Communication Services": "XLC",
}

# --------------------------------------------------------------- liquidity --
MIN_PRICE = 3.00                # sub-$3 names: spreads eat any edge
MIN_DOLLAR_VOL = 1_000_000      # hard floor to appear at all ($/day, 20d avg)
LIQ_TIER_CASH = 1_000_000       # cash equity only
LIQ_TIER_OPTIONS = 10_000_000   # options structures become sane
LIQ_TIER_LEVERAGE = 25_000_000  # leveraged expression allowed
MIN_HISTORY_BARS = 200          # need a 200-day SMA to say anything about trend

# ----------------------------------------------------------------- signals --
MIN_SETUP_SCORE = 55            # below this a setup is not reported
MAX_PER_CATEGORY = 25           # cards per theme in the dashboard
TOP_N_FOR_OPTIONS = 120         # only fetch option chains for the best names

# Volatility banding on ATR% (ATR14 / close).
VOL_LOW = 0.020
VOL_HIGH = 0.055

# Risk model: stops and targets are ATR multiples.
STOP_ATR_MULT = 2.0
TARGET_ATR_MULT = 3.5
MIN_RISK_REWARD = 1.3

# ----------------------------------------------------------------- options --
# IV/RV ratio bands. We do not have a true IV-rank history on a free feed, so
# ATM implied vol is compared against the stock's own realised vol. >1.35 means
# options are pricing meaningfully more movement than the stock has delivered.
IV_RV_RICH = 1.35
IV_RV_CHEAP = 0.95
EARNINGS_SOON_DAYS = 10

# ------------------------------------------------------------------- pairs --
PAIR_LOOKBACK = 120
PAIR_MIN_CORR = 0.75
PAIR_ENTRY_Z = 2.0
