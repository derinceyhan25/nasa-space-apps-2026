"""
Download the newest FIRMS data: the part the main world download (app.worldgrid)
does not cover.

  * standard-processing (SP) days after 2026-05-31
  * near-real-time (NRT) days, which reach almost to today

NRT data is provisional and keeps changing, so run this again every now and then
(e.g. once a day): chunks near the newest day are downloaded again.
It runs next to the main download and shares the same request budget.
Usage:  python -m app.recent
"""
import json
import os
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
import io

import pandas as pd
import requests

import app.prefetch as budget
import app.worldgrid as wg
from app.archive import CHUNK_DAYS, _chunk_starts

AVAIL_URL = "https://firms.modaps.eosdis.nasa.gov/api/data_availability/csv/{key}/ALL"
SOURCES = ("MODIS_SP", "VIIRS_SNPP_SP", "MODIS_NRT", "VIIRS_SNPP_NRT")
MAIN_END = date(2026, 5, 31)  # last day covered by the main download
REVISE_DAYS = 21  # NRT chunks this close to the newest day are downloaded again
WORKERS = 2
EMPTY = ["latitude", "longitude", "acq_date", "frp"]


def avail_file():
    return wg.OUT_DIR / "availability.json"


def fetch_availability():
    key = os.environ["FIRMS_MAP_KEY"]
    r = requests.get(AVAIL_URL.format(key=key), timeout=60)
    r.raise_for_status()
    out = {}
    for line in r.text.splitlines()[1:]:
        p = line.strip().split(",")
        if len(p) >= 3 and p[0] in SOURCES:
            out[p[0]] = [p[1], p[2]]
    missing = set(SOURCES) - set(out)
    if missing:
        raise RuntimeError("availability lacks: " + ", ".join(sorted(missing)))
    avail_file().parent.mkdir(parents=True, exist_ok=True)
    avail_file().write_text(json.dumps(out, indent=1))
    return out


def plan(avail):
    """List of (source, chunk_start, first_day, last_day) still to download."""
    last_main = next(_chunk_starts(MAIN_END.isoformat(), MAIN_END.isoformat()))
    jobs = []
    for source in SOURCES:
        lo_s, hi_s = avail[source]
        lo, hi = date.fromisoformat(lo_s), date.fromisoformat(hi_s)
        nrt = source.endswith("_NRT")
        for s in _chunk_starts(lo_s if nrt else MAIN_END.isoformat(), hi_s):
            if not nrt and s <= last_main:
                continue
            chunk_end = s + timedelta(days=CHUNK_DAYS - 1)
            fresh = nrt and (hi - chunk_end).days < REVISE_DAYS
            if fresh or not wg.out_path(source, s).exists():
                jobs.append((source, s, lo if nrt else s, hi))
    jobs.sort(key=lambda j: j[1], reverse=True)  # newest first
    return jobs


def download(source, s, lo, hi):
    """One 5-day chunk, limited to the days that exist (lo..hi)."""
    a, b = max(s, lo), min(s + timedelta(days=CHUNK_DAYS - 1), hi)
    if a > b:
        return pd.DataFrame(columns=EMPTY)
    key = os.environ["FIRMS_MAP_KEY"]
    url = f"{wg.BASE}{key}/{source}/world/{(b - a).days + 1}/{a.isoformat()}"
    r = requests.get(url, timeout=900)
    r.raise_for_status()
    text = r.text
    if "latitude" not in text[:300]:
        raise RuntimeError("Unexpected FIRMS answer: " + text[:80])
    lines = text.rstrip("\n").split("\n")
    if lines[-1].count(",") != lines[0].count(","):
        raise RuntimeError("Download was cut off, will retry")
    return pd.read_csv(io.StringIO(text), usecols=EMPTY)


def process(source, s, lo, hi):
    df = download(source, s, lo, hi)
    g = wg.to_cells(df) if len(df) else pd.DataFrame(columns=wg.COLUMNS)
    path = wg.out_path(source, s)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    g.to_csv(tmp, index=False, compression="gzip")
    os.replace(tmp, path)


def job(source, s, lo, hi):
    for attempt in range(4):
        budget.wait_for_budget()
        try:
            return process(source, s, lo, hi)
        except Exception:
            if attempt == 3:
                raise
            time.sleep(15)


def run():
    budget.COST_GUESS = 150
    key = os.environ["FIRMS_MAP_KEY"]
    avail = fetch_availability()
    for s in SOURCES:
        print(f"{s}: {avail[s][0]} .. {avail[s][1]}", flush=True)
    jobs = plan(avail)
    print(len(jobs), "parca indirilecek", flush=True)

    done, failed = 0, []
    t0 = time.time()
    with ThreadPoolExecutor(WORKERS) as ex:
        futs = {ex.submit(job, *j): (j[0], str(j[1])) for j in jobs}
        for f in as_completed(futs):
            done += 1
            try:
                f.result()
            except Exception as e:
                failed.append((futs[f], str(e).replace(key, "***")[:90]))
            if done % 5 == 0 or done == len(jobs):
                per_min = done / ((time.time() - t0) / 60)
                print(f"{done}/{len(jobs)} | {per_min:.1f} parca/dk | hata {len(failed)}", flush=True)
    print("Bitti.", len(failed), "hata", flush=True)
    for msg, n in Counter(m for _, m in failed).most_common(5):
        print(n, "x", msg)


if __name__ == "__main__":
    run()
