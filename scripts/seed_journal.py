"""Backfills the trade journal by replaying the screener over past sessions.

Without this the follow-up tab is empty until enough daily runs accumulate.
Rather than wait months, we re-run the *same* setup engine against the price
history truncated to each past date, which produces the suggestions the
screener would have made on that day.

## No lookahead

Every past session is evaluated on a panel sliced to that date, so an
indicator computed for 12 May sees nothing after 12 May. That is the property
that makes this meaningful rather than decorative.

## What is still biased

**Survivorship.** The universe is today's listed names. Anything delisted,
acquired or bankrupted between then and now is simply absent, and those are
disproportionately the losers. This flatters the record and there is no way
to fix it from a free data source.

**Liquidity is measured as of the replay date**, which is correct, but the
*universe membership* is not — a stock that only recently became liquid enough
to screen would not have been in the list back then.

**No option data.** Chains cannot be reconstructed historically, so seeded
trades carry no IV regime and their conviction omits the earnings penalty.

Treat seeded history as a sanity check on the rules, not as a track record.
Run with:  python -m scripts.seed_journal --days 60
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import config as C           # noqa: E402
from engine import indicators as I       # noqa: E402
from engine import journal as J          # noqa: E402
from engine import prices as P           # noqa: E402
from engine import profiles as PF        # noqa: E402
from engine import signals as S          # noqa: E402
from engine import themes as TH          # noqa: E402
from engine import universe as U         # noqa: E402
from engine.actions import conviction    # noqa: E402

# Calendar days before the same ticker+direction may be seeded again. Roughly
# the typical holding period, so a continuously-firing trend contributes one
# trade rather than one per session.
SEED_COOLDOWN_DAYS = 30


def replay_day(panels: dict[str, pd.DataFrame], bench: pd.Series,
               upto: pd.Timestamp, theme_map: pd.Series,
               names: pd.Series, top_n: int) -> list[dict]:
    """Suggestions the screener would have produced at `upto`'s close."""
    sliced = {k: v.loc[:upto] for k, v in panels.items()}
    if len(sliced["close"]) < C.MIN_HISTORY_BARS + 5:
        return []

    feat = I.build_features(sliced, bench.loc[:upto])
    liquid = I.apply_liquidity_filter(feat)
    if liquid.empty:
        return []

    hits = S.run_all(liquid)
    if hits.empty:
        return []

    day = str(upto.date())
    out: list[dict] = []
    for ticker, grp in hits.groupby("ticker"):
        row = liquid.loc[ticker]
        by_dir = grp.groupby("direction")["score"].max()
        direction = by_dir.idxmax()
        mine = grp[grp["direction"] == direction].sort_values("score",
                                                              ascending=False)

        atr = float(row["atr"])
        price = float(row["price"])
        if not atr or atr <= 0 or price <= 0:
            continue
        # Same reward-to-risk gate the live screener applies.
        if C.TARGET_ATR_MULT / C.STOP_ATR_MULT < C.MIN_RISK_REWARD:
            continue

        conv, _notes = conviction(row, float(mine["score"].iloc[0]),
                                  len(mine), None)
        if len(by_dir) > 1:
            conv -= 6

        theme = theme_map.get(ticker, "other")
        out.append({
            "id": f"{ticker}|{mine['setup'].iloc[0]}|{day}",
            "ticker": ticker,
            "name": str(names.get(ticker, ticker) or ticker),
            "theme": theme,
            "theme_label": TH.label(theme),
            "direction": direction,
            "setup": mine["setup"].iloc[0],
            "setup_label": mine["setup_label"].iloc[0],
            "suggested_on": day,
            "conviction": round(float(conv), 1),
            "signal_price": round(price, 2),
            "atr": round(atr, 4),
            "seeded": True,
        })

    out.sort(key=lambda x: -x["conviction"])
    return out[:top_n]


def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill the trade journal")
    ap.add_argument("--days", type=int, default=60,
                    help="trading sessions to replay (default 60)")
    ap.add_argument("--top", type=int, default=J.TRACK_TOP_N,
                    help="trades recorded per session")
    ap.add_argument("--reset", action="store_true",
                    help="discard the existing journal first")
    args = ap.parse_args()

    print("loading price panels...")
    panels = P.load_panels()
    if panels is None:
        print("No cached panels. Run `python -m engine.build` first so the "
              "price history exists locally.")
        return 1

    if C.BENCHMARK not in panels["close"].columns:
        print(f"benchmark {C.BENCHMARK} missing from cached panels; "
              "re-run the build")
        return 1
    bench = panels["close"][C.BENCHMARK]
    panels = {k: v.drop(columns=[C.BENCHMARK], errors="ignore")
              for k, v in panels.items()}

    print("classifying themes...")
    stocks, _ = U.load_universe()
    cache = PF.load_cache()
    prof = pd.DataFrame.from_dict(cache, orient="index")
    for col in ("sector", "industry"):
        if col not in prof.columns:
            prof[col] = ""
    prof = prof.fillna({"sector": "", "industry": ""})
    names = stocks["name"].reindex(panels["close"].columns).fillna("")
    theme_map = pd.Series(
        {t: TH.classify(t, name=str(names.get(t, "")),
                        industry=str(prof["industry"].get(t, "")),
                        sector=str(prof["sector"].get(t, "")))
         for t in panels["close"].columns}, dtype="object")

    seeds = [] if args.reset else J.load_seeds()
    known = {s["id"] for s in seeds}
    existing_before = len(seeds)

    idx = panels["close"].index
    # Leave the final session out: a trade suggested at the last close has no
    # next open to enter on yet.
    sessions = list(idx[-(args.days + 1):-1])
    print(f"replaying {len(sessions)} sessions "
          f"({sessions[0].date()} -> {sessions[-1].date()})...")

    t0 = time.time()
    added = 0
    for n, day in enumerate(sessions, 1):
        for s in replay_day(panels, bench, day, theme_map, names, args.top):
            if s["id"] in known:
                continue
            # The live recorder skips a ticker whose previous trade is still
            # open. Replaying, we don't know that without simulating, so a
            # fixed cooldown stands in: a trend that re-fires every session
            # for a month is one idea, and counting it thirty times would
            # make the statistics meaningless.
            if any(x["ticker"] == s["ticker"]
                   and x["direction"] == s["direction"]
                   and abs((pd.Timestamp(s["suggested_on"])
                            - pd.Timestamp(x["suggested_on"])).days)
                       < SEED_COOLDOWN_DAYS
                   for x in seeds):
                continue
            seeds.append(s)
            known.add(s["id"])
            added += 1
        if n % 10 == 0 or n == len(sessions):
            print(f"  {n}/{len(sessions)} sessions, {added} trades seeded "
                  f"({time.time() - t0:.0f}s)")

    J.save_seeds(seeds)
    print(f"\nseeded {added} trades ({existing_before} -> {len(seeds)} total)")
    print("Run `python -m engine.build --cached` to regenerate the journal.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
