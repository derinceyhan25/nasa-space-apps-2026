"""
Region calendars computed from the world grid written by app/worldgrid.py
(and app/recent.py for the newest months).

A region (bounding box) is a block of 0.25-degree cells. For each sensor we sum
the monthly hotspot counts of those cells, convert MODIS counts to
VIIRS-equivalent counts with a ratio measured in the 2013-2019 overlap, and
compare every month with the median of the same calendar month.

Months whose 5-day chunks are not all downloaded yet are reported as missing
(null), never as zero. Months that come only from near-real-time (NRT) data are
flagged "provisional". The newest, unfinished month is reported separately as
"current_month".
"""
import calendar
import json
import threading
import time
from datetime import date, timedelta

import numpy as np
import pandas as pd

import app.worldgrid as wg
from app.archive import _chunk_starts
from app.monthly import VIIRS_FIRST_FULL_MONTH

MODIS, VIIRS = "MODIS", "VIIRS"
GROUPS = {MODIS: ["MODIS_SP", "MODIS_NRT"], VIIRS: ["VIIRS_SNPP_SP", "VIIRS_SNPP_NRT"]}
BASE_YEAR = 2000
OVERLAP = [f"{y}-{m:02d}" for y in range(2013, 2020) for m in range(1, 13)]
MIN_OVERLAP_HOTSPOTS = 300  # MODIS hotspots needed in the overlap for a stable ratio
MIN_OVERLAP_MONTHS = 24  # overlap months that must be downloaded for both sensors
WIDEN_CELLS = 16  # fallback window: 4 degrees around the region
ANOMALY_FACTOR = 1.5  # a month above 1.5 x its calendar-month median is flagged


class NotReady(Exception):
    """The world grid does not contain enough data yet."""


def month_name(i):
    return f"{BASE_YEAR + i // 12}-{i % 12 + 1:02d}"


def month_idx(iso):
    return (int(iso[:4]) - BASE_YEAR) * 12 + int(iso[5:7]) - 1


def bbox_cells(west, south, east, north):
    """Cell window (x0, x1, y0, y1) of all 0.25-degree cells touching the bbox."""
    g = wg.GRID_DEG
    x0 = int(np.floor((west + 180.0) / g))
    x1 = max(x0, int(np.ceil((east + 180.0) / g)) - 1)
    y0 = int(np.floor((south + 90.0) / g))
    y1 = max(y0, int(np.ceil((north + 90.0) / g)) - 1)
    clip = lambda v, hi: min(max(v, 0), hi)
    return clip(x0, wg.NX - 1), clip(x1, wg.NX - 1), clip(y0, wg.NY - 1), clip(y1, wg.NY - 1)


def widen(cells, k=WIDEN_CELLS):
    x0, x1, y0, y1 = cells
    return max(x0 - k, 0), min(x1 + k, wg.NX - 1), max(y0 - k, 0), min(y1 + k, wg.NY - 1)


WORLD = (0, wg.NX - 1, 0, wg.NY - 1)


