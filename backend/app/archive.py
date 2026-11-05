"""
Fetch long date ranges from FIRMS by splitting them into chunks.
Each chunk is cached on disk so it is only downloaded once.
"""
import hashlib
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from app.firms_client import fetch_hotspots

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
CHUNK_DAYS = 5  # FIRMS Area API allows 1-5 days per request (10 returns 400)


def _cache_path(source, bbox, start, n_days):
    key = f"{source}|{bbox}|{start}|{n_days}"
    h = hashlib.md5(key.encode()).hexdigest()[:12]
    return CACHE_DIR / f"{source}_{start}_{n_days}d_{h}.csv"


def fetch_range(bbox, source, start, end, chunk_days=CHUNK_DAYS, pause=0.2):
    """Fetch hotspots for bbox between start and end (YYYY-MM-DD, inclusive)."""
    CACHE_DIR.mkdir(exist_ok=True)
    frames = []
    d = date.fromisoformat(start)
    last = date.fromisoformat(end)

    while d <= last:
        n = min(chunk_days, (last - d).days + 1)
        path = _cache_path(source, bbox, d.isoformat(), n)

        if path.exists():
            df = pd.read_csv(path)
        else:
            df = fetch_hotspots(bbox, source=source, day_range=n, start_date=d.isoformat())
            df.to_csv(path, index=False)
            time.sleep(pause)

        if len(df) > 0:
            frames.append(df)
        d += timedelta(days=n)

    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)
