"""Tracks what happened to each suggestion after it was made.

A screener that never looks back is easy to fool yourself with. This records
every suggestion the day it fires and then follows it forward bar by bar until
it hits its stop, hits its target, or runs out of time.

Persistence is deliberately minimal. Only the *seed* of each trade is stored —
ticker, setup, the date it fired, the reference price and the ATR at the time.
Every bar, every exit and every statistic is recomputed from the price panels
on each run, so the journal cannot drift out of sync with the price history
the way an incrementally-appended file would.

## The simulation rules

These are stated here because they decide every number in the tab, and they
are also mirrored in the browser so the entry slider agrees with the server:

  entry      the open of the next session. The signal is computed after the
             close, so that is the first price you could realistically pay.
  stop       entry -/+ STOP_ATR_MULT x ATR, using the ATR as it was on the
             signal date, not today's.
  target     entry +/- TARGET_ATR_MULT x ATR.
  exit       the first bar whose low touches the stop or whose high touches
             the target. Fills are assumed *at* the level.
  same bar   if a bar's range covers both the stop and the target, the stop
             is taken. Daily bars cannot say which came first, and assuming
             the good one would quietly inflate every statistic here.
  timeout    after MAX_HOLD_DAYS sessions the trade is marked expired and
             marked out at the last close.

What this does not model: slippage, commission, the bid-ask spread, gaps
through the stop being filled worse than the stop, borrow cost on shorts, and
dividends. Real results would be worse than these, and the gap is not small
for the wider-spread names.
"""

from __future__ import annotations

import json
from datetime import datetime

import numpy as np
import pandas as pd

from . import config as C

SEED_PATH = C.CACHE_DIR / "trades.json"

TRACK_TOP_N = 25        # new trades recorded per run, best conviction first
MAX_HOLD_DAYS = 60      # sessions before a trade is timed out
RETENTION_DAYS = 200    # calendar days a closed trade stays in the journal