class GridStore:
    """Keeps one consolidated table per source.

    Reads worldgrid/pack/{source}.npz (written by app/pack.py) when it exists and no
    csv.gz chunk is newer than it; otherwise reads the per-chunk csv.gz files. So a
    deployment only needs worldgrid/pack/ + worldgrid/availability.json, and locally
    a source falls back to the chunks after app/recent.py rewrites them (re-run
    app/pack.py to use the pack again)."""

    def __init__(self, directory=None, use_pack=True):
        self.dir = directory or wg.OUT_DIR
        self.use_pack = use_pack
        self._state = {}
        self._lock = threading.RLock()  # one refresh at a time (requests run in threads)

    # ---- which days exist ------------------------------------------------
    def ranges(self):
        """{source: (first_day, last_day)}; the last days come from availability.json."""
        r = {s: tuple(v) for s, v in wg.SOURCES.items()}
        try:
            av = json.loads((self.dir / "availability.json").read_text())
        except (OSError, ValueError):
            av = {}
        for s, (lo, hi) in av.items():
            r[s] = (r[s][0], hi) if s in wg.SOURCES else (lo, hi)
        return r

    # ---- reading files ---------------------------------------------------
    def refresh(self, source, max_age=60.0):
        with self._lock:
            return self._refresh(source, max_age)

    def _refresh(self, source, max_age):
        st = self._state.setdefault(
            source, {"files": {}, "starts": set(), "table": None, "t": 0.0}
        )
        if st["table"] is not None and time.time() - st["t"] < max_age:
            return st
        folder = self.dir / source
        found = {f.name: f for f in folder.glob("*.csv.gz")} if folder.exists() else {}
        if self.use_pack and self._load_pack(source, st, found):
            st["t"] = time.time()
            return st
        if st.get("pack") is not None:  # was reading the pack, now the chunks: start over
            st.update(files={}, starts=set(), table=None, pack=None)
        if any(n in st["files"] and f.stat().st_mtime_ns != st["files"][n] for n, f in found.items()):
            st["files"], st["starts"], st["table"] = {}, set(), None  # a chunk was replaced: rebuild
        new_parts, new_files = [], {}
        for name, f in found.items():
            if name in st["files"]:
                continue
            try:
                mtime = f.stat().st_mtime_ns
                g = pd.read_csv(f, usecols=["cx", "cy", "month", "n"])
            except Exception:
                continue  # unreadable file: leave it out this time, retry on the next refresh
            new_files[name] = mtime
            if len(g):
                new_parts.append(
                    pd.DataFrame(
                        {
                            "cx": g["cx"].astype("int16"),
                            "cy": g["cy"].astype("int16"),
                            "m": ((g["month"].str[:4].astype(int) - BASE_YEAR) * 12
                                  + g["month"].str[5:7].astype(int) - 1).astype("int16"),
                            "n": g["n"].astype("int32"),
                        }
                    )
                )
        if new_parts:
            parts = ([st["table"]] if st["table"] is not None else []) + new_parts
            t = pd.concat(parts, ignore_index=True)
            t = t.groupby(["cx", "cy", "m"], as_index=False)["n"].sum()
            st["table"] = t.astype({"cx": "int16", "cy": "int16", "m": "int16", "n": "int32"})
        elif st["table"] is None:
            st["table"] = pd.DataFrame(
                {"cx": pd.Series(dtype="int16"), "cy": pd.Series(dtype="int16"),
                 "m": pd.Series(dtype="int16"), "n": pd.Series(dtype="int32")}
            )
        # commit the file bookkeeping only now, together with the merged table, so that
        # "this chunk is downloaded" and "its rows are in the table" can never disagree
        st["files"].update(new_files)
        st["starts"].update(date.fromisoformat(n[:10]) for n in new_files)
        st["t"] = time.time()
        return st

    def _load_pack(self, source, st, found):
        """Use the pack if it is at least as new as every chunk. True if used."""
        p = self.dir / "pack" / f"{source}.npz"
        try:
            pm = p.stat().st_mtime_ns
        except OSError:
            return False
        if any(f.stat().st_mtime_ns > pm for f in found.values()):
            return False  # a chunk changed after packing: the pack is stale
        if st.get("pack") == pm:
            return True  # already loaded
        with np.load(p) as z:
            if "starts" not in z.files:
                print(f"{p}: made by an old app/pack.py, re-run python -m app.pack")
                return False
            st["table"] = pd.DataFrame(
                {"cx": z["cx"].astype("int16"), "cy": z["cy"].astype("int16"),
                 "m": z["m"].astype("int16"), "n": z["n"].astype("int32")}
            )
            st["starts"] = {date.fromisoformat(str(s)) for s in z["starts"]}
        st["files"], st["pack"] = {}, pm
        return True

    # ---- queries ---------------------------------------------------------
    def series_source(self, source, cells):
        """Monthly hotspot count of one source in the cell window ('YYYY-MM' -> count)."""
        t = self.refresh(source)["table"]
        x0, x1, y0, y1 = cells
        sel = t[(t["cx"] >= x0) & (t["cx"] <= x1) & (t["cy"] >= y0) & (t["cy"] <= y1)]
        lo, hi = self.ranges().get(source, (None, None))
        if lo:
            sel = sel[sel["m"] >= month_idx(lo)]
        if hi:
            sel = sel[sel["m"] <= month_idx(hi)]
        s = sel.groupby("m")["n"].sum().astype(float)
        s.index = [month_name(int(i)) for i in s.index]
        return s

    def series(self, group, cells):
        """Monthly count of a sensor (standard + near-real-time data together)."""
        rng = self.ranges()
        parts = [self.series_source(s, cells) for s in GROUPS[group] if s in rng]
        if not parts:
            return pd.Series(dtype=float)
        return pd.concat(parts).groupby(level=0).sum()

    def complete_months(self, group):
        """{month: provisional?} for months whose 5-day chunks are all downloaded."""
        rng, out = self.ranges(), {}
        for src in GROUPS[group]:
            if src not in rng:
                continue
            starts = self.refresh(src)["starts"]
            a, b = (date.fromisoformat(x) for x in rng[src])
            first = pd.Period(a, "M") + (0 if a.day == 1 else 1)
            last = pd.Period(b, "M") - (0 if (b + timedelta(days=1)).day == 1 else 1)
            if first > last:
                continue
            for p in pd.period_range(first, last, freq="M"):
                need = _chunk_starts(p.start_time.strftime("%Y-%m-%d"), p.end_time.strftime("%Y-%m-%d"))
                if all(s in starts for s in need):
                    out[str(p)] = src.endswith("_NRT")
        return out


