# Baptou Stocks

A rules-based screener that scans every US-listed common stock each weekday,
groups what it finds by theme (AI & semiconductors, biotech, uranium, banks…),
and for each name shows the specific ways a setup could be expressed — long,
short, leveraged, or a particular option structure.

It runs entirely on free infrastructure: **GitHub Actions** does the daily
compute, **GitHub Pages** serves the dashboard, and all data comes from free,
keyless endpoints. There is no server, no database and no API key anywhere.

> **This is a screener, not investment advice.** Every result is a chart or
> volatility condition that matched a written rule. It is not a prediction,
> it has not been backtested, and it knows nothing about fundamentals, news or
> your circumstances. Verify everything with your broker before trading.

---

## What it produces

For each stock that matches, you get:

- **The setups it matched** — with the thesis behind each, the evidence
  (actual RSI, ADX, relative-strength and volume numbers), and explicitly
  what would invalidate it.
- **Reference levels** — entry, a 2-ATR stop, a 3.5-ATR target, and the
  resulting reward-to-risk. Scaled to each stock's own volatility rather than
  a flat percentage.
- **Ranked ways to express it**, each with reasoning and warnings:
  - long or short stock
  - leveraged ETFs (single-stock 2x where they exist, otherwise sector 3x)
  - option structures chosen from the volatility regime — long calls/puts and
    debit spreads when implied vol is cheap, credit spreads when it is rich
  - puts instead of a stock short on small caps, where borrow is a real risk
- **A conviction score** with every adjustment shown (multiple setups
  agreeing, thin liquidity, earnings proximity, conflicting signals).

Plus a **Pairs** tab: same-theme stocks whose spread has stretched, with the
correlation, OLS hedge ratio and mean-reversion half-life behind each.

And a **Follow-up** tab, which is where the screener has to answer for itself.

---

## Following trades up

Each day's best ideas are recorded and then followed forward bar by bar until
they hit their stop, hit their target, or time out. Expanding a trade draws
its price history with the entry, stop, target and exit marked, and a slider
re-runs the whole simulation from a different entry day — levels move with the
entry, keeping their ATR distances — so you can see how much the result
depended on catching it on day one.

The simulation rules, stated plainly because they decide every number there:

| | |
|---|---|
| Entry | the open of the session *after* the signal — the first price you could actually pay |
| Stop | entry −/+ 2 × ATR, using the ATR as of the signal date |
| Target | entry +/− 3.5 × ATR |
| Exit | first bar whose low touches the stop or whose high touches the target |
| Same bar | if one bar's range covers **both**, the stop is taken |
| Timeout | 60 sessions, marked out at the close |

That same-bar rule matters. A daily bar cannot say which level was hit first,
and assuming the good one would inflate every statistic on the page.

**Not modelled:** slippage, commission, the bid-ask spread, gapping through a
stop and filling far worse than it, and borrow cost on shorts. Real results
would be worse, and the gap is not small for the wider-spread names.

The browser re-runs this simulation live as you move the slider, so its
arithmetic must match the engine's exactly. `scripts/validate.py` verifies
every reported R-multiple is reproducible from the entry, stop and exit stored
beside it, and the publish gate refuses anything that isn't.

### Seeding history

A journal that starts empty tells you nothing for months, so
`scripts/seed_journal.py` replays the setup engine over past sessions with the
price panel truncated to each date — no lookahead. It is a sanity check on the
rules, not a track record, and it carries **survivorship bias**: the universe
is today's listed names, so anything delisted, acquired or bankrupted in the
meantime is absent, and those are disproportionately the losers.

```bash
python -m scripts.seed_journal --days 70
```

---

## Setup

**1. Create the repo and push**

```bash
gh repo create baptou-stocks --public --source . --push
```

A **public** repo gets unlimited free Actions minutes. Private works too — a
daily run costs roughly 200 of the 2,000 free monthly minutes.

**2. Turn on Pages**

