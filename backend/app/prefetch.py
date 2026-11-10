"""
Download and cache MODIS + VIIRS archive data for demo regions, in parallel,
staying under the FIRMS transaction limit (5000 per 10 minutes).
Before each request it checks the key's current usage and waits while the
usage is above THRESHOLD.
Safe to stop (Ctrl+C) and re-run: finished chunks are skipped.
Usage:  python -m app.prefetch california
"""
import os
import re
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from app.archive import _cache_path, _chunk_starts, get_chunk

REGIONS = {
    "california": (-124.5, 32.5, -114.0, 42.0),
    "turkey": (26.0, 36.0, 45.0, 42.0),
}
SOURCES = {
    "MODIS_SP": ("2000-11-11", "2026-05-31"),
    "VIIRS_SNPP_SP": ("2012-01-20", "2026-05-31"),
}
WORKERS = 6

STATUS_URL = "https://firms.modaps.eosdis.nasa.gov/mapserver/mapkey_status/?MAP_KEY="
THRESHOLD = 3500  # wait while the 10-minute usage is above this (limit: 5000)
COST_GUESS = 15  # assumed cost of a request not yet visible in the status
REFRESH = 3.0  # seconds between status checks
POLL = 5.0  # seconds to sleep while waiting for budget

_state = {"used": 0, "t": 0.0, "pending": 0}
_lock = threading.Lock()


def used_transactions():
    text = requests.get(STATUS_URL + os.environ["FIRMS_MAP_KEY"], timeout=20).text
    return int(re.search(r'current_transactions"\s*:\s*(\d+)', text).group(1))


def wait_for_budget():
    """Block until the 10-minute transaction budget has room for one more request."""
    while True:
        with _lock:
            if time.time() - _state["t"] > REFRESH:
                try:
                    _state["used"] = used_transactions()
                    _state["t"] = time.time()
                    _state["pending"] = 0
                except Exception:
                    time.sleep(POLL)
                    continue
            if _state["used"] + _state["pending"] * COST_GUESS < THRESHOLD:
                _state["pending"] += 1
                return
        time.sleep(POLL)


def job(bbox, source, s):
    for attempt in range(4):
        wait_for_budget()
        try:
            return get_chunk(bbox, source, s, retries=1)
        except Exception:
            if attempt == 3:
                raise
            time.sleep(15)


def run(names):
    jobs = []
    for name in names:
        bbox = REGIONS[name]
        for source, (a, b) in SOURCES.items():
            for s in _chunk_starts(a, b):
                if not _cache_path(source, bbox, s).exists():
                    jobs.append((name, bbox, source, s))
    print(len(jobs), "parca indirilecek", flush=True)

    t0 = time.time()
    done, failed = 0, []
    with ThreadPoolExecutor(WORKERS) as ex:
        futs = {
            ex.submit(job, bbox, source, s): (name, source, str(s))
            for name, bbox, source, s in jobs
        }
        for f in as_completed(futs):
            done += 1
            try:
                f.result()
            except Exception as e:
                failed.append((futs[f], str(e)[:90]))
            if done % 25 == 0:
                rate = done / (time.time() - t0)
                left = (len(jobs) - done) / rate / 60
                print(
                    f"{done}/{len(jobs)} | {rate:.2f} istek/sn | islem ~{_state['used']}/5000"
                    f" | hata {len(failed)} | kalan ~{left:.0f} dk",
                    flush=True,
                )

    print("Bitti.", len(failed), "hata", flush=True)
    for msg, n in Counter(m for _, m in failed).most_common(5):
        print(n, "x", msg)


if __name__ == "__main__":
    run(sys.argv[1:] or list(REGIONS))