def pick_ratio(store, cells, ok_m, ok_v):
    """VIIRS/MODIS count ratio: the region's own if it has enough hotspots,
    else a wider area, else the whole world. Returns (ratio, basis, months_used)."""
    months = [m for m in OVERLAP if m in ok_m and m in ok_v]
    if len(months) < MIN_OVERLAP_MONTHS:
        raise NotReady(
            f"Only {len(months)} of 84 overlap months (2013-2019) are downloaded yet "
            f"(need {MIN_OVERLAP_MONTHS})."
        )
    for basis, window in (("region", cells), ("wider area", widen(cells)), ("world", WORLD)):
        m = store.series(MODIS, window).reindex(months).fillna(0).sum()
        if m >= MIN_OVERLAP_HOTSPOTS:
            v = store.series(VIIRS, window).reindex(months).fillna(0).sum()
            return float(v / m), basis, len(months)
    raise NotReady("No hotspots found in the overlap period.")


def current_month(store, cells, ratio, medians):
    """The newest, unfinished month: hotspots so far and a rough full-month pace."""
    rng, best = store.ranges(), None
    for group in (VIIRS, MODIS):
        for src in GROUPS[group]:
            if not src.endswith("_NRT") or src not in rng:
                continue
            lo, hi = (date.fromisoformat(x) for x in rng[src])
            if (hi + timedelta(days=1)).day == 1:
                continue  # the month is finished: it is an ordinary calendar month
            ms = hi.replace(day=1)
            if lo > ms:
                continue
            starts = store.refresh(src)["starts"]
            if not all(s in starts for s in _chunk_starts(ms.isoformat(), hi.isoformat())):
                continue
            n = store.series_source(src, cells).get(ms.strftime("%Y-%m"), 0.0)
            cand = (hi.day, group == VIIRS, src, ms, hi, n * (1.0 if group == VIIRS else ratio), group != VIIRS)
            if best is None or cand[:2] > best[:2]:
                best = cand
    if best is None:
        return None
    days, _, src, ms, hi, count, est = best
    count = float(count)
    dim = calendar.monthrange(ms.year, ms.month)[1]
    med = medians.get(f"{ms.month:02d}")
    projected = count * dim / days
    return {
        "month": ms.strftime("%Y-%m"),
        "through": hi.isoformat(),
        "days_covered": int(days),
        "days_in_month": int(dim),
        "count": round(count, 1),
        "estimated": bool(est),
        "projected": round(projected, 1),
        "median": None if med is None else round(float(med), 1),
        "projected_vs_median": None if not med else round(projected / float(med), 2),
    }


