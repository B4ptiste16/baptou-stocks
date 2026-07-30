"""Maps a detected setup onto a concrete way to express it.

The same chart pattern justifies very different instruments depending on
context, and the context is what this module encodes:

  liquidity      decides what is even executable. Options on a stock trading
                 $2m/day are a trap regardless of how good the chart looks.
  implied vol    decides whether you want to be buying or selling premium.
                 A great setup with rich options is often a premium *sale*.
  expected speed  a squeeze breakout resolves fast (convexity is worth
                 paying for); a trend pullback grinds (theta would eat you).
  borrow risk    shorting a small cap outright carries recall and fee risk
                 that a put simply does not have.
  earnings       a binary event inside the holding period changes the trade
                 from a chart bet into a coin flip.

Everything returned is a *candidate expression* with its reasoning and its
warnings attached, not a recommendation.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import config as C
from . import leverage
from .profiles import cap_bucket

# Setups whose thesis is a fast resolution — convexity is worth paying for.
FAST_SETUPS = {"breakout_52w", "squeeze_breakout", "breakdown_52w",
               "parabolic_fade", "exhaustion"}
# Setups that grind out over weeks — long options bleed theta here.
SLOW_SETUPS = {"trend_pullback", "momentum_leader", "momentum_laggard",
               "golden_cross", "death_cross", "trend_breakdown"}


def _round_strike(x: float) -> float:
    """Snap to a plausible listed strike increment."""
    if x >= 200:
        return round(x / 5) * 5
    if x >= 50:
        return round(x)
    if x >= 20:
        return round(x * 2) / 2
    return round(x * 2) / 2


def compute_levels(row: pd.Series, direction: str) -> dict:
    price, atr = float(row["price"]), float(row["atr"])
    if direction == "long":
        stop = price - C.STOP_ATR_MULT * atr
        target = price + C.TARGET_ATR_MULT * atr
        # A 52-week high overhead is real resistance; don't claim a target
        # beyond it without acknowledging the level.
        resistance = float(row.get("high_52w", np.nan))
    else:
        stop = price + C.STOP_ATR_MULT * atr
        target = price - C.TARGET_ATR_MULT * atr
        resistance = float(row.get("low_52w", np.nan))

    risk = abs(price - stop)
    reward = abs(target - price)
    return {
        "entry": round(price, 2),
        "stop": round(stop, 2),
        "target": round(target, 2),
        "risk_pct": round(risk / price * 100, 2),
        "reward_pct": round(reward / price * 100, 2),
        "risk_reward": round(reward / risk, 2) if risk > 0 else None,
        "atr": round(atr, 2),
        "atr_pct": round(float(row["atr_pct"]) * 100, 2),
        "key_level": round(resistance, 2) if np.isfinite(resistance) else None,
    }


def _vol_band(atr_pct: float) -> str:
    if atr_pct < C.VOL_LOW:
        return "low"
    if atr_pct > C.VOL_HIGH:
        return "high"
    return "medium"


def build_actions(row: pd.Series, setup_id: str, direction: str,
                  levels: dict, opt: pd.Series | None, theme: str) -> list[dict]:
    """Ranked list of ways to express one setup on one stock."""
    actions: list[dict] = []
    price = float(row["price"])
    atr = float(row["atr"])
    dvol = float(row.get("dollar_vol", 0) or 0)
    adx = float(row.get("adx", 0) or 0)
    mcap = float(row.get("market_cap", 0) or 0)
    bucket = cap_bucket(mcap)
    vol_band = _vol_band(float(row["atr_pct"]))
    is_long = direction == "long"

    has_opt = bool(opt is not None and opt.get("has_options", False))
    iv_regime = str(opt.get("iv_regime", "unknown")) if opt is not None else "unknown"
    iv_rv = float(opt.get("iv_rv", np.nan)) if opt is not None else np.nan
    dte = int(opt.get("dte", 0)) if has_opt else 0
    expiry = opt.get("expiry") if has_opt else None
    dte_earn = opt.get("days_to_earnings") if opt is not None else None
    dte_earn = int(dte_earn) if dte_earn is not None and np.isfinite(dte_earn) else None
    earnings_inside = dte_earn is not None and 0 <= dte_earn <= max(dte, C.EARNINGS_SOON_DAYS)

    options_ok = has_opt and dvol >= C.LIQ_TIER_OPTIONS
    leverage_ok = dvol >= C.LIQ_TIER_LEVERAGE and adx >= 25 and vol_band != "high"
    fast = setup_id in FAST_SETUPS

    # ------------------------------------------------------------- equity --
    equity_warn = []
    if vol_band == "high":
        equity_warn.append(
            f"High volatility ({levels['atr_pct']:.1f}% daily ATR) — a normal "
            "position size here carries an outsized dollar risk.")
    if not is_long and bucket in ("small", "micro"):
        equity_warn.append(
            "Small-cap short: borrow may be expensive or unavailable, and a "
            "recall can force you out at the worst moment.")
    if earnings_inside:
        equity_warn.append(
            f"Earnings in {dte_earn} days — a gap can jump straight through "
            "your stop.")

    actions.append({
        "type": "long" if is_long else "short",
        "label": "Long stock" if is_long else "Short stock",
        "instrument": row.name,
        "rationale": (
            f"Straight directional exposure. Risk is defined by the "
            f"{C.STOP_ATR_MULT:.0f}-ATR stop at ${levels['stop']}, "
            f"{levels['risk_pct']:.1f}% away."),
        "warnings": equity_warn,
        "priority": 50,
    })

    # ------------------------------------------------------------ options --
    if options_ok:
        atm = _round_strike(price)
        wide = _round_strike(price + (2.5 * atr if is_long else -2.5 * atr))
        short_strike = _round_strike(price - 2 * atr if is_long else price + 2 * atr)
        far_strike = _round_strike(price - 4 * atr if is_long else price + 4 * atr)
        exp_txt = f"{expiry} ({dte}d)"
        iv_txt = (f"ATM IV {opt['atm_iv'] * 100:.0f}% vs {opt['ref_vol'] * 100:.0f}% "
                  f"realised (ratio {iv_rv:.2f})") if np.isfinite(iv_rv) else ""

        iv_trusted = iv_regime in ("cheap", "fair", "rich")

        if not iv_trusted and fast:
            # We still like the setup, but we cannot claim anything about
            # whether the options are cheap or expensive.
            actions.append({
                "type": "debit_spread",
                "label": (f"{'Call' if is_long else 'Put'} debit spread — "
                          f"{atm:g}/{wide:g} {exp_txt}"),
                "instrument": f"{row.name} {expiry} {atm:g}/{wide:g} debit spread",
                "rationale": (
                    "This setup resolves quickly, which suits a defined-risk "
                    "option structure. A spread also limits the damage from "
                    "overpaying, which matters because the implied volatility "
                    "here could not be verified."),
                "warnings": [
                    "Implied volatility could not be read reliably (thin or "
                    "wide-spread chain) — price the structure yourself before "
                    "trading it.",
                    "Defined risk and defined reward."],
                "priority": 45,
            })

        if iv_regime == "cheap" or (iv_regime == "fair" and fast):
            # Pay for convexity.
            actions.append({
                "type": "long_call" if is_long else "long_put",
                "label": f"Long {'call' if is_long else 'put'} — {atm:g} {exp_txt}",
                "instrument": f"{row.name} {expiry} {atm:g}{'C' if is_long else 'P'}",
                "rationale": (
                    f"Options are not pricing in much movement ({iv_txt}), and "
                    f"this setup resolves quickly if it works. Convexity is "
                    f"cheap and your loss is capped at the premium."),
                "warnings": (["Total loss of premium if the move doesn't come "
                              "before expiry."]
                             + (["Earnings inside the option's life — implied "
                                 "vol will collapse after the print."]
                                if earnings_inside else [])),
                "priority": 80 if fast else 60,
            })
            actions.append({
                "type": "debit_spread",
                "label": (f"{'Call' if is_long else 'Put'} debit spread — "
                          f"{atm:g}/{wide:g} {exp_txt}"),
                "instrument": f"{row.name} {expiry} {atm:g}/{wide:g} debit spread",
                "rationale": (
                    f"Same direction at lower cost than the outright option. "
                    f"Caps the gain near ${wide:g} — roughly the "
                    f"{C.TARGET_ATR_MULT:.1f}-ATR target — in exchange for a "
                    f"cheaper entry."),
                "warnings": ["Defined risk and defined reward; you give up "
                             "everything beyond the short strike."],
                "priority": 70 if fast else 55,
            })
        if iv_regime == "rich":
            # Get paid for the same view.
            if is_long:
                actions.append({
                    "type": "bull_put_spread",
                    "label": f"Bull put spread — sell {short_strike:g}, "
                             f"buy {far_strike:g} {exp_txt}",
                    "instrument": (f"{row.name} {expiry} {short_strike:g}/"
                                   f"{far_strike:g} put credit spread"),
                    "rationale": (
                        f"Options are expensive relative to how much this "
                        f"stock has actually been moving ({iv_txt}). Selling "
                        f"the {short_strike:g} put — near the "
                        f"{C.STOP_ATR_MULT:.0f}-ATR support — gets you paid to "
                        f"be bullish, and profits if price merely holds."),
                    "warnings": [
                        "Maximum loss is the spread width minus the credit, "
                        "and it exceeds the credit received.",
                        "Assignment risk on the short leg near expiry."],
                    "priority": 85,
                })
            else:
                actions.append({
                    "type": "bear_call_spread",
                    "label": f"Bear call spread — sell {short_strike:g}, "
                             f"buy {far_strike:g} {exp_txt}",
                    "instrument": (f"{row.name} {expiry} {short_strike:g}/"
                                   f"{far_strike:g} call credit spread"),
                    "rationale": (
                        f"Rich implied vol ({iv_txt}) makes selling premium "
                        f"the better-paid way to be bearish. Profits if price "
                        f"stays below {short_strike:g}, with no borrow needed."),
                    "warnings": [
                        "Maximum loss is the spread width minus the credit.",
                        "Assignment risk on the short leg near expiry."],
                    "priority": 85,
                })
        elif not is_long and bucket in ("small", "micro", "unknown"):
            # Borrow risk makes puts structurally better than a stock short.
            actions.append({
                "type": "long_put",
                "label": f"Long put — {atm:g} {exp_txt}",
                "instrument": f"{row.name} {expiry} {atm:g}P",
                "rationale": (
                    "Expresses the short view without borrowing the stock — "
                    "no borrow fee, no recall risk, and loss capped at the "
                    "premium paid."),
                "warnings": ["Total loss of premium if the move doesn't come "
                             "before expiry."],
                "priority": 75,
            })

    # ----------------------------------------------------------- leverage --
    if leverage_ok:
        lev = leverage.resolve(str(row.name), theme, direction, bucket)
        if lev:
            warn = [leverage.DECAY_WARNING]
            if lev["kind"] != "single-stock":
                warn.append(leverage.PROXY_WARNING)
            if vol_band == "high":
                warn.append("Underlying is already volatile; leverage "
                            "multiplies an already-wide range.")
            actions.append({
                "type": "leveraged_long" if is_long else "leveraged_short",
                "label": f"{lev['symbol']} ({lev['factor']} {lev['kind']})",
                "instrument": lev["symbol"],
                "rationale": (
                    f"ADX {adx:.0f} says the trend is strong and directional, "
                    f"which is the only condition daily-reset leverage "
                    f"tolerates. Tracks {lev['tracks']}."),
                "warnings": warn,
                "priority": 65 if lev["kind"] == "single-stock" else 40,
            })

    # ------------------------------------------------------ context flags --
    if earnings_inside and dte_earn is not None and dte_earn <= 3:
        actions.append({
            "type": "caution",
            "label": f"Earnings in {dte_earn} days",
            "instrument": None,
            "rationale": (
                "A binary event lands before this setup has room to play out. "
                "The chart pattern stops being the dominant variable."),
            "warnings": ["Consider waiting for the print, or sizing so a gap "
                         "through your stop is survivable."],
            "priority": 95,
        })

    actions.sort(key=lambda a: -a["priority"])
    return actions


def conviction(row: pd.Series, setup_score: float, n_confirming: int,
               opt: pd.Series | None) -> tuple[float, list[str]]:
    """Blend setup quality with tradeability into a single 0-100 number."""
    notes: list[str] = []
    score = setup_score

    if n_confirming > 1:
        bump = min(12.0, 5.0 * (n_confirming - 1))
        score += bump
        notes.append(f"{n_confirming} independent setups agree on direction "
                     f"(+{bump:.0f})")

    dvol = float(row.get("dollar_vol", 0) or 0)
    if dvol >= C.LIQ_TIER_LEVERAGE:
        score += 4
        notes.append("Deep liquidity (+4)")
    elif dvol < C.LIQ_TIER_OPTIONS:
        score -= 5
        notes.append("Thin liquidity — execution will cost you (-5)")

    atr_pct = float(row.get("atr_pct", 0) or 0)
    if atr_pct > C.VOL_HIGH:
        score -= 6
        notes.append(f"Very high volatility ({atr_pct * 100:.1f}% ATR) (-6)")

    if opt is not None:
        d = opt.get("days_to_earnings")
        if d is not None and np.isfinite(d) and 0 <= d <= 5:
            score -= 8
            notes.append(f"Earnings in {int(d)} days (-8)")

    return float(np.clip(score, 0, 100)), notes
