"""Sector / industry / market-cap cache.

yfinance exposes profile data one ticker at a time, so fetching 5,600 names
every run would both dominate the job and get us rate-limited. Instead we keep
a JSON cache that is committed back to the repo and refreshed incrementally:
screened candidates are always current, everything else backfills slowly.

Two things this module is careful about, both learned the hard way:

  A failed request is not a missing profile. If Yahoo throttles us, caching
  the empty response as "this ticker has no sector" poisons the cache for the
  whole TTL. Errors are never written; only genuine empty responses are, and
  even those are retried a couple of times before being believed.

  Throttling is contagious. Once Yahoo starts refusing, every later request in
  the run fails too — including the option chains, which matter far more than
  a backfill. So the fetcher trips a circuit breaker and gives up early rather
  than spending the run's goodwill on low-value requests.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import yfinance as yf

from . import config as C

CACHE_PATH = C.CACHE_DIR / "profiles.json"
PROFILE_TTL_DAYS = 45
MAX_FETCH_PER_RUN = 500      # gentle: the backfill has many runs to finish
FETCH_WORKERS = 6
MISS_BEFORE_TRUSTED = 3      # empty responses needed before we believe them
BREAKER_SAMPLE = 40          # consecutive failures that trip the breaker


class _Breaker:
    """Trips once the failure rate says we are being throttled."""

    def __init__(self) -> None:
        self.consecutive_errors = 0
        self.tripped = False

    def record(self, ok: bool) -> None:
        if ok:
            self.consecutive_errors = 0
        else:
            self.consecutive_errors += 1
            if self.consecutive_errors >= BREAKER_SAMPLE:
                self.tripped = True


def load_cache() -> dict[str, dict]:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print("  profile cache corrupt, starting fresh")
    return {}


def save_cache(cache: dict[str, dict]) -> None:
    CACHE_PATH.write_text(
        json.dumps(cache, indent=0, sort_keys=True), encoding="utf-8"
    )


def _needs_fetch(entry: dict | None, now: float) -> bool:
    if entry is None:
        return True
    # A cached empty is only believed after several consistent misses, so a
    # throttled run can't durably blank out real companies.
    if not entry.get("sector") and not entry.get("industry"):
        if entry.get("misses", 0) < MISS_BEFORE_TRUSTED:
            return True
    return (now - entry.get("ts", 0)) > PROFILE_TTL_DAYS * 86400


def _fetch_one(ticker: str) -> tuple[str, dict | None, str]:
    """Returns (ticker, entry, status) where status is ok | empty | error."""
    try:
        info = yf.Ticker(ticker).info or {}
    except Exception:  # noqa: BLE001 - throttling, timeouts, dead tickers
        return ticker, None, "error"
    if not info.get("sector") and not info.get("industry"):
        return ticker, None, "empty"
    return ticker, {
        "sector": info.get("sector") or "",
        "industry": info.get("industry") or "",
        "market_cap": info.get("marketCap") or 0,
        "short_name": info.get("shortName") or "",
        "ts": time.time(),
    }, "ok"


def refresh(priority: list[str], backfill: list[str] | None = None,
            limit: int = MAX_FETCH_PER_RUN,
            label: str = "profiles") -> dict[str, dict]:
    """Update the cache, spending the budget on `priority` tickers first."""
    cache = load_cache()
    now = time.time()

    queue = [t for t in priority if _needs_fetch(cache.get(t), now)]
    if backfill:
        seen = set(queue)
        queue += [t for t in backfill
                  if t not in seen and _needs_fetch(cache.get(t), now)]
    queue = queue[:limit]

    if not queue:
        print(f"  {label}: cache current ({len(cache)} entries)")
        return cache

    print(f"  {label}: fetching {len(queue)} (cache has {len(cache)})...")
    t0 = time.time()
    breaker = _Breaker()
    ok = errors = empties = 0

    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        for ticker, entry, status in pool.map(_fetch_one, queue):
            if breaker.tripped:
                continue
            if status == "ok":
                entry["misses"] = 0
                cache[ticker] = entry
                ok += 1
            elif status == "empty":
                # Believe it only after repeated agreement.
                prev = cache.get(ticker, {})
                cache[ticker] = {
                    "sector": "", "industry": "", "market_cap": 0,
                    "short_name": "", "ts": now,
                    "misses": int(prev.get("misses", 0)) + 1,
                }
                empties += 1
            else:
                errors += 1
            breaker.record(status != "error")

    msg = (f"  {label}: {ok} resolved, {empties} empty, {errors} failed "
           f"in {time.time() - t0:.0f}s")
    if breaker.tripped:
        msg += "  [rate limited — stopped early]"
    print(msg)
    save_cache(cache)
    return cache


def purge_suspect_misses(cache: dict[str, dict]) -> int:
    """Drop cached empties that were never confirmed — e.g. written during a
    throttled run before miss-counting existed. They will simply be refetched."""
    doomed = [t for t, e in cache.items()
              if not e.get("sector") and not e.get("industry")
              and e.get("misses", 0) == 0]
    for t in doomed:
        del cache[t]
    return len(doomed)


def cap_bucket(market_cap: float) -> str:
    if not market_cap:
        return "unknown"
    if market_cap >= 200e9:
        return "mega"
    if market_cap >= 10e9:
        return "large"
    if market_cap >= 2e9:
        return "mid"
    if market_cap >= 300e6:
        return "small"
    return "micro"


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