def region_calendar(store, bbox):
    """Monthly harmonized calendar for bbox = (west, south, east, north)."""
    cells = bbox_cells(*bbox)
    ok_m, ok_v = store.complete_months(MODIS), store.complete_months(VIIRS)
    ratio, basis, n_overlap = pick_ratio(store, cells, ok_m, ok_v)
    modis, viirs = store.series(MODIS, cells), store.series(VIIRS, cells)

    avail = sorted(set(ok_m) | set(ok_v))
    if not avail:
        raise NotReady("No complete month downloaded yet.")
    rows = []
    for mo in pd.period_range(avail[0], avail[-1], freq="M").strftime("%Y-%m"):
        m, v = modis.get(mo, 0.0), viirs.get(mo, 0.0)
        if mo in ok_v and mo >= VIIRS_FIRST_FULL_MONTH and (v > 0 or m == 0 or mo not in ok_m):
            count, est, prov = v, False, ok_v[mo]
        elif mo in ok_m:
            count, est, prov = m * ratio, True, ok_m[mo]
        else:
            count, est, prov = None, None, None
        rows.append({"month": mo, "count": None if count is None else round(count, 1),
                     "estimated": est, "provisional": prov})

    df = pd.DataFrame(rows)
    df["median"] = df.groupby(df["month"].str[5:7])["count"].transform("median")
    df["vs_median"] = np.where(df["median"] > 0, df["count"] / df["median"], np.nan)
    df["vs_median"] = df["vs_median"].round(2)
    medians = df.groupby(df["month"].str[5:7])["count"].median().dropna().to_dict()
    df["median"] = df["median"].round(1)
    df["anomaly"] = df["vs_median"] >= ANOMALY_FACTOR
    return {
        "cells": cells,
        "ratio": round(ratio, 3),
        "ratio_basis": basis,
        "overlap_months": n_overlap,
        "months_with_data": int(df["count"].notna().sum()),
        "calendar": df.astype(object).where(df.notna(), None).to_dict(orient="records"),
        "current_month": current_month(store, cells, ratio, medians),
    }


def globe_months(store):
    """Months with complete worldwide VIIRS data (the globe time-lapse uses VIIRS only)."""
    return sorted(m for m in store.complete_months(VIIRS) if m >= VIIRS_FIRST_FULL_MONTH)


def globe_cells(store, month, factor=4, top=6000):
    """Hotspots of one month on a coarse world grid (factor x 0.25 degrees per cell),
    busiest cells first, as plain {lat, lng, n} dicts."""
    rng, m, parts = store.ranges(), month_idx(month), []
    for src in GROUPS[VIIRS]:
        if src not in rng:
            continue
        lo, hi = rng[src]
        if not (month_idx(lo) <= m <= month_idx(hi)):
            continue
        t = store.refresh(src)["table"]
        parts.append(t[t["m"] == m][["cx", "cy", "n"]])
    if not parts:
        return []
    t = pd.concat(parts)
    g = (
        t.assign(gx=t["cx"].astype(int) // factor, gy=t["cy"].astype(int) // factor)
        .groupby(["gx", "gy"], as_index=False)["n"].sum()
        .sort_values("n", ascending=False)
        .head(top)
    )
    size = factor * wg.GRID_DEG
    return [
        {"lat": round(float(gy * size - 90 + size / 2), 3),
         "lng": round(float(gx * size - 180 + size / 2), 3),
         "n": int(n)}
        for gx, gy, n in zip(g["gx"], g["gy"], g["n"])
    ]
