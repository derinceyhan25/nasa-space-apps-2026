"""
Fetch long date ranges from FIRMS in 5-day chunks aligned to a fixed global
grid (origin 2000-01-01), so a cached chunk is reused by any later request.
Rows are filtered to [start, end] at the end.
"""
import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from app.firms_client import fetch_hotspots

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
CHUNK_DAYS = 5
GRID_ORIGIN = date(2000, 1, 1)
NOT_FINAL_DAYS = 150  # recent standard data may be missing: do not cache


def _chunk_starts(start, end):
    d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
    offset = (d0 - GRID_ORIGIN).days // CHUNK_DAYS * CHUNK_DAYS
    s = GRID_ORIGIN + timedelta(days=offset)
    while s <= d1:
        yield s
        s += timedelta(days=CHUNK_DAYS)


def _cache_path(source, bbox, s):
    h = hashlib.md5(f"{source}|{bbox}".encode()).hexdigest()[:8]
    return CACHE_DIR / f"{source}_{h}" / f"{s.isoformat()}.csv"


def get_chunk(bbox, source, s, retries=4):
    """One 5-day chunk: from disk if cached, else download (with retries)."""
    path = _cache_path(source, bbox, s)
    if path.exists():
        return pd.read_csv(path)
    for attempt in range(retries):
        try:
            df = fetch_hotspots(
                bbox, source=source, day_range=CHUNK_DAYS, start_date=s.isoformat()
            )
            break
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)
    chunk_end = s + timedelta(days=CHUNK_DAYS)
    if chunk_end < date.today() - timedelta(days=NOT_FINAL_DAYS):
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(path, index=False)
    return df


def fetch_range(bbox, source, start, end, workers=1):
    """All hotspots for bbox between start and end (YYYY-MM-DD, inclusive)."""
    starts = list(_chunk_starts(start, end))
    with ThreadPoolExecutor(workers) as ex:
        frames = list(ex.map(lambda s: get_chunk(bbox, source, s), starts))
    frames = [f for f in frames if len(f) > 0]
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    return df[(df.acq_date >= start) & (df.acq_date <= end)]
