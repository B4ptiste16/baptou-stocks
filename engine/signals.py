"""The setup rule engine.

A *setup* is a named, mechanical pattern with two parts:

  gate       hard requirements. Either the pattern is present or it isn't;
             a name that fails the gate is never reported at any score.
  components weighted 0-1 quality measures, blended into a 0-100 score that
             says how *clean* an instance of the pattern this is.

Splitting them matters. Without a gate a scoring model will happily hand a
mediocre score to a chart that looks nothing like the setup; without
components every trigger looks equally good. Scores are comparable across
setups because every component is normalised to 0-1 before weighting.

None of this predicts anything. It identifies chart conditions that match a
written definition, and shows the numbers behind the match.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from . import config as C


def scale(s: pd.Series, lo: float, hi: float) -> pd.Series:
    """Linear 0-1 normalisation, clipped at both ends."""
    return ((s - lo) / (hi - lo)).clip(0.0, 1.0).fillna(0.0)


@dataclass
class Setup:
    id: str
    label: str
    direction: str            # "long" | "short"
    thesis: str               # what the pattern is claiming, in one sentence
    invalidation: str         # what would say the pattern has failed
    gate: Callable[[pd.DataFrame], pd.Series]
    components: list[tuple[str, Callable[[pd.DataFrame], pd.Series], float]]
    evidence: list[tuple[str, str]] = field(default_factory=list)

    def evaluate(self, f: pd.DataFrame) -> pd.Series:
        """Return a 0-100 score for gated names, NaN for everything else."""
        mask = self.gate(f).fillna(False)
        if not mask.any():
            return pd.Series(np.nan, index=f.index)
        sub = f[mask]
        total_w = sum(w for _, _, w in self.components)
        acc = pd.Series(0.0, index=sub.index)
        for _, fn, w in self.components:
            acc += w * fn(sub).clip(0.0, 1.0).fillna(0.0)
        out = pd.Series(np.nan, index=f.index)
        out[mask] = 100.0 * acc / total_w
        return out

    def describe(self, row: pd.Series) -> list[str]:
        """Human-readable evidence for one ticker."""
        out = []
        for col, fmt in self.evidence:
            val = row.get(col)
            if val is None or (isinstance(val, float) and np.isnan(val)):
                continue
            try:
                out.append(fmt.format(val))
            except (ValueError, TypeError):
                continue
        return out


# ------------------------------------------------------------------ LONGS --
SETUPS: list[Setup] = [
    Setup(
        id="trend_pullback",
        label="Pullback in an uptrend",
        direction="long",
        thesis="Price is in an established uptrend and has pulled back to "
               "the 50-day average without breaking structure.",
        invalidation="A close below the 200-day average, or below the recent "
                     "swing low.",
        gate=lambda f: (
            f["above_sma200"] & f["uptrend_stack"]
            & f["rsi"].between(30, 52)
            & f["dist_sma50_atr"].between(-1.5, 1.0)
            & f["ret_252d"].gt(0)
        ),
        components=[
            ("relative strength", lambda f: scale(f["rs_126d"], 0, 30), 0.25),
            ("pullback depth", lambda f: scale(52 - f["rsi"], 0, 20), 0.25),
            ("at support", lambda f: scale(1.0 - f["dist_sma50_atr"].abs(), 0, 1), 0.20),
            ("trend strength", lambda f: scale(f["adx"], 15, 35), 0.15),
            ("trend cushion", lambda f: scale(f["dist_sma200_atr"], 0, 6), 0.15),
        ],
        evidence=[("rsi", "RSI {:.0f}"), ("dist_sma50_atr", "{:+.1f} ATR from 50DMA"),
                  ("rs_126d", "{:+.0f}% vs SPY (6m)"), ("adx", "ADX {:.0f}")],
    ),
    Setup(
        id="breakout_52w",
        label="Breakout to 52-week highs",
        direction="long",
        thesis="Price is pushing into new 1-year high ground on expanding "
               "volume, with no overhead supply left.",
        invalidation="A close back below the breakout level on heavy volume.",
        gate=lambda f: (
            f["pct_from_high"].gt(-2.5) & f["vol_surge"].gt(1.2)
            & f["above_sma50"] & f["above_sma200"] & f["adx"].gt(18)
        ),
        components=[
            ("volume expansion", lambda f: scale(f["vol_surge"], 1.2, 3.0), 0.30),
            ("relative strength", lambda f: scale(f["rs_63d"], 0, 25), 0.25),
            ("trend strength", lambda f: scale(f["adx"], 18, 40), 0.20),
            ("thrust", lambda f: scale(f["ret_21d"], 0, 20), 0.15),
            ("at the high", lambda f: scale(f["pct_from_high"], -2.5, 0), 0.10),
        ],
        evidence=[("pct_from_high", "{:.1f}% from 52w high"),
                  ("vol_surge", "{:.1f}x normal volume"),
                  ("rs_63d", "{:+.0f}% vs SPY (3m)"), ("adx", "ADX {:.0f}")],
    ),
    Setup(
        id="oversold_reversion",
        label="Oversold bounce in a healthy name",
        direction="long",
        thesis="A stock still above its 200-day average has been sold to an "
               "extreme and momentum has started to turn back up.",
        invalidation="RSI rolling over again, or a close below the 200-day.",
        gate=lambda f: (
            f["rsi"].lt(32) & f["above_sma200"] & f["ret_252d"].gt(-10)
            & f["rsi"].gt(f["rsi_prev"])
        ),
        components=[
            ("oversold depth", lambda f: scale(32 - f["rsi"], 0, 15), 0.30),
            ("band extension", lambda f: scale(0.15 - f["bb_pctb"], 0, 0.15), 0.20),
            ("turning up", lambda f: scale(f["rsi"] - f["rsi_prev"], 0, 5), 0.20),
            ("trend intact", lambda f: scale(f["dist_sma200_atr"], 0, 4), 0.20),
            ("capitulation volume", lambda f: scale(f["vol_surge"], 1.0, 2.5), 0.10),
        ],
        evidence=[("rsi", "RSI {:.0f}"), ("bb_pctb", "Bollinger %B {:.2f}"),
                  ("pct_from_high", "{:.0f}% off 52w high"),
                  ("ret_252d", "{:+.0f}% over 1y")],
    ),
    Setup(
        id="momentum_leader",
        label="Momentum leader",
        direction="long",
        thesis="Price leads the market on 3- and 6-month relative strength "
               "with every moving average stacked bullishly beneath it.",
        invalidation="Loss of the 50-day average, or relative strength "
                     "rolling over versus SPY.",
        gate=lambda f: (
            f["above_sma50"] & f["above_sma200"] & f["uptrend_stack"]
            & f["rs_63d"].gt(8) & f["adx"].gt(22) & f["pct_from_high"].gt(-12)
        ),
        components=[
            ("3m relative strength", lambda f: scale(f["rs_63d"], 8, 40), 0.30),
            ("6m relative strength", lambda f: scale(f["rs_126d"], 0, 50), 0.25),
            ("trend strength", lambda f: scale(f["adx"], 22, 45), 0.20),
            ("recent thrust", lambda f: scale(f["ret_21d"], 0, 25), 0.15),
            ("near highs", lambda f: scale(f["pct_from_high"], -12, 0), 0.10),
        ],
        evidence=[("rs_63d", "{:+.0f}% vs SPY (3m)"),
                  ("rs_126d", "{:+.0f}% vs SPY (6m)"), ("adx", "ADX {:.0f}"),
                  ("pct_from_high", "{:.1f}% from 52w high")],
    ),
    Setup(
        id="golden_cross",
        label="Fresh golden cross",
        direction="long",
        thesis="The 50-day average has just crossed above the 200-day, the "
               "classic marker of a regime change from down to up.",
        invalidation="The cross reversing, or price losing the 200-day.",
        gate=lambda f: (
            f["uptrend_stack"] & f["bars_since_ma_cross"].le(15)
            & f["above_sma50"]
        ),
        components=[
            ("freshness", lambda f: scale(15 - f["bars_since_ma_cross"], 0, 15), 0.30),
            ("trend strength", lambda f: scale(f["adx"], 15, 35), 0.25),
            ("relative strength", lambda f: scale(f["rs_63d"], -5, 25), 0.25),
            ("volume confirmation", lambda f: scale(f["vol_surge"], 0.9, 2.0), 0.20),
        ],
        evidence=[("bars_since_ma_cross", "crossed {:.0f} sessions ago"),
                  ("adx", "ADX {:.0f}"), ("rs_63d", "{:+.0f}% vs SPY (3m)")],
    ),
    Setup(
        id="squeeze_breakout",
        label="Volatility squeeze breaking up",
        direction="long",
        thesis="Bollinger bands have compressed to the tightest 20% of the "
               "past year and price is breaking out of the top of the range.",
        invalidation="Price falling back inside the bands — squeezes do "
                     "resolve in both directions.",
        gate=lambda f: (
            f["bb_width_pctile"].lt(0.20) & f["bb_pctb"].gt(0.85)
            & f["above_sma200"] & f["vol_surge"].gt(1.1)
        ),
        components=[
            ("compression", lambda f: scale(0.20 - f["bb_width_pctile"], 0, 0.20), 0.30),
            ("volume expansion", lambda f: scale(f["vol_surge"], 1.1, 2.5), 0.25),
            ("break strength", lambda f: scale(f["bb_pctb"], 0.85, 1.15), 0.25),
            ("trend alignment", lambda f: scale(f["dist_sma200_atr"], 0, 5), 0.20),
        ],
        evidence=[("bb_width_pctile", "band width in bottom {:.0%} of 1y"),
                  ("bb_pctb", "%B {:.2f}"), ("vol_surge", "{:.1f}x normal volume")],
    ),

    # ------------------------------------------------------------- SHORTS --
    Setup(
        id="trend_breakdown",
        label="Rally into resistance in a downtrend",
        direction="short",
        thesis="A stock in a confirmed downtrend has rallied back into its "
               "falling 50-day average, where supply has repeatedly appeared.",
        invalidation="A close above the 200-day average, or the 50-day "
                     "turning up.",
        gate=lambda f: (
            ~f["above_sma200"] & ~f["uptrend_stack"]
            & f["rsi"].between(45, 65)
            & f["dist_sma50_atr"].between(-1.0, 1.5)
            & f["ret_252d"].lt(0)
        ),
        components=[
            ("relative weakness", lambda f: scale(-f["rs_126d"], 0, 30), 0.25),
            ("rally extent", lambda f: scale(f["rsi"] - 45, 0, 20), 0.25),
            ("at resistance", lambda f: scale(1.0 - f["dist_sma50_atr"].abs(), 0, 1), 0.20),
            ("trend strength", lambda f: scale(f["adx"], 15, 35), 0.15),
            ("downtrend depth", lambda f: scale(-f["dist_sma200_atr"], 0, 6), 0.15),
        ],
        evidence=[("rsi", "RSI {:.0f}"), ("dist_sma50_atr", "{:+.1f} ATR from 50DMA"),
                  ("rs_126d", "{:+.0f}% vs SPY (6m)"),
                  ("ret_252d", "{:+.0f}% over 1y")],
    ),
    Setup(
        id="breakdown_52w",
        label="Breakdown to 52-week lows",
        direction="short",
        thesis="Price is cutting through 1-year lows on expanding volume "
               "with no visible support beneath.",
        invalidation="A reclaim of the broken low, especially on volume.",
        gate=lambda f: (
            f["pct_from_low"].lt(3) & f["vol_surge"].gt(1.2)
            & ~f["above_sma50"] & ~f["above_sma200"] & f["adx"].gt(18)
        ),
        components=[
            ("volume expansion", lambda f: scale(f["vol_surge"], 1.2, 3.0), 0.30),
            ("relative weakness", lambda f: scale(-f["rs_63d"], 0, 25), 0.25),
            ("trend strength", lambda f: scale(f["adx"], 18, 40), 0.20),
            ("downside thrust", lambda f: scale(-f["ret_21d"], 0, 20), 0.15),
            ("at the low", lambda f: scale(3 - f["pct_from_low"], 0, 3), 0.10),
        ],
        evidence=[("pct_from_low", "{:.1f}% above 52w low"),
                  ("vol_surge", "{:.1f}x normal volume"),
                  ("rs_63d", "{:+.0f}% vs SPY (3m)"), ("adx", "ADX {:.0f}")],
    ),
    Setup(
        id="exhaustion",
        label="Overbought exhaustion",
        direction="short",
        thesis="Price is stretched far above its 20-day average at an "
               "extreme RSI while MACD momentum has already begun to fade.",
        invalidation="Momentum re-accelerating — strong trends stay "
                     "overbought far longer than feels reasonable.",
        gate=lambda f: (
            f["rsi"].gt(76) & f["dist_sma20_atr"].gt(2.0)
            & f["macd_hist"].lt(f["macd_hist_prev"])
        ),
        components=[
            ("overbought extreme", lambda f: scale(f["rsi"] - 76, 0, 12), 0.30),
            ("stretch from mean", lambda f: scale(f["dist_sma20_atr"], 2.0, 5.0), 0.30),
            ("momentum fading", lambda f: scale(f["macd_hist_prev"] - f["macd_hist"],
                                                0, f["atr"].abs() * 0.5 + 1e-9), 0.20),
            ("band extension", lambda f: scale(f["bb_pctb"] - 1.0, 0, 0.2), 0.20),
        ],
        evidence=[("rsi", "RSI {:.0f}"),
                  ("dist_sma20_atr", "{:+.1f} ATR above 20DMA"),
                  ("ret_21d", "{:+.0f}% in 21 sessions")],
    ),
    Setup(
        id="momentum_laggard",
        label="Momentum laggard",
        direction="short",
        thesis="Price trails the market badly on 3- and 6-month relative "
               "strength with every moving average stacked bearishly above it.",
        invalidation="Reclaiming the 50-day average, or relative strength "
                     "turning up versus SPY.",
        gate=lambda f: (
            ~f["above_sma50"] & ~f["above_sma200"] & ~f["uptrend_stack"]
            & f["rs_63d"].lt(-8) & f["adx"].gt(22)
        ),
        components=[
            ("3m relative weakness", lambda f: scale(-f["rs_63d"], 8, 40), 0.30),
            ("6m relative weakness", lambda f: scale(-f["rs_126d"], 0, 50), 0.25),
            ("trend strength", lambda f: scale(f["adx"], 22, 45), 0.20),
            ("recent decline", lambda f: scale(-f["ret_21d"], 0, 25), 0.15),
            ("near lows", lambda f: scale(20 - f["pct_from_low"], 0, 20), 0.10),
        ],
        evidence=[("rs_63d", "{:+.0f}% vs SPY (3m)"),
                  ("rs_126d", "{:+.0f}% vs SPY (6m)"), ("adx", "ADX {:.0f}"),
                  ("pct_from_low", "{:.0f}% above 52w low")],
    ),
    Setup(
        id="death_cross",
        label="Fresh death cross",
        direction="short",
        thesis="The 50-day average has just crossed below the 200-day, "
               "marking a regime change from up to down.",
        invalidation="The cross reversing, or price reclaiming the 200-day.",
        gate=lambda f: (
            ~f["uptrend_stack"] & f["bars_since_ma_cross"].le(15)
            & ~f["above_sma50"]
        ),
        components=[
            ("freshness", lambda f: scale(15 - f["bars_since_ma_cross"], 0, 15), 0.30),
            ("trend strength", lambda f: scale(f["adx"], 15, 35), 0.25),
            ("relative weakness", lambda f: scale(-f["rs_63d"], -5, 25), 0.25),
            ("volume confirmation", lambda f: scale(f["vol_surge"], 0.9, 2.0), 0.20),
        ],
        evidence=[("bars_since_ma_cross", "crossed {:.0f} sessions ago"),
                  ("adx", "ADX {:.0f}"), ("rs_63d", "{:+.0f}% vs SPY (3m)")],
    ),
    Setup(
        id="parabolic_fade",
        label="Parabolic move losing steam",
        direction="short",
        thesis="A vertical multi-week advance has left price far above any "
               "support at historically extreme volatility.",
        invalidation="Any continuation. This is the highest-risk pattern "
                     "here — parabolic moves routinely double before they end.",
        gate=lambda f: (
            f["ret_21d"].gt(50) & f["rsi"].gt(70)
            & f["rvol_pctile"].gt(0.75) & f["dist_sma20_atr"].gt(2.0)
        ),
        components=[
            ("advance size", lambda f: scale(f["ret_21d"], 50, 200), 0.30),
            ("stretch from mean", lambda f: scale(f["dist_sma20_atr"], 2.0, 6.0), 0.25),
            ("volatility extreme", lambda f: scale(f["rvol_pctile"], 0.75, 1.0), 0.25),
            ("overbought", lambda f: scale(f["rsi"] - 70, 0, 15), 0.20),
        ],
        evidence=[("ret_21d", "{:+.0f}% in 21 sessions"), ("rsi", "RSI {:.0f}"),
                  ("dist_sma20_atr", "{:+.1f} ATR above 20DMA"),
                  ("rvol_pctile", "volatility at {:.0%} of 1y range")],
    ),
]

SETUP_BY_ID = {s.id: s for s in SETUPS}


def run_all(f: pd.DataFrame, min_score: float | None = None) -> pd.DataFrame:
    """Evaluate every setup over the feature frame.

    Returns a long frame: one row per (ticker, setup) that cleared the gate
    and the score threshold.
    """
    min_score = C.MIN_SETUP_SCORE if min_score is None else min_score
    rows = []
    for setup in SETUPS:
        scores = setup.evaluate(f)
        hit = scores[scores.notna() & (scores >= min_score)]
        for ticker, score in hit.items():
            rows.append({
                "ticker": ticker,
                "setup": setup.id,
                "setup_label": setup.label,
                "direction": setup.direction,
                "score": round(float(score), 1),
            })
    if not rows:
        return pd.DataFrame(columns=["ticker", "setup", "setup_label",
                                     "direction", "score"])
    return pd.DataFrame(rows).sort_values("score", ascending=False)
