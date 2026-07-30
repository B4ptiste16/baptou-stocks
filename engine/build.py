"""Orchestrator: universe -> prices -> features -> setups -> actions -> JSON.

Run with `python -m engine.build`. Add `--cached` to reuse the last price
download (fast local iteration on the rule logic), `--limit N` to work on a
slice of the universe.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from . import actions as A
from . import config as C
from . import indicators as I
from . import options as O
from . import pairs as PR
from . import prices as P
from . import profiles as PF
from . import signals as S
from . import themes as TH
from . import universe as U


def _clean(v):
    """JSON-safe scalar conversion (NaN/Inf -> None, numpy -> python)."""
    if v is None:
        return None
    if isinstance(v, (np.bool_, bool)):
        return bool(v)
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating, float)):
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else round(f, 4)
    if isinstance(v, (np.str_, str)):
        return str(v)
    return v


def _metrics(row: pd.Series, opt: pd.Series | None) -> dict:
    keys = ["rsi", "adx", "atr_pct", "rs_21d", "rs_63d", "rs_126d",
            "pct_from_high", "pct_from_low", "ret_1d", "ret_5d", "ret_21d",
            "ret_63d", "ret_252d", "dollar_vol", "vol_surge", "rvol_20",
            "bb_pctb", "bb_width_pctile", "dist_sma50_atr", "dist_sma200_atr",
            "market_cap"]
    m = {k: _clean(row.get(k)) for k in keys}
    if opt is not None:
        for k in ("atm_iv", "iv_rv", "iv_regime", "dte", "expiry",
                  "days_to_earnings", "earnings_date", "open_interest",
                  "opt_spread_pct", "has_options"):
            m[k] = _clean(opt.get(k))
    return m


def build(limit: int | None = None, use_cache: bool = False,
          skip_options: bool = False) -> dict:
    t_start = time.time()

    # -------------------------------------------------------- 1. universe --
    print("[1/8] loading universe...")
    stocks, _etfs = U.load_universe()
    tickers = list(stocks.index)
    if limit:
        tickers = tickers[:limit]
    print(f"  {len(tickers)} listed common stocks")

    # ---------------------------------------------------------- 2. prices --
    print("[2/8] fetching prices...")
    panels = P.load_panels() if use_cache else None
    if panels is None:
        need = sorted(set(tickers) | {C.BENCHMARK})
        panels = P.fetch_panels(need)
        P.save_panels(panels)
    else:
        print(f"  reusing cached panels ({panels['close'].shape[1]} tickers)")

    close_all = panels["close"]
    if C.BENCHMARK not in close_all.columns:
        raise RuntimeError(f"benchmark {C.BENCHMARK} missing from download")
    bench = close_all[C.BENCHMARK]
    for k in panels:
        panels[k] = panels[k].drop(columns=[C.BENCHMARK], errors="ignore")

    as_of = panels["close"].index[-1]

    # -------------------------------------------------------- 3. features --
    print("[3/8] computing indicators...")
    feat = I.build_features(panels, bench)
    liquid = I.apply_liquidity_filter(feat)
    print(f"  {len(feat)} priced -> {len(liquid)} pass liquidity filter")

    breadth = float(liquid["above_sma200"].mean()) if len(liquid) else float("nan")

    # --------------------------------------------------------- 4. setups --
    print("[4/8] evaluating setups...")
    hits = S.run_all(liquid)
    print(f"  {len(hits)} setup hits on {hits['ticker'].nunique()} tickers")
    if hits.empty:
        print("  nothing cleared the thresholds today")

    cand_tickers = sorted(hits["ticker"].unique()) if not hits.empty else []

    # ------------------------------------------------------- 5. profiles --
    # Candidates only. The universe-wide backfill happens at the very end,
    # because if Yahoo starts throttling it must not take the option chains
    # down with it — those are worth far more to the output.
    print("[5/8] refreshing candidate profiles...")
    cache = PF.load_cache()
    purged = PF.purge_suspect_misses(cache)
    if purged:
        print(f"  dropped {purged} unconfirmed cache misses for retry")
        PF.save_cache(cache)
    cache = PF.refresh(priority=cand_tickers, label="candidate profiles")

    prof = pd.DataFrame.from_dict(cache, orient="index")
    for col in ("sector", "industry", "short_name", "market_cap"):
        if col not in prof.columns:
            prof[col] = "" if col != "market_cap" else 0
    feat_names = stocks["name"].reindex(feat.index).fillna("")

    theme_map = pd.Series(
        {t: TH.classify(t,
                        name=str(feat_names.get(t, "")),
                        industry=str(prof["industry"].get(t, "")),
                        sector=str(prof["sector"].get(t, "")))
         for t in liquid.index},
        dtype="object")

    liquid = liquid.join(prof[["sector", "industry", "market_cap", "short_name"]],
                         how="left")
    liquid["market_cap"] = liquid["market_cap"].fillna(0)

    # -------------------------------------------------------- 6. options --
    print("[6/8] fetching option chains...")
    if hits.empty or skip_options:
        opt = pd.DataFrame()
    else:
        best = (hits.groupby("ticker")["score"].max()
                .nlargest(C.TOP_N_FOR_OPTIONS).index)
        eligible = liquid.loc[
            liquid.index.isin(best)
            & (liquid["dollar_vol"] >= C.LIQ_TIER_OPTIONS)]
        opt = O.fetch(eligible) if len(eligible) else pd.DataFrame()

    # ---------------------------------------------- 7. assemble output --
    print("[7/8] assembling opportunities...")
    opportunities = []
    for ticker, grp in (hits.groupby("ticker") if not hits.empty else []):
        if ticker not in liquid.index:
            continue
        row = liquid.loc[ticker]

        by_dir = grp.groupby("direction")["score"].agg(["max", "size"])
        direction = by_dir["max"].idxmax()
        conflicting = len(by_dir) > 1
        mine = grp[grp["direction"] == direction].sort_values("score",
                                                              ascending=False)

        opt_row = opt.loc[ticker] if (len(opt) and ticker in opt.index) else None
        theme = theme_map.get(ticker, "other")
        levels = A.compute_levels(row, direction)
        if levels["risk_reward"] is not None and \
                levels["risk_reward"] < C.MIN_RISK_REWARD:
            continue

        conv, conv_notes = A.conviction(row, float(mine["score"].iloc[0]),
                                        len(mine), opt_row)
        if conflicting:
            conv -= 6
            conv_notes.append("Long and short setups both present — the "
                              "signals disagree (-6)")

        setups_out = []
        for _, s in mine.iterrows():
            spec = S.SETUP_BY_ID[s["setup"]]
            setups_out.append({
                "id": s["setup"],
                "label": s["setup_label"],
                "score": _clean(s["score"]),
                "thesis": spec.thesis,
                "invalidation": spec.invalidation,
                "evidence": spec.describe(row),
            })

        opportunities.append({
            "ticker": ticker,
            "name": str(row.get("short_name") or feat_names.get(ticker, "") or ticker),
            "theme": theme,
            "theme_label": TH.label(theme),
            "sector": str(row.get("sector") or ""),
            "industry": str(row.get("industry") or ""),
            "cap_bucket": PF.cap_bucket(float(row.get("market_cap") or 0)),
            "price": _clean(row["price"]),
            "change_1d": _clean(row.get("ret_1d")),
            "direction": direction,
            "conviction": round(float(conv), 1),
            "conviction_notes": conv_notes,
            "setups": setups_out,
            "levels": levels,
            "actions": A.build_actions(row, mine["setup"].iloc[0], direction,
                                       levels, opt_row, theme),
            "metrics": _metrics(row, opt_row),
        })

    opportunities.sort(key=lambda o: -o["conviction"])

    # Cap per theme so one crowded sector can't swamp the dashboard.
    per_theme: dict[str, int] = {}
    trimmed = []
    for o in opportunities:
        n = per_theme.get(o["theme"], 0)
        if n >= C.MAX_PER_CATEGORY:
            continue
        per_theme[o["theme"]] = n + 1
        trimmed.append(o)
    opportunities = trimmed

    # ---------------------------------------------------------- 8. pairs --
    print("[8/8] scanning pairs...")
    pair_df = PR.find_pairs(panels["close"], liquid, theme_map)
    name_of = liquid.get("short_name", pd.Series(dtype=object))
    pairs_out = []
    for _, r in pair_df.head(40).iterrows():
        pairs_out.append({
            "theme": r["theme"],
            "theme_label": TH.label(r["theme"]),
            "long_leg": r["long_leg"],
            "long_name": str(name_of.get(r["long_leg"], "") or r["long_leg"]),
            "short_leg": r["short_leg"],
            "short_name": str(name_of.get(r["short_leg"], "") or r["short_leg"]),
            "z": _clean(r["z"]),
            "corr": _clean(r["corr"]),
            "beta": _clean(r["beta"]),
            "half_life": _clean(r["half_life"]),
            "score": _clean(r["score"]),
        })
    print(f"  {len(pairs_out)} pair setups")

    # Opportunistic universe backfill, last so that being throttled here costs
    # nothing but a slower-filling cache on future runs.
    print("[+] backfilling profile cache...")
    # Most-liquid-first, so the names a user is actually likely to see get
    # classified in the earliest runs rather than whatever starts with "A".
    by_liquidity = liquid["dollar_vol"].sort_values(ascending=False).index.tolist()
    PF.refresh(priority=[], backfill=by_liquidity, label="backfill")

    # Theme summary
    theme_rows = []
    counts = pd.Series([o["theme"] for o in opportunities]).value_counts()
    for theme_id in counts.index:
        members = liquid.index[theme_map.reindex(liquid.index) == theme_id]
        theme_rows.append({
            "id": theme_id,
            "label": TH.label(theme_id),
            "order": TH.order(theme_id),
            "opportunities": int(counts[theme_id]),
            "long": sum(1 for o in opportunities
                        if o["theme"] == theme_id and o["direction"] == "long"),
            "short": sum(1 for o in opportunities
                         if o["theme"] == theme_id and o["direction"] == "short"),
            "universe_size": int(len(members)),
            "median_ret_21d": _clean(liquid.loc[members, "ret_21d"].median())
            if len(members) else None,
        })
    theme_rows.sort(key=lambda t: t["order"])

    bench_ret_21 = float(bench.iloc[-1] / bench.iloc[-22] - 1) * 100
    regime = ("risk-on" if breadth > 0.60 else
              "risk-off" if breadth < 0.40 else "mixed")

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "as_of_date": str(pd.Timestamp(as_of).date()),
        "stats": {
            "universe": len(tickers),
            "priced": int(len(feat)),
            "liquid": int(len(liquid)),
            "setup_hits": int(len(hits)),
            "opportunities": len(opportunities),
            "pairs": len(pairs_out),
            "build_seconds": round(time.time() - t_start, 1),
        },
        "market": {
            "benchmark": C.BENCHMARK,
            "benchmark_price": _clean(float(bench.iloc[-1])),
            "benchmark_ret_21d": _clean(bench_ret_21),
            "breadth_above_200dma": _clean(breadth),
            "regime": regime,
        },
        "themes": theme_rows,
        "opportunities": opportunities,
        "pairs": pairs_out,
    }
    return payload


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the opportunity feed")
    ap.add_argument("--limit", type=int, default=None,
                    help="only scan the first N tickers (testing)")
    ap.add_argument("--cached", action="store_true",
                    help="reuse the last price download")
    ap.add_argument("--skip-options", action="store_true",
                    help="skip option chain fetching")
    args = ap.parse_args()

    payload = build(limit=args.limit, use_cache=args.cached,
                    skip_options=args.skip_options)

    out = C.OUT_DIR / "latest.json"
    out.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    size_kb = out.stat().st_size / 1024
    print(f"\nwrote {out} ({size_kb:.0f} KB)")
    print(f"  {payload['stats']['opportunities']} opportunities, "
          f"{payload['stats']['pairs']} pairs, "
          f"regime={payload['market']['regime']}, "
          f"{payload['stats']['build_seconds']}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