In *Settings → Pages*, set **Source: Deploy from a branch**, then branch
`main` and folder `/docs`. The dashboard appears at
`https://<you>.github.io/baptou-stocks/` within a minute or two.

**3. Allow Actions to commit**

In *Settings → Actions → General → Workflow permissions*, choose
**Read and write permissions**. The job commits the refreshed data file back
to the repo; without this it will fail at the push step.

**4. Run it once by hand**

*Actions → Refresh screener → Run workflow*. After that it runs itself at
21:30 UTC every weekday — after the US close in both EST and EDT.

---

## Running locally

```bash
pip install -r requirements.txt
python -m engine.build
```

Then serve the dashboard (opening `index.html` directly won't work — the page
fetches its data file, which browsers block on `file://`):

```bash
python -m http.server 8000 --directory docs
```

Useful flags while working on the rules:

| Flag | Effect |
|---|---|
| `--limit N` | only scan the first N tickers |
| `--cached` | reuse the last price download instead of re-fetching |
| `--skip-options` | skip option chains (much faster) |

`--cached` is the important one: it turns a 5-minute iteration loop into a
5-second one when you're tuning `MIN_SETUP_SCORE` or a gate.

---

## How it fits together

```
engine/
  config.py       all tunables — thresholds, weights, liquidity floors
  universe.py     NasdaqTrader symbol directories -> tradeable common stock
  prices.py       chunked bulk OHLCV download -> date x ticker panels
  indicators.py   RSI, MACD, ATR, ADX, Bollinger, RS vs SPY — fully vectorised
  themes.py       ticker/industry/name -> theme, in priority order
  profiles.py     incremental sector & market-cap cache, committed to the repo
  signals.py      the setup definitions: gate + weighted components
  options.py      ATM implied vol and earnings proximity for candidates only
  leverage.py     single-stock and sector leveraged ETF mapping
  pairs.py        same-theme spread z-scores with hedge ratio and half-life
  actions.py      setup + context -> concrete expression, with warnings
  journal.py      records suggestions and follows them to stop/target/timeout
  build.py        orchestrator -> docs/data/{latest,journal}.json
docs/             the dashboard (static, zero dependencies)
scripts/
  validate.py     structural checks; CI refuses to publish if these fail
  seed_journal.py replays the rules over past sessions to seed the journal
  summary.py      writes the run report to the Actions job summary
```

Only the *seed* of each tracked trade is stored — ticker, setup, date,
reference price and ATR. Bars, exits and statistics are recomputed from the
price panels on every run, so the journal cannot drift out of sync with the
price history the way an incrementally-appended file would.

Everything is computed across the whole universe at once as wide DataFrames,
so ~5,600 tickers cost barely more than a hundred would. A full run takes a
few minutes, nearly all of it network wait.

### Design decisions worth knowing

**Gates and scores are separate.** A setup's gate is a hard requirement — the
pattern is present or it isn't, and a name that fails is never shown at any
score. The score only measures how clean the instance is. Without the gate, a
scoring model happily assigns a mediocre score to a chart that looks nothing
like the setup.

**Implied volatility is treated sceptically.** A true IV rank needs a year of
implied-vol history that no free feed provides, so what's shown is ATM implied
vol against the volatility the stock actually delivered. Two guards matter:
the realised-vol reference is a gap-resistant (MAD-based) estimator, so one
merger or FDA gap doesn't make normal options look cheap for months; and
chains too thin or wide-spread to read are marked `unreliable`, which
suppresses every volatility-based suggestion for that name rather than
inventing one.

**Liquidity gates the instrument, not just the list.** Options are only
suggested above $10m/day, leverage above $25m/day and only when ADX confirms
a directional trend — the one condition daily-reset leverage tolerates.

**Request budget is spent in priority order.** Yahoo's free endpoints will
rate-limit you, and once they start refusing, everything later in the run
fails too. So the run does the cheap, high-value fetches first — candidate
profiles, then option chains — and leaves the universe-wide profile backfill
until the very end, where being throttled costs nothing but a slower-filling
cache. The profile cache therefore fills over several runs rather than one,
most-liquid names first; until it does, unclassified names fall back to
name-based theme matching and otherwise land in *Other*.

