"""Sanity-checks docs/data/latest.json before it gets committed.

The dashboard is only as trustworthy as this file, and a silently malformed
build is worse than a failed one — a failed job is visible, a published page
full of nonsense is not. This runs between the build and the commit, and a
non-zero exit stops the data being pushed.

Checks are structural and internal-consistency only; they cannot tell you
whether a signal is any *good*, just that the file says what it claims to.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

DATA = Path("docs/data/latest.json")

TOP_KEYS = {"generated_at", "as_of_date", "stats", "market", "themes",
            "opportunities", "pairs"}
OPP_KEYS = {"ticker", "name", "theme", "theme_label", "direction",
            "conviction", "setups", "levels", "actions", "metrics"}

errors: list[str] = []
warnings: list[str] = []


def err(msg: str) -> None:
    errors.append(msg)


def warn(msg: str) -> None:
    warnings.append(msg)


def check_levels(o: dict) -> None:
    L, t, d = o["levels"], o["ticker"], o["direction"]
    entry, stop, target = L.get("entry"), L.get("stop"), L.get("target")
    if None in (entry, stop, target):
        err(f"{t}: missing level values")
        return
    if entry <= 0:
        err(f"{t}: non-positive entry {entry}")
    if d == "long" and not (stop < entry < target):
        err(f"{t}: long levels out of order — stop {stop}, entry {entry}, "
            f"target {target}")
    if d == "short" and not (target < entry < stop):
        err(f"{t}: short levels out of order — target {target}, entry {entry}, "
            f"stop {stop}")
    rr = L.get("risk_reward")
    if rr is not None and rr < 1.0:
        err(f"{t}: reward-to-risk {rr} below 1.0 should have been filtered")


JOURNAL = Path("docs/data/journal.json")
STATUSES = {"open", "target", "stop", "expired"}


def check_journal() -> None:
    """The follow-up tab is a performance claim, so its arithmetic has to hold.

    The important check is that every reported R-multiple is reproducible from
    the entry, stop and exit stored alongside it. If the simulation drifts,
    this catches it before the numbers reach a page that looks authoritative.
    """
    if not JOURNAL.exists():
        warn("no journal.json yet — the follow-up tab will be empty")
        return
    try:
        j = json.loads(JOURNAL.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        err(f"journal.json is not valid JSON — {exc}")
        return

    rules = j.get("rules") or {}
    stop_mult = rules.get("stop_atr_mult")
    tgt_mult = rules.get("target_atr_mult")
    if not stop_mult or not tgt_mult:
        err("journal rules missing ATR multiples")
        return

    trades = j.get("trades", [])
    counts = {k: 0 for k in STATUSES}

    for t in trades:
        tid = t.get("id", "?")
        sim = t.get("sim") or {}
        st = sim.get("status")
        if st not in STATUSES:
            err(f"journal {tid}: bad status {st!r}")
            continue
        counts[st] += 1

        n = len(t.get("c", []))
        for k in ("o", "h", "l", "dates"):
            if len(t.get(k, [])) != n:
                err(f"journal {tid}: {k} length {len(t.get(k, []))} != c length {n}")

        ei = sim.get("entry_idx")
        if ei is None or not (0 <= ei < n):
            err(f"journal {tid}: entry_idx {ei} outside 0..{n - 1}")
            continue
        if ei == 0:
            err(f"journal {tid}: entered on the signal bar itself (lookahead)")

        entry, stop, target = sim.get("entry"), sim.get("stop"), sim.get("target")
        atr = t.get("atr")
        if None in (entry, stop, target, atr):
            err(f"journal {tid}: missing entry/stop/target/atr")
            continue

        # Levels must sit exactly the configured ATR distance from entry.
        long = t["direction"] == "long"
        want_stop = entry - stop_mult * atr if long else entry + stop_mult * atr
        want_tgt = entry + tgt_mult * atr if long else entry - tgt_mult * atr
        if abs(stop - want_stop) > 0.011:
            err(f"journal {tid}: stop {stop} != entry -/+ {stop_mult}xATR ({want_stop:.4f})")
        if abs(target - want_tgt) > 0.011:
            err(f"journal {tid}: target {target} != {tgt_mult}xATR ({want_tgt:.4f})")

        # R-multiple must be reproducible from the stored prices.
        mark = sim.get("exit_price")
        if mark is None:
            mark = sim.get("mark")
        r = sim.get("r_multiple")
        risk = abs(entry - stop)
        if r is not None and mark is not None and risk > 0:
            pnl = (mark - entry) if long else (entry - mark)
            if abs(pnl / risk - r) > 0.011:
                err(f"journal {tid}: r_multiple {r} not reproducible from "
                    f"entry {entry} / exit {mark} (expected {pnl / risk:.3f})")

        ex = sim.get("exit_idx")
        if st == "open" and ex is not None:
            err(f"journal {tid}: status open but has exit_idx {ex}")
        if st != "open" and ex is None:
            err(f"journal {tid}: status {st} but no exit_idx")
        if ex is not None and ex < ei:
            err(f"journal {tid}: exit_idx {ex} before entry_idx {ei}")

    stats = j.get("stats") or {}
    if stats.get("total") != len(trades):
        err(f"journal stats total {stats.get('total')} != {len(trades)} trades")
    for key, st in (("open", "open"), ("target_hit", "target"),
                    ("stopped", "stop"), ("expired", "expired")):
        if stats.get(key) is not None and stats[key] != counts[st]:
            err(f"journal stats {key}={stats[key]} but counted {counts[st]}")

    if trades:
        print(f"  journal: {len(trades)} trades checked "
              f"({counts['open']} open, {counts['target']} target, "
              f"{counts['stop']} stop, {counts['expired']} expired)")


def main() -> int:
    if not DATA.exists():
        print("FAIL: docs/data/latest.json does not exist")
        return 1

    try:
        d = json.loads(DATA.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"FAIL: not valid JSON — {exc}")
        return 1

    missing = TOP_KEYS - set(d)
    if missing:
        err(f"missing top-level keys: {sorted(missing)}")
        print("FAIL:", errors[0])
        return 1

    opps = d["opportunities"]
    if not isinstance(opps, list):
        print("FAIL: opportunities is not a list")
        return 1

    # An empty result is legitimate (a quiet tape), but worth flagging loudly
    # because it also looks exactly like a broken screen.
    if not opps:
        warn("no opportunities in this build — verify this is a real quiet "
             "market and not a broken filter")

    seen: set[str] = set()
    for o in opps:
        miss = OPP_KEYS - set(o)
        if miss:
            err(f"{o.get('ticker', '?')}: missing keys {sorted(miss)}")
            continue

        t = o["ticker"]
        if t in seen:
            err(f"{t}: duplicated in opportunities")
        seen.add(t)

        if o["direction"] not in ("long", "short"):
            err(f"{t}: bad direction {o['direction']!r}")
        if not 0 <= o["conviction"] <= 100:
            err(f"{t}: conviction {o['conviction']} out of range")
        if not o["setups"]:
            err(f"{t}: no setups attached")
        if not o["actions"]:
            err(f"{t}: no actions attached")

        for s in o["setups"]:
            if not 0 <= s.get("score", -1) <= 100:
                err(f"{t}: setup {s.get('id')} score {s.get('score')} out of range")
            if not s.get("thesis") or not s.get("invalidation"):
                err(f"{t}: setup {s.get('id')} missing thesis/invalidation")

        for a in o["actions"]:
            if not a.get("label") or not a.get("rationale"):
                err(f"{t}: action {a.get('type')} missing label/rationale")

        # Overlapping branches in the action mapper can emit the same
        # instrument twice; the card would show it as two separate ideas.
        kinds = [a["type"] for a in o["actions"]]
        dupes = {k for k in kinds if kinds.count(k) > 1}
        if dupes:
            err(f"{t}: duplicate action types {sorted(dupes)}")

        # An IV-based recommendation must not exist without a trusted reading.
        iv_regime = o["metrics"].get("iv_regime")
        premium = {"bull_put_spread", "bear_call_spread", "long_call", "long_put"}
        for a in o["actions"]:
            if a["type"] in premium and a["type"] != "long_put":
                if iv_regime not in ("cheap", "fair", "rich"):
                    err(f"{t}: {a['type']} suggested with iv_regime="
                        f"{iv_regime!r}")

        check_levels(o)

    for p in d["pairs"]:
        if p["long_leg"] == p["short_leg"]:
            err(f"pair {p['long_leg']} has identical legs")
        if abs(p.get("z", 0)) < 1.0:
            err(f"pair {p['long_leg']}/{p['short_leg']}: z={p.get('z')} too small")

    s = d["stats"]
    if s["liquid"] > s["priced"]:
        err(f"liquid ({s['liquid']}) exceeds priced ({s['priced']})")
    if len(opps) != s["opportunities"]:
        err(f"stats say {s['opportunities']} opportunities, file has {len(opps)}")

    check_journal()

    size_kb = DATA.stat().st_size / 1024
    if size_kb > 8000:
        warn(f"output is {size_kb:.0f} KB — large for a static page load")

    for w in warnings:
        print(f"WARN: {w}")
    for e in errors[:25]:
        print(f"FAIL: {e}")
    if len(errors) > 25:
        print(f"... and {len(errors) - 25} more")

    if errors:
        print(f"\n{len(errors)} validation error(s) — refusing to publish.")
        return 1

    print(f"OK: {len(opps)} opportunities, {len(d['pairs'])} pairs, "
          f"{size_kb:.0f} KB, {len(warnings)} warning(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
