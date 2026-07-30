"""Leveraged expression of a directional view.

Two routes exist:

  single-stock LETFs   2x daily exposure to one name (NVDL, TSLL, CONL...)
  sector/index LETFs   3x daily exposure to a basket (SOXL, LABU, FAS...)

Both reset **daily**. Their compounding is path-dependent: in a choppy tape a
3x fund loses value even when the underlying finishes flat. They express a
view about the next few days or weeks, never a position to sit in. Every
suggestion built here carries that warning, and leverage is only offered when
the underlying trend is strong enough (high ADX) that chop is less likely.
"""

from __future__ import annotations

# ticker -> (bull LETF, bear LETF, leverage factor)
SINGLE_STOCK: dict[str, tuple[str | None, str | None, str]] = {
    "NVDA": ("NVDL", "NVD", "2x"),
    "TSLA": ("TSLL", "TSLQ", "2x"),
    "AAPL": ("AAPU", "AAPD", "2x"),
    "MSFT": ("MSFU", "MSFD", "2x"),
    "AMZN": ("AMZU", "AMZD", "2x"),
    "GOOGL": ("GGLL", "GGLS", "2x"),
    "META": ("METU", "METD", "2x"),
    "AMD": ("AMDL", "AMDS", "2x"),
    "MSTR": ("MSTU", "MSTZ", "2x"),
    "COIN": ("CONL", "CONI", "2x"),
    "PLTR": ("PLTU", "PLTD", "2x"),
    "AVGO": ("AVL", "AVS", "2x"),
    "MU": ("MUU", "MUD", "2x"),
    "NFLX": ("NFXL", "NFXS", "2x"),
    "SMCI": ("SMCX", "SMCZ", "2x"),
    "TSM": ("TSMX", "TSMZ", "2x"),
    "MRVL": ("MRVU", None, "2x"),
    "BRK-B": ("BRKU", None, "2x"),
    "JPM": ("JPMU", None, "2x"),
    "UNH": ("UNHU", None, "2x"),
    "LLY": ("ELIL", "ELIS", "2x"),
    "CRWV": ("CRWL", None, "2x"),
    "HOOD": ("HOOU", None, "2x"),
}

# theme id -> (bull LETF, bear LETF, leverage factor, basket description)
THEME_ETF: dict[str, tuple[str | None, str | None, str, str]] = {
    "ai_semis":      ("SOXL", "SOXS", "3x", "semiconductor index"),
    "software":      ("TECL", "TECS", "3x", "technology sector"),
    "internet":      ("WEBL", "WEBS", "3x", "internet index"),
    "cyber":         ("TECL", "TECS", "3x", "technology sector"),
    "quantum":       ("TECL", "TECS", "3x", "technology sector"),
    "hardware":      ("TECL", "TECS", "3x", "technology sector"),
    "space_defense": ("DFEN", None, "3x", "aerospace & defense"),
    "fintech":       ("FAS", "FAZ", "3x", "financial sector"),
    "banks":         ("DPST", "WDRW", "3x", "regional banks"),
    "insurance":     ("FAS", "FAZ", "3x", "financial sector"),
    "capital_mkts":  ("FAS", "FAZ", "3x", "financial sector"),
    "crypto":        ("BITX", None, "2x", "bitcoin futures"),
    "biotech":       ("LABU", "LABD", "3x", "biotech index"),
    "pharma":        ("CURE", None, "3x", "healthcare sector"),
    "medtech":       ("CURE", None, "3x", "healthcare sector"),
    "health_svcs":   ("CURE", None, "3x", "healthcare sector"),
    "oil_gas":       ("GUSH", "DRIP", "3x", "oil & gas E&P"),
    "clean_energy":  ("TAN", None, "1x", "solar sector (unlevered)"),
    "nuclear":       ("URAA", None, "2x", "uranium miners"),
    "utilities":     ("UTSL", None, "3x", "utilities sector"),
    "mining":        ("NUGT", "DUST", "3x", "gold miners"),
    "materials":     ("MATL", None, "2x", "materials sector"),
    "ev_auto":       ("CARU", None, "3x", "auto sector"),
    "industrials":   ("DUSL", None, "3x", "industrials sector"),
    "transport":     ("DUSL", None, "3x", "industrials sector"),
    "retail":        ("RETL", None, "3x", "retail index"),
    "travel":        ("TPOR", None, "3x", "transport & travel"),
    "reits":         ("DRN", "DRV", "3x", "real estate sector"),
    "homebuild":     ("NAIL", None, "3x", "homebuilders"),
    "media":         ("TECL", "TECS", "3x", "technology sector"),
    "telecom":       ("TECL", "TECS", "3x", "technology sector"),
}

# Fallback when a theme has no dedicated fund: broad market.
BROAD = {"bull": ("TQQQ", "3x", "Nasdaq 100"), "bear": ("SQQQ", "3x", "Nasdaq 100")}
BROAD_SMALL = {"bull": ("TNA", "3x", "Russell 2000"),
               "bear": ("TZA", "3x", "Russell 2000")}


def resolve(ticker: str, theme: str, direction: str,
            cap_bucket: str = "unknown") -> dict | None:
    """Best leveraged instrument for this view, or None if there isn't one."""
    want_bull = direction == "long"

    single = SINGLE_STOCK.get(ticker)
    if single:
        sym = single[0] if want_bull else single[1]
        if sym:
            return {
                "symbol": sym,
                "factor": single[2],
                "kind": "single-stock",
                "tracks": f"{ticker} directly",
            }

    basket = THEME_ETF.get(theme)
    if basket:
        sym = basket[0] if want_bull else basket[1]
        if sym:
            return {
                "symbol": sym,
                "factor": basket[2],
                "kind": "sector",
                "tracks": basket[3],
            }

    fallback = BROAD_SMALL if cap_bucket in ("small", "micro") else BROAD
    sym, factor, tracks = fallback["bull" if want_bull else "bear"]
    return {"symbol": sym, "factor": factor, "kind": "broad-market",
            "tracks": tracks}


DECAY_WARNING = ("Daily-reset leverage: compounding decays this position in "
                 "choppy tape even if the underlying goes your way. Days to "
                 "weeks, not months.")
PROXY_WARNING = ("This fund tracks the basket, not the individual stock — "
                 "single-name strength can be diluted or offset by peers.")
