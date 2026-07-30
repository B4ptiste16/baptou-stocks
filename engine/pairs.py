"""Market-neutral pair setups within a theme.

Two stocks driven by the same theme tend to move together. When the spread
between them stretches far from its own history, the mean-reverting trade is
to buy the cheap leg and sell the rich one — a position that is largely
indifferent to what the market or the sector does.

Quality here rests on three numbers, all reported:

  corr        do these two actually move together at all
  z           how many standard deviations the spread has stretched
  half-life   how fast the spread has historically closed, from the AR(1)
              coefficient. A spread that has never mean-reverted has an
              enormous half-life, and a wide z on it means nothing.

The hedge ratio is an OLS beta on log prices rather than a naive 1:1 ratio,
so the two legs are actually sized against each other.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import config as C

MAX_PER_THEME = 40      # most liquid names considered per theme
MIN_MEMBERS = 6
HALF_LIFE_MIN = 3.0
HALF_LIFE_MAX = 60.0


def _half_life(spread: np.ndarray) -> float:
    """AR(1) mean-reversion half-life in trading days."""
    s = spread[~np.isnan(spread)]
    if len(s) < 30:
        return np.inf
    lag, cur = s[:-1], s[1:]
    lag_c = lag - lag.mean()
    denom = float((lag_c ** 2).sum())
    if denom <= 0:
        return np.inf
    phi = float((lag_c * (cur - cur.mean())).sum() / denom)
    if phi <= 0 or phi >= 1:
        return np.inf
    return float(-np.log(2) / np.log(phi))


def sector_relative(feat: pd.DataFrame, theme_map: pd.Series) -> pd.DataFrame:
    """Per-stock z-score of 21-day return against its own theme."""
    df = feat[["ret_21d", "ret_63d"]].copy()
    df["theme"] = theme_map.reindex(df.index)
    df = df.dropna(subset=["theme", "ret_21d"])

    grp = df.groupby("theme")["ret_21d"]
    med = grp.transform("median")
    std = grp.transform("std").replace(0, np.nan)
    n = grp.transform("size")

    df["theme_median_21d"] = med
    df["rel_z"] = (df["ret_21d"] - med) / std
    df = df[n >= MIN_MEMBERS]
    return df[["theme", "ret_21d", "theme_median_21d", "rel_z"]]


def find_pairs(close: pd.DataFrame, feat: pd.DataFrame,
               theme_map: pd.Series) -> pd.DataFrame:
    """Scan every theme for stretched, well-behaved spreads."""
    logp_all = np.log(close.tail(C.PAIR_LOOKBACK).ffill())
    rets_all = logp_all.diff()
    rows: list[dict] = []

    themes = theme_map.reindex(feat.index).dropna()
    for theme, members in themes.groupby(themes):
        tickers = list(members.index)
        if len(tickers) < MIN_MEMBERS:
            continue
        # Most liquid names only — pair trading illiquid stock is a bad idea
        # and the combinatorics explode.
        liquid = (feat.loc[tickers, "dollar_vol"]
                  .nlargest(MAX_PER_THEME).index.tolist())
        cols = [t for t in liquid if t in logp_all.columns]
        if len(cols) < 2:
            continue

        logp = logp_all[cols].dropna(axis=1, thresh=int(C.PAIR_LOOKBACK * 0.9))
        cols = list(logp.columns)
        if len(cols) < 2:
            continue
        rets = rets_all[cols]

        corr = rets.corr()
        vals = logp.to_numpy()

        for i in range(len(cols)):
            for j in range(i + 1, len(cols)):
                c = corr.iat[i, j]
                if not np.isfinite(c) or c < C.PAIR_MIN_CORR:
                    continue
                a, b = vals[:, i], vals[:, j]
                ok = ~(np.isnan(a) | np.isnan(b))
                if ok.sum() < C.PAIR_LOOKBACK * 0.8:
                    continue
                av, bv = a[ok], b[ok]

                # OLS hedge ratio: a ~ alpha + beta * b
                bc = bv - bv.mean()
                var_b = float((bc ** 2).sum())
                if var_b <= 0:
                    continue
                beta = float((bc * (av - av.mean())).sum() / var_b)
                if not (0.2 < beta < 5.0):
                    continue

                spread = av - beta * bv
                sd = spread.std()
                if sd <= 0:
                    continue
                z = float((spread[-1] - spread.mean()) / sd)
                if abs(z) < C.PAIR_ENTRY_Z:
                    continue

                hl = _half_life(spread)
                if not (HALF_LIFE_MIN <= hl <= HALF_LIFE_MAX):
                    continue

                # z > 0 means leg A is rich relative to B: sell A, buy B.
                rich, cheap = (cols[i], cols[j]) if z > 0 else (cols[j], cols[i])
                rows.append({
                    "theme": theme,
                    "short_leg": rich,
                    "long_leg": cheap,
                    "z": round(z, 2),
                    "corr": round(float(c), 3),
                    "beta": round(beta, 3),
                    "half_life": round(hl, 1),
                    "score": round(min(100.0, abs(z) * 22 + float(c) * 25), 1),
                })

    if not rows:
        return pd.DataFrame(columns=["theme", "short_leg", "long_leg", "z",
                                     "corr", "beta", "half_life", "score"])
    out = pd.DataFrame(rows).sort_values("score", ascending=False)

    # One appearance per stock keeps the list from filling with variations of
    # the same dislocation.
    seen: set[str] = set()
    keep = []
    for _, r in out.iterrows():
        if r["short_leg"] in seen or r["long_leg"] in seen:
            continue
        seen.update([r["short_leg"], r["long_leg"]])
        keep.append(r)
    return pd.DataFrame(keep).reset_index(drop=True)
