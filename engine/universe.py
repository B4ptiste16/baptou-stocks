"""Builds the tradeable universe from the free NasdaqTrader symbol directories.

Two files cover every US listed security:
  nasdaqlisted.txt  -> Nasdaq
  otherlisted.txt   -> NYSE, NYSE American, NYSE Arca, Cboe, IEX

No API key, no rate limit, updated nightly by the exchange. We strip out test
issues, ETFs, warrants, units, rights and preferred shares so what remains is
actual common stock that a directional signal means something for.
"""

from __future__ import annotations

import io
import re

import pandas as pd
import requests

NASDAQ_URL = "https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt"
OTHER_URL = "https://www.nasdaqtrader.com/dynamic/symdir/otherlisted.txt"

# Security-name patterns that mark a non-common-stock instrument. Note the
# optional plurals — the exchange writes "- Rights" and "- Units", so a bare
# \bright\b never fires.
_JUNK_NAME = re.compile(
    r"\b(?:warrants?|units?|rights?|preferreds?|pfd|depositary|debentures?|"
    r"notes?|bonds?|subordinated|convertible|contingent value)\b",
    re.IGNORECASE,
)
# Nasdaq encodes non-common issues as a dotted suffix (FOO.WS, FOO.U, FOO.PA).
_JUNK_TAIL = re.compile(r"\.(?:WS|WT|U|R|P[A-Z]?|CL)$", re.IGNORECASE)

EXCHANGE_NAMES = {
    "N": "NYSE", "A": "NYSE American", "P": "NYSE Arca",
    "Z": "Cboe BZX", "V": "IEX", "Q": "Nasdaq",
}


def _get(url: str) -> pd.DataFrame:
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    df = pd.read_csv(io.StringIO(resp.text), sep="|", dtype=str)
    # Last row of these files is a "File Creation Time" footer.
    first_col = df.columns[0]
    df = df[~df[first_col].astype(str).str.startswith("File Creation Time")]
    return df.fillna("")


def to_yahoo(symbol: str) -> str:
    """NasdaqTrader uses BRK.A / RDS.B; Yahoo wants BRK-A / RDS-B."""
    return symbol.strip().replace(".", "-")


def _clean(df: pd.DataFrame, sym_col: str, name_col: str,
           etf_col: str, test_col: str, exch: str | None) -> pd.DataFrame:
    df = df[df[test_col].str.upper() == "N"]
    out = pd.DataFrame({
        "raw_symbol": df[sym_col].str.strip(),
        "name": df[name_col].str.strip(),
        "is_etf": df[etf_col].str.upper().eq("Y"),
    })
    if exch is not None:
        out["exchange"] = exch
    else:
        out["exchange"] = df["Exchange"].map(EXCHANGE_NAMES).fillna("Other")
    return out


def load_universe() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (stocks, etfs), both indexed by Yahoo-style ticker."""
    nq = _get(NASDAQ_URL)
    ot = _get(OTHER_URL)

    a = _clean(nq, "Symbol", "Security Name", "ETF", "Test Issue", "Nasdaq")
    b = _clean(ot, "ACT Symbol", "Security Name", "ETF", "Test Issue", None)
    allsec = pd.concat([a, b], ignore_index=True)

    allsec = allsec[allsec["raw_symbol"].str.len().between(1, 6)]
    allsec = allsec[~allsec["raw_symbol"].str.contains("$", regex=False)]
    allsec = allsec[~allsec["raw_symbol"].str.contains(_JUNK_TAIL, na=False)]
    allsec = allsec[~allsec["name"].str.contains(_JUNK_NAME, na=False)]
    # 5-letter Nasdaq tickers ending W/R/U are warrants/rights/units whose
    # security name occasionally omits the keyword.
    _nq5 = (allsec["exchange"].eq("Nasdaq")
            & allsec["raw_symbol"].str.fullmatch(r"[A-Z]{4}[WRU]", na=False))
    allsec = allsec[~_nq5]

    allsec["ticker"] = allsec["raw_symbol"].map(to_yahoo)
    allsec = allsec.drop_duplicates(subset="ticker", keep="first")
    allsec = allsec.set_index("ticker").sort_index()

    stocks = allsec[~allsec["is_etf"]].copy()
    etfs = allsec[allsec["is_etf"]].copy()
    return stocks, etfs


if __name__ == "__main__":
    s, e = load_universe()
    print(f"stocks: {len(s)}  etfs: {len(e)}")
    print(s.head())