# ----------------------------------------------------------------- storage --
def load_seeds() -> list[dict]:
    if SEED_PATH.exists():
        try:
            data = json.loads(SEED_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            print("  trade journal corrupt, starting fresh")
    return []


def save_seeds(seeds: list[dict]) -> None:
    SEED_PATH.write_text(json.dumps(seeds, indent=0, sort_keys=True),
                         encoding="utf-8")


def _seed_id(ticker: str, setup: str, date: str) -> str:
    return f"{ticker}|{setup}|{date}"


# --------------------------------------------------------------- recording --
def record(seeds: list[dict], opportunities: list[dict], as_of: str,
           top_n: int = TRACK_TOP_N) -> int:
    """Append today's best suggestions as new tracked trades.

    A ticker already being tracked in the same direction is skipped — the
    same trend re-firing for six sessions running is one idea, not six, and
    counting it six times would distort every statistic.
    """
    existing = {s["id"] for s in seeds}
    open_keys = {(s["ticker"], s["direction"]) for s in seeds
                 if s.get("_status", "open") == "open"}

    added = 0
    for o in sorted(opportunities, key=lambda x: -x["conviction"]):
        if added >= top_n:
            break
        setup = o["setups"][0]["id"]
        sid = _seed_id(o["ticker"], setup, as_of)
        if sid in existing:
            continue
        if (o["ticker"], o["direction"]) in open_keys:
            continue

        atr = o["levels"].get("atr")
        if not atr or atr <= 0:
            continue

        seeds.append({
            "id": sid,
            "ticker": o["ticker"],
            "name": o["name"],
            "theme": o["theme"],
            "theme_label": o["theme_label"],
            "direction": o["direction"],
            "setup": setup,
            "setup_label": o["setups"][0]["label"],
            "suggested_on": as_of,
            "conviction": o["conviction"],
            "signal_price": o["levels"]["entry"],
            "atr": atr,
        })
        existing.add(sid)
        open_keys.add((o["ticker"], o["direction"]))
        added += 1
    return added


def prune(seeds: list[dict], today: pd.Timestamp) -> int:
    """Drop trades old enough that they are certainly closed and stale."""
    cutoff = today - pd.Timedelta(days=RETENTION_DAYS)
    keep = [s for s in seeds
            if pd.Timestamp(s["suggested_on"]) >= cutoff]
    removed = len(seeds) - len(keep)
    seeds[:] = keep
    return removed


# -------------------------------------------------------------- simulation --
def _last_finite(c: np.ndarray, upto: int, fallback: float) -> float:
    """Most recent real close at or before `upto`.

    A halted or thinly-traded name can carry a NaN on its final bar; marking
    the position at NaN would poison every statistic downstream and, worse,
    serialise as bare `NaN`, which is not valid JSON.
    """
    for k in range(min(upto, len(c) - 1), -1, -1):
        v = float(c[k])
        if np.isfinite(v):
            return v
    return float(fallback)


def json_safe(obj):
    """Recursively replace NaN/Inf with None so the output is valid JSON."""
    if isinstance(obj, dict):
        return {k: json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    if isinstance(obj, (np.floating, float)):
        f = float(obj)
        return None if (np.isnan(f) or np.isinf(f)) else f
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    return obj


def walk(o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray,
         entry_idx: int, direction: str, atr: float,
         max_hold: int = MAX_HOLD_DAYS) -> dict:
    """Simulate one trade entered at the open of `entry_idx`.

    Mirrored by `simulateTrade` in docs/app.js — keep the two in step.
    """
    n = len(c)
    if entry_idx >= n:
        return {"status": "pending"}

    entry = float(o[entry_idx])
    if not np.isfinite(entry) or entry <= 0:
        return {"status": "pending"}

    long = direction == "long"
    stop = entry - C.STOP_ATR_MULT * atr if long else entry + C.STOP_ATR_MULT * atr
    target = (entry + C.TARGET_ATR_MULT * atr if long
              else entry - C.TARGET_ATR_MULT * atr)
    risk = abs(entry - stop)

    last = min(n - 1, entry_idx + max_hold)
    mfe = mae = 0.0
    status, exit_idx, exit_price = "open", None, None

    for j in range(entry_idx, last + 1):
        hi, lo = float(h[j]), float(l[j])
        if not (np.isfinite(hi) and np.isfinite(lo)):
            continue

        fav = (hi - entry) if long else (entry - lo)
        adv = (entry - lo) if long else (hi - entry)
        mfe = max(mfe, fav)
        mae = max(mae, adv)

        hit_stop = lo <= stop if long else hi >= stop
        hit_target = hi >= target if long else lo <= target

        if hit_stop and hit_target:
            # Ambiguous within a daily bar. Assume the stop — see module docs.
            status, exit_idx, exit_price = "stop", j, stop
            break
        if hit_stop:
            status, exit_idx, exit_price = "stop", j, stop
            break
        if hit_target:
            status, exit_idx, exit_price = "target", j, target
            break

    if status == "open" and last == entry_idx + max_hold and last < n - 1:
        status, exit_idx, exit_price = "expired", last, _last_finite(c, last, entry)

    mark = exit_price if exit_price is not None else _last_finite(c, n - 1, entry)
    pnl = (mark - entry) if long else (entry - mark)

    return {
        "status": status,
        "entry_idx": entry_idx,
        "entry": round(entry, 4),
        "stop": round(stop, 4),
        "target": round(target, 4),
        "exit_idx": exit_idx,
        "exit_price": None if exit_price is None else round(exit_price, 4),
        "mark": round(mark, 4),
        "pnl_pct": round(pnl / entry * 100, 3),
        "r_multiple": round(pnl / risk, 3) if risk > 0 else None,
        "mfe_r": round(mfe / risk, 3) if risk > 0 else None,
        "mae_r": round(-mae / risk, 3) if risk > 0 else None,
        "bars_held": (exit_idx - entry_idx) if exit_idx is not None
                     else (n - 1 - entry_idx),
    }


def _series(panels: dict[str, pd.DataFrame], ticker: str, start: str):
    """OHLC arrays for one ticker from its signal date onward."""
    close = panels["close"]
    if ticker not in close.columns:
        return None
    idx = close.index
    start_ts = pd.Timestamp(start)
    pos = idx.searchsorted(start_ts)
    if pos >= len(idx):
        return None
    sl = slice(pos, min(len(idx), pos + MAX_HOLD_DAYS + 3))
    dates = [str(d.date()) for d in idx[sl]]
    out = {}
    for k in ("open", "high", "low", "close"):
        out[k] = panels[k][ticker].to_numpy()[sl]
    if len(dates) < 2 or not np.isfinite(out["close"][0]):
        return None
    return dates, out


def build(seeds: list[dict], panels: dict[str, pd.DataFrame],
          as_of: str) -> dict:
    """Recompute every tracked trade from the current price panels."""
    trades = []
    for s in seeds:
        got = _series(panels, s["ticker"], s["suggested_on"])
        if got is None:
            continue
        dates, arr = got
        sim = walk(arr["open"], arr["high"], arr["low"], arr["close"],
                   entry_idx=1, direction=s["direction"], atr=float(s["atr"]))
        if sim["status"] == "pending":
            continue

        s["_status"] = sim["status"]
        trades.append({
            **{k: s[k] for k in ("id", "ticker", "name", "theme", "theme_label",
                                 "direction", "setup", "setup_label",
                                 "suggested_on", "conviction")},
            "atr": round(float(s["atr"]), 4),
            "signal_price": s["signal_price"],
            "dates": dates,
            "o": [None if not np.isfinite(v) else round(float(v), 4) for v in arr["open"]],
            "h": [None if not np.isfinite(v) else round(float(v), 4) for v in arr["high"]],
            "l": [None if not np.isfinite(v) else round(float(v), 4) for v in arr["low"]],
            "c": [None if not np.isfinite(v) else round(float(v), 4) for v in arr["close"]],
            "sim": sim,
        })

    trades.sort(key=lambda t: (t["suggested_on"], -t["conviction"]), reverse=True)
    return json_safe({
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "as_of_date": as_of,
        "rules": {
            "entry": "open of the session after the signal",
            "stop_atr_mult": C.STOP_ATR_MULT,
            "target_atr_mult": C.TARGET_ATR_MULT,
            "max_hold_days": MAX_HOLD_DAYS,
            "same_bar": "stop assumed to fill before target",
            "costs": "no slippage, commission, spread or borrow modelled",
        },
        "stats": summarise(trades),
        "trades": trades,
    })


def summarise(trades: list[dict]) -> dict:
    closed = [t for t in trades if t["sim"]["status"] in ("stop", "target", "expired")]
    wins = [t for t in closed if (t["sim"]["r_multiple"] or 0) > 0]
    rs = [t["sim"]["r_multiple"] for t in closed
          if t["sim"]["r_multiple"] is not None]

    def _stat(vals, fn):
        return round(float(fn(vals)), 3) if vals else None

    by_setup: dict[str, dict] = {}
    for t in closed:
        b = by_setup.setdefault(t["setup"], {"label": t["setup_label"],
                                             "n": 0, "wins": 0, "r": []})
        b["n"] += 1
        r = t["sim"]["r_multiple"]
        if r is not None:
            b["r"].append(r)
            if r > 0:
                b["wins"] += 1
    for b in by_setup.values():
        b["avg_r"] = _stat(b["r"], np.mean)
        b["win_rate"] = round(b["wins"] / b["n"], 3) if b["n"] else None
        del b["r"]

    return {
        "total": len(trades),
        "open": sum(1 for t in trades if t["sim"]["status"] == "open"),
        "closed": len(closed),
        "target_hit": sum(1 for t in closed if t["sim"]["status"] == "target"),
        "stopped": sum(1 for t in closed if t["sim"]["status"] == "stop"),
        "expired": sum(1 for t in closed if t["sim"]["status"] == "expired"),
        "win_rate": round(len(wins) / len(closed), 3) if closed else None,
        "avg_r": _stat(rs, np.mean),
        "median_r": _stat(rs, np.median),
        "total_r": _stat(rs, np.sum),
        "by_setup": by_setup,
    }