**A failed request is not missing data.** Caching a throttled empty response
as "this ticker has no sector" would poison the cache for its whole 45-day
TTL. Errors are never written, genuine empties are only believed after
several consistent misses, and unconfirmed misses are purged and retried.

**Bad output is never published.** `scripts/validate.py` runs between the
build and the commit, checking structural integrity, level ordering
(`stop < entry < target` for a long, reversed for a short), score ranges,
duplicate tickers, and that no volatility-based suggestion exists without a
trusted IV reading. A non-zero exit stops the push, so the live page keeps
yesterday's good data instead of gaining today's broken data.

---

## Tuning

Everything worth changing is in `engine/config.py`:

| Setting | Does what |
|---|---|
| `MIN_SETUP_SCORE` | how selective the screen is (default 55) |
| `MIN_DOLLAR_VOL` | liquidity floor to appear at all |
| `LIQ_TIER_OPTIONS` / `LIQ_TIER_LEVERAGE` | when those instruments unlock |
| `STOP_ATR_MULT` / `TARGET_ATR_MULT` | the risk model |
| `MIN_RISK_REWARD` | discards setups with poor geometry |
| `IV_RV_RICH` / `IV_RV_CHEAP` | premium-selling vs premium-buying boundaries |
| `PAIR_MIN_CORR` / `PAIR_ENTRY_Z` | how stretched a pair must be |
| `MAX_PER_CATEGORY` | cards per theme, so one sector can't swamp the page |

To add a setup, append a `Setup(...)` to `SETUPS` in `engine/signals.py` — a
gate, weighted components, and the evidence fields to display. Nothing else
needs to change; the dashboard and method table pick it up automatically.

To fix a theme classification, add the ticker to `TICKER_OVERRIDES` in
`engine/themes.py`. Overrides beat industry labels, which is how NVDA lands
under AI rather than generic semiconductors.

---

## Troubleshooting

**"options: rate limited — IV will be reported as unknown"**

Yahoo throttles by IP on sustained request volume. When it does, the run still
completes and every card is still valid — you just lose the option structures
for that day, because the engine refuses to guess at implied volatility it
couldn't read. It recovers on its own next run.

If it happens on every run, spread the load: lower `MAX_FETCH_PER_RUN` in
`engine/profiles.py` (the backfill is the largest consumer, and it's the least
important — it has as many runs as it needs), or raise `PHASE_COOLDOWN` and
`OPTIONS_RETRY_PAUSE` in `engine/config.py`.

Note this is far more likely when developing locally, where repeated runs hit
the same IP within minutes. A CI runner gets a fresh IP each time and starts
with a clean budget.

**Most stocks show as theme "Other"**

The profile cache is still filling. It fetches candidates every run and
backfills the rest most-liquid-first, so it takes a handful of runs to cover
the universe. Because the cache is committed back to the repo, progress
persists — it does not restart each time.

**The page shows "Could not load data/latest.json"**

You opened `index.html` directly from disk. Browsers block `fetch` on
`file://`. Serve the folder over HTTP instead.

---

## Limitations

- **No fundamentals.** No earnings quality, balance sheet, guidance,
  dilution, insider activity or litigation. A chart can look perfect on a
  company about to file Chapter 11.
- **Not backtested.** A matched pattern is not a demonstrated edge. Nothing
  here has been tested for whether it makes money.
- **Delayed, imperfect data.** Yahoo's free feed has bad ticks, stale option
  quotes and occasional missing history. The guards catch a lot, not all.
- **Survivorship in the universe.** Only currently-listed names are scanned.
- **Option strikes are indicative.** They are derived from ATR and rounded to
  plausible increments; they are not guaranteed to be listed or liquid.
- **Shorting has costs this ignores** — borrow fees, recall risk, hard-to-
  borrow status and dividend liability.
