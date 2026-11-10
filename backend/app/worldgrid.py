"""
World-wide monthly grid of fire hotspot counts.

For every 5-day chunk of the FIRMS archive (whole world) we download the
hotspots, count them per 0.25-degree cell and calendar month, and save only
that small table (gzip CSV). The raw hotspots are not kept. Any region can
later be answered by summing the cells that fall inside it.

The newest data is downloaded first, so a usable 2013-today calendar exists
long before the older MODIS-only years are finished.
Safe to stop (Ctrl+C) and re-run: finished chunks are skipped.
Usage:  python -m app.worldgrid
"""
import io
import os
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

import pandas as pd
import requests

import app.prefetch as budget
from app.archive import CHUNK_DAYS, _chunk_starts

GRID_DEG = 0.25
NX, NY = 1440, 720  # cells along longitude / latitude
OUT_DIR = Path(__file__).resolve().parent.parent / "worldgrid"
BASE = "https://firms.modaps.eosdis.nasa.gov/api/area/csv/"
SOURCES = {
    "MODIS_SP": ("2000-11-11", "2026-05-31"),
    "VIIRS_SNPP_SP": ("2012-01-20", "2026-05-31"),
}
WORKERS = 4
COLUMNS = ["cx", "cy", "month", "n", "frp"]


def out_path(source, s):
    return OUT_DIR / source / f"{s.isoformat()}.csv.gz"


def to_cells(df):
    """Count hotspots (and sum FRP) per 0.25-degree cell and calendar month."""
    cx = ((df["longitude"] + 180.0) // GRID_DEG).astype("int32").clip(0, NX - 1)
    cy = ((df["latitude"] + 90.0) // GRID_DEG).astype("int32").clip(0, NY - 1)
    g = (
        pd.DataFrame({"cx": cx, "cy": cy, "month": df["acq_date"].str[:7], "frp": df["frp"]})
        .groupby(["cx", "cy", "month"])
        .agg(n=("frp", "size"), frp=("frp", "sum"))
        .reset_index()
    )
    g["frp"] = g["frp"].round(1)
    return g


def download_chunk(source, s):
    key = os.environ["FIRMS_MAP_KEY"]
    url = f"{BASE}{key}/{source}/world/{CHUNK_DAYS}/{s.isoformat()}"
    r = requests.get(url, timeout=900)
    r.raise_for_status()
    if "latitude" not in r.text[:300]:
        raise RuntimeError("Unexpected FIRMS answer: " + r.text[:80])
    return pd.read_csv(io.StringIO(r.text), usecols=["latitude", "longitude", "acq_date", "frp"])


def process(source, s):
    path = out_path(source, s)
    if path.exists():
        return
    df = download_chunk(source, s)
    g = to_cells(df) if len(df) else pd.DataFrame(columns=COLUMNS)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")  # write then rename: no half-written files
    g.to_csv(tmp, index=False, compression="gzip")
    os.replace(tmp, path)


def job(source, s):
    for attempt in range(4):
        budget.wait_for_budget()
        try:
            return process(source, s)
        except Exception:
            if attempt == 3:
                raise
            time.sleep(15)


def run():
    budget.COST_GUESS = 150  # a world request is expensive: be conservative
    key = os.environ["FIRMS_MAP_KEY"]

    jobs = []
    for source, (a, b) in SOURCES.items():
        for s in _chunk_starts(a, b):
            if not out_path(source, s).exists():
                jobs.append((source, s))
    # 2013-today first (newest first), then the older MODIS-only years
    jobs.sort(key=lambda j: (0 if j[1] >= date(2013, 1, 1) else 1, -j[1].toordinal()))
    print(len(jobs), "parca indirilecek", flush=True)

    t0 = time.time()
    done, failed = 0, []
    with ThreadPoolExecutor(WORKERS) as ex:
        futs = {ex.submit(job, source, s): (source, str(s)) for source, s in jobs}
        for f in as_completed(futs):
            done += 1
            try:
                f.result()
            except Exception as e:
                failed.append((futs[f], str(e).replace(key, "***")[:90]))
            if done % 10 == 0:
                per_min = done / ((time.time() - t0) / 60)
                left_h = (len(jobs) - done) / per_min / 60
                print(
                    f"{done}/{len(jobs)} | {per_min:.1f} parca/dk | islem ~{budget._state['used']}/5000"
                    f" | hata {len(failed)} | kalan ~{left_h:.1f} saat",
                    flush=True,
                )

    print("Bitti.", len(failed), "hata", flush=True)
    for msg, n in Counter(m for _, m in failed).most_common(5):
        print(n, "x", msg)


if __name__ == "__main__":
    run()
