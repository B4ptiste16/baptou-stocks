"""Chunked bulk OHLCV download, reshaped into date x ticker panels.

Everything downstream is vectorised across the whole universe at once, so the
job here is to turn yfinance's MultiIndex frame into five wide DataFrames
(open/high/low/close/volume) sharing one date index. On ~5,600 tickers this
takes roughly 2-4 minutes.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import yfinance as yf

from . import config as C

FIELDS = ("Open", "High", "Low", "Close", "Volume")


def _chunks(seq: list[str], n: int):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _download_chunk(tickers: list[str]) -> pd.DataFrame | None:
    """One bulk request. Returns a MultiIndex (ticker, field) frame or None."""
    for attempt in range(C.DOWNLOAD_RETRIES + 1):
        try:
            df = yf.download(
                tickers,
                period=C.DOWNLOAD_PERIOD,
                interval="1d",
                group_by="ticker",
                auto_adjust=True,
                actions=False,
                threads=True,
                progress=False,
            )
            if df is not None and not df.empty:
                # A single surviving ticker comes back without the outer level.
                if not isinstance(df.columns, pd.MultiIndex):
                    df.columns = pd.MultiIndex.from_product([[tickers[0]], df.columns])
                return df
        except Exception as exc:  # noqa: BLE001 - one bad chunk must not kill the run
            if attempt == C.DOWNLOAD_RETRIES:
                print(f"    chunk failed permanently ({len(tickers)} tickers): {exc}")
            else:
                time.sleep(2 * (attempt + 1))
    return None


def fetch_panels(tickers: list[str], workers: int | None = None) -> dict[str, pd.DataFrame]:
    """Download `tickers` and return {'close': df, 'high': df, ...}.

    Each returned frame is indexed by date with one column per ticker that
    actually returned usable data.
    """
    tickers = sorted(set(tickers))
    batches = list(_chunks(tickers, C.DOWNLOAD_CHUNK))
    workers = workers or C.MAX_DOWNLOAD_WORKERS
    print(f"  downloading {len(tickers)} tickers in {len(batches)} chunks "
          f"({workers} workers)...")

    frames: list[pd.DataFrame] = []
    done = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_download_chunk, b): b for b in batches}
        for fut in as_completed(futures):
            res = fut.result()
            done += 1
            if res is not None:
                frames.append(res)
            if done % 5 == 0 or done == len(batches):
                print(f"    {done}/{len(batches)} chunks  "
                      f"({time.time() - t0:.0f}s elapsed)")

    if not frames:
        raise RuntimeError("no price data returned for any chunk")

    wide = pd.concat(frames, axis=1)
    wide = wide.sort_index()

    panels: dict[str, pd.DataFrame] = {}
    available = set(wide.columns.get_level_values(1))
    for field in FIELDS:
        if field not in available:
            continue
        sub = wide.xs(field, axis=1, level=1)
        sub = sub.loc[:, ~sub.columns.duplicated()]
        panels[field.lower()] = sub.astype("float64")

    close = panels["close"]
    good = close.notna().sum() >= C.MIN_HISTORY_BARS
    keep = good[good].index
    dropped = close.shape[1] - len(keep)
    print(f"  usable tickers: {len(keep)} (dropped {dropped} with "
          f"<{C.MIN_HISTORY_BARS} bars)")

    for k in panels:
        panels[k] = panels[k].reindex(columns=keep)
    return panels


def _has_parquet() -> bool:
    try:
        import pyarrow  # noqa: F401
        return True
    except ImportError:
        try:
            import fastparquet  # noqa: F401
            return True
        except ImportError:
            return False


def save_panels(panels: dict[str, pd.DataFrame], tag: str = "us") -> None:
    """Cache panels for local iteration. Purely a convenience — a failure
    here must never abort a build, and CI has nothing to reuse anyway."""
    parquet = _has_parquet()
    try:
        for name, df in panels.items():
            if parquet:
                df.to_parquet(C.RAW_DIR / f"{tag}_{name}.parquet")
            else:
                df.to_pickle(C.RAW_DIR / f"{tag}_{name}.pkl")
    except Exception as exc:  # noqa: BLE001
        print(f"  (price cache not written: {exc})")


def load_panels(tag: str = "us") -> dict[str, pd.DataFrame] | None:
    """Reload a previously saved run — used for fast local iteration."""
    out: dict[str, pd.DataFrame] = {}
    for name in ("open", "high", "low", "close", "volume"):
        pq = C.RAW_DIR / f"{tag}_{name}.parquet"
        pk = C.RAW_DIR / f"{tag}_{name}.pkl"
        try:
            if pq.exists():
                out[name] = pd.read_parquet(pq)
            elif pk.exists():
                out[name] = pd.read_pickle(pk)
            else:
                return None
        except Exception:  # noqa: BLE001 - a stale cache should just be ignored
            return None
    return out
