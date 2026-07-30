"""ATM implied volatility and earnings proximity for screened candidates only.

Option chains cost ~1s per ticker, so this runs on the few hundred names that
already passed screening rather than the whole universe.

On the IV number: a real IV *rank* needs a year of implied-vol history, which
no free feed provides. What we can compute honestly is how ATM implied vol
compares to the volatility the stock has actually delivered:

    iv_rv = ATM implied vol / blended realised vol (20d & 60d)

Above ~1.35 the options market is charging meaningfully more than recent
history justifies (premium selling is favoured); below ~0.95 it is charging
less (premium buying is favoured). It is a proxy, and it is labelled as one
everywhere it surfaces.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime

import numpy as np
import pandas as pd
import yfinance as yf

from . import config as C

TARGET_DTE_LO = 25
TARGET_DTE_HI = 60


def _pick_expiry(expiries: tuple[str, ...]) -> tuple[str, int] | None:
    """Choose the expiry closest to the middle of our target DTE window."""
    today = date.today()
    best, best_gap = None, None
    for e in expiries:
        try:
            d = datetime.strptime(e, "%Y-%m-%d").date()
        except ValueError:
            continue
        dte = (d - today).days
        if dte < 7:
            continue
        gap = abs(dte - (TARGET_DTE_LO + TARGET_DTE_HI) / 2)
        if best_gap is None or gap < best_gap:
            best, best_gap = (e, dte), gap
    return best


MAX_SPREAD_PCT = 35.0     # wider than this and the "mid" is fiction
MIN_ATM_STRIKES = 2       # one lonely contract is not a volatility reading


def _atm_iv(chain: pd.DataFrame, spot: float) -> tuple[float, float, float, int]:
    """Return (atm_iv, median_spread_pct, total_open_interest, n_strikes).

    Deep ITM/OTM contracts carry nonsense implied vols on this feed, so we
    take the strikes *nearest* the money rather than a fixed percentage band —
    a $7 stock on $1 strike spacing has only one contract inside +/-7%, and
    reading a volatility surface off it is meaningless. Contracts whose
    bid/ask is too wide to imply a real mid are discarded outright.
    """
    if chain is None or chain.empty:
        return np.nan, np.nan, 0.0, 0
    df = chain.copy()
    # Generous outer bound; the nearest-N selection below does the real work.
    df = df[(df["strike"] > spot * 0.80) & (df["strike"] < spot * 1.20)]
    df = df[df["impliedVolatility"].between(0.05, 5.0)]
    df = df[(df["bid"] > 0) & (df["ask"] > 0) & (df["ask"] >= df["bid"])]
    if df.empty:
        return np.nan, np.nan, 0.0, 0

    mid = (df["bid"] + df["ask"]) / 2
    df["spread_pct"] = (df["ask"] - df["bid"]) / mid.replace(0, np.nan) * 100
    oi = float(df["openInterest"].fillna(0).sum())

    tradeable = df[df["spread_pct"] <= MAX_SPREAD_PCT]
    if tradeable.empty:
        return np.nan, float(df["spread_pct"].median()), oi, 0

    tradeable = tradeable.assign(dist=(tradeable["strike"] - spot).abs())
    near = tradeable.nsmallest(4, "dist")
    return (float(near["impliedVolatility"].median()),
            float(near["spread_pct"].median()), oi, len(near))


def _fetch_one(args: tuple[str, float]) -> tuple[str, dict]:
    ticker, spot = args
    out: dict = {"has_options": False}
    try:
        tk = yf.Ticker(ticker)
        expiries = tk.options
        if not expiries:
            return ticker, out
        picked = _pick_expiry(expiries)
        if picked is None:
            return ticker, out
        expiry, dte = picked
        chain = tk.option_chain(expiry)

        civ, cspread, coi, cn = _atm_iv(chain.calls, spot)
        piv, pspread, poi, pn = _atm_iv(chain.puts, spot)
        ivs = [v for v in (civ, piv) if not np.isnan(v)]
        if not ivs:
            return ticker, out

        # Put-call parity says ATM call and put vols should be close. A wide
        # divergence means at least one side is a stale or nonsense quote.
        parity_ok = True
        if len(ivs) == 2:
            parity_ok = abs(civ - piv) / max(civ, piv) < 0.5

        out.update({
            "has_options": True,
            "expiry": expiry,
            "dte": dte,
            "atm_iv": float(np.mean(ivs)),
            "opt_spread_pct": float(np.nanmean([cspread, pspread])),
            "open_interest": coi + poi,
            "n_atm_strikes": cn + pn,
            "iv_quality_ok": bool(parity_ok and (cn + pn) >= MIN_ATM_STRIKES),
            "n_expiries": len(expiries),
        })
    except Exception:  # noqa: BLE001 - missing chains are normal, not fatal
        return ticker, out

    # Earnings inside the option's life changes everything about the trade.
    try:
        cal = yf.Ticker(ticker).calendar or {}
        dates = cal.get("Earnings Date") or []
        if dates:
            nxt = min(d for d in dates if isinstance(d, date))
            out["earnings_date"] = nxt.isoformat()
            out["days_to_earnings"] = (nxt - date.today()).days
    except Exception:  # noqa: BLE001
        pass
    return ticker, out


def _fetch_pass(args: list[tuple[str, float]], workers: int) -> tuple[dict, bool]:
    """One sweep over the candidates. Returns (rows, looked_throttled)."""
    rows: dict[str, dict] = {}
    consecutive_empty = 0
    throttled = False
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for ticker, data in pool.map(_fetch_one, args):
            rows[ticker] = data
            # A long unbroken run of failures means Yahoo is refusing us, not
            # that these stocks lack options. Stop rather than burn the run.
            if data.get("has_options"):
                consecutive_empty = 0
            else:
                consecutive_empty += 1
                if consecutive_empty >= 30:
                    throttled = True
                    break
    return rows, throttled


def fetch(candidates: pd.DataFrame, workers: int = 8) -> pd.DataFrame:
    """`candidates` must be indexed by ticker with `price` and vol columns."""
    if candidates.empty:
        return pd.DataFrame()
    args = [(t, float(p)) for t, p in candidates["price"].items()]
    print(f"  options: fetching chains for {len(args)} candidates...")
    t0 = time.time()

    rows, throttled = _fetch_pass(args, workers)

    # Zero chains across a set of screened, liquid names is not a real market
    # condition — it means we were refused. One patient retry is worth far
    # more than the seconds it costs, because IV drives the option suggestions.
    if throttled or not any(d.get("has_options") for d in rows.values()):
        print(f"  options: no chains returned, pausing "
              f"{C.OPTIONS_RETRY_PAUSE}s and retrying once...")
        time.sleep(C.OPTIONS_RETRY_PAUSE)
        retry_rows, throttled = _fetch_pass(args, max(2, workers // 3))
        for k, v in retry_rows.items():
            if v.get("has_options") or k not in rows:
                rows[k] = v

    if throttled:
        print("  options: rate limited — IV will be reported as unknown, "
              "and no volatility-based suggestions will be made")

    opt = pd.DataFrame.from_dict(rows, orient="index")
    if "has_options" not in opt.columns:
        opt["has_options"] = False
    opt["has_options"] = opt["has_options"].fillna(False)

    # Compare implied against delivered volatility. The reference is the
    # gap-resistant estimator when available, so one merger or FDA gap in the
    # history doesn't make ordinary option prices look like a bargain.
    ref = candidates.get("rvol_robust")
    blend = 0.5 * candidates["rvol_20"] + 0.5 * candidates["rvol_60"]
    ref_vol = blend if ref is None else pd.concat([ref, blend], axis=1).min(axis=1)
    opt["ref_vol"] = ref_vol.reindex(opt.index)
    if "atm_iv" not in opt.columns:
        opt["atm_iv"] = np.nan
    if "iv_quality_ok" not in opt.columns:
        opt["iv_quality_ok"] = False
    opt["iv_quality_ok"] = opt["iv_quality_ok"].fillna(False).astype(bool)
    opt["iv_rv"] = opt["atm_iv"] / opt["ref_vol"].replace(0, np.nan)

    # An extreme ratio is nearly always a data or one-off-event artifact
    # rather than a real edge, so it is refused rather than traded on.
    plausible = opt["iv_rv"].between(0.40, 3.0)
    reliable = opt["iv_quality_ok"] & plausible & opt["iv_rv"].notna()

    opt["iv_regime"] = np.select(
        [~opt["iv_rv"].notna(),
         ~reliable,
         opt["iv_rv"] >= C.IV_RV_RICH,
         opt["iv_rv"] <= C.IV_RV_CHEAP],
        ["unknown", "unreliable", "rich", "cheap"],
        default="fair",
    )

    n_ok = int(opt["has_options"].sum())
    n_rel = int(reliable.sum())
    print(f"  options: {n_ok}/{len(args)} have usable chains, "
          f"{n_rel} with reliable IV ({time.time() - t0:.0f}s)")
    return opt
