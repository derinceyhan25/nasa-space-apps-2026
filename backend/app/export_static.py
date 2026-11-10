"""
Pre-computes everything the static (GitHub Pages) site needs, with the same
harmonization as app/gridcalendar.py, so the site needs no server.

Writes to ../site/data/:
  regions/{id}.json   calendar of every country and state/province (regions.json.gz)
  grid/{tx}_{ty}.json calendars of all 1x1-degree cells with fires, grouped in 10x10-degree tiles
  globe/months.json, globe/{YYYY-MM}.json   time-lapse cells (same as /api/world/globe)
  search.json         names for the search box (regions + cities)
  meta.json           ratio, data ranges, build time

Calendar files are compact: one count per month from "start", flags as strings
("1"/"0"/"-" per month). The site derives median, vs_median and anomaly exactly
like region_calendar() does. expand() below is the Python version of that.

Usage:  python -m app.export_static            (everything)
        python -m app.export_static --check    (only compare with region_calendar)
"""
import gzip
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import app.gridcalendar as gc
from app.monthly import VIIRS_FIRST_FULL_MONTH

OUT = Path("..") / "site" / "data"
REGIONS_FILE = Path("regions.json.gz")
CELL = 4  # 1 degree = 4 grid cells of 0.25 degree
TILE = 10  # 1-degree cells per tile side
NGX, NGY = gc.wg.NX // CELL, gc.wg.NY // CELL


# ---------------------------------------------------------------- inputs
def load_regions():
    d = json.loads(gzip.open(REGIONS_FILE, "rt", encoding="utf-8").read())
    for r in d["regions"]:
        run = r["runs"]
        ys, xs = [], []
        for i in range(0, len(run), 3):
            cy, x0, x1 = run[i:i + 3]
            xs.extend(range(x0, x1 + 1)); ys.extend([cy] * (x1 - x0 + 1))
        r["cell_ids"] = np.array(ys, dtype=np.int64) * gc.wg.NX + np.array(xs, dtype=np.int64)
    return d


def source_tables(store):
    """Each source's table limited to its date range (same filter as series_source)."""
    rng, out = store.ranges(), {}
    for group in (gc.MODIS, gc.VIIRS):
        for src in gc.GROUPS[group]:
            if src not in rng:
                continue
            t = store.refresh(src)["table"]
            lo, hi = rng[src]
            t = t[(t["m"] >= gc.month_idx(lo)) & (t["m"] <= gc.month_idx(hi))]
            out[src] = t.assign(cid=t["cy"].astype(np.int64) * gc.wg.NX + t["cx"].astype(np.int64))
    return out


def series_by_key(tables, keymap):
    """{src: {key: {month_idx: n}}} where keymap is a DataFrame cid -> key (one cid may map to many keys)."""
    out = {}
    for src, t in tables.items():
        j = t[["cid", "m", "n"]].merge(keymap, on="cid")
        g = j.groupby(["key", "m"])["n"].sum()
        d = {}
        for (k, m), n in g.items():
            d.setdefault(k, {})[int(m)] = float(n)
        out[src] = d
    return out


# ---------------------------------------------------------------- the calendar
class Proxy:
    """Just enough of GridStore for gc.current_month() on a pre-grouped region."""

    def __init__(self, store, per_src):
        self.store, self.per_src = store, per_src

    def ranges(self):
        return self.store.ranges()

    def refresh(self, src, max_age=60.0):
        return self.store.refresh(src, max_age)

    def series_source(self, src, key):
        d = self.per_src.get(src, {}).get(key, {})
        return {gc.month_name(m): n for m, n in d.items()}


def group_series(per_src, key, group):
    s = {}
    for src in gc.GROUPS[group]:
        for m, n in per_src.get(src, {}).get(key, {}).items():
            s[m] = s.get(m, 0.0) + n
    return s


class Ratio:
    """Overlap totals per 0.25-degree cell, with prefix sums for window queries."""

    def __init__(self, tables, ok_m, ok_v):
        months = [m for m in gc.OVERLAP if m in ok_m and m in ok_v]
        if len(months) < gc.MIN_OVERLAP_MONTHS:
            raise gc.NotReady(f"Only {len(months)} overlap months downloaded.")
        self.n_months = len(months)
        idx = {gc.month_idx(m) for m in months}
        self.grid, self.cum = {}, {}
        for group in (gc.MODIS, gc.VIIRS):
            g = np.zeros((gc.wg.NY, gc.wg.NX))
            for src in gc.GROUPS[group]:
                if src in tables:
                    t = tables[src]
                    t = t[t["m"].isin(idx)]
                    np.add.at(g, (t["cy"].to_numpy(int), t["cx"].to_numpy(int)), t["n"].to_numpy(float))
            self.grid[group] = g
            c = np.zeros((gc.wg.NY + 1, gc.wg.NX + 1))
            c[1:, 1:] = g.cumsum(0).cumsum(1)
            self.cum[group] = c

    def window(self, group, w):
        x0, x1, y0, y1 = w
        c = self.cum[group]
        return c[y1 + 1, x1 + 1] - c[y0, x1 + 1] - c[y1 + 1, x0] + c[y0, x0]

    def cells(self, group, cids):
        return float(self.grid[group].ravel()[cids].sum())

    def pick(self, cids, window):
        """Same order as gc.pick_ratio: region, wider area, world."""
        m = self.cells(gc.MODIS, cids)
        if m >= gc.MIN_OVERLAP_HOTSPOTS:
            return self.cells(gc.VIIRS, cids) / m, "region"
        for basis, w in (("wider area", gc.widen(window)), ("world", gc.WORLD)):
            m = self.window(gc.MODIS, w)
            if m >= gc.MIN_OVERLAP_HOTSPOTS:
                return self.window(gc.VIIRS, w) / m, basis
        raise gc.NotReady("No hotspots found in the overlap period.")


def compact_calendar(store, proxy, per_src, key, ratio, basis, n_overlap, ok_m, ok_v, months):
    """region_calendar() rows, stored compactly."""
    modis, viirs = group_series(per_src, key, gc.MODIS), group_series(per_src, key, gc.VIIRS)
    counts, est, prov = [], [], []
    for mo in months:
        i = gc.month_idx(mo)
        m, v = modis.get(i, 0.0), viirs.get(i, 0.0)
        if mo in ok_v and mo >= VIIRS_FIRST_FULL_MONTH and (v > 0 or m == 0 or mo not in ok_m):
            c, e, p = v, False, ok_v[mo]
        elif mo in ok_m:
            c, e, p = m * ratio, True, ok_m[mo]
        else:
            c, e, p = None, None, None
        counts.append(None if c is None else round(c, 1))
        est.append("-" if e is None else "1" if e else "0")
        prov.append("-" if p is None else "1" if p else "0")
    medians = {}
    for k in range(12):
        vals = [c for c, mo in zip(counts, months) if c is not None and int(mo[5:7]) == k + 1]
        if vals:
            medians[f"{k + 1:02d}"] = statistics.median(vals)
    return {
        "ratio": round(ratio, 3), "ratio_basis": basis, "overlap_months": n_overlap,
        "start": months[0], "count": [None if c is None else (int(c) if c == int(c) else c) for c in counts],
        "est": "".join(est), "prov": "".join(prov),
        "current_month": gc.current_month(proxy, key, ratio, medians),
    }


def expand(c):
    """Compact calendar -> region_calendar()'s 'calendar' rows (mirrored in the site's JS)."""
    months = pd.period_range(c["start"], periods=len(c["count"]), freq="M").strftime("%Y-%m")
    by_cal = {}
    for mo, n in zip(months, c["count"]):
        if n is not None:
            by_cal.setdefault(mo[5:7], []).append(float(n))
    med = {k: statistics.median(v) for k, v in by_cal.items()}
    rows = []
    flag = {"1": True, "0": False, "-": None}
    for i, (mo, n) in enumerate(zip(months, c["count"])):
        md = med.get(mo[5:7])
        # numpy rounding (half to even after x100), as in region_calendar
        vs = None if n is None or md is None or md <= 0 else float(np.round(float(n) / md, 2))
        rows.append({"month": mo, "count": None if n is None else float(n),
                     "estimated": flag[c["est"][i]], "provisional": flag[c["prov"][i]],
                     "median": None if md is None else float(np.round(md, 1)), "vs_median": vs,
                     "anomaly": vs is not None and vs >= gc.ANOMALY_FACTOR})
    return rows


# ---------------------------------------------------------------- build
def build(store, check_only=False):
    t0 = time.time()
    ok_m, ok_v = store.complete_months(gc.MODIS), store.complete_months(gc.VIIRS)
    avail = sorted(set(ok_m) | set(ok_v))
    months = list(pd.period_range(avail[0], avail[-1], freq="M").strftime("%Y-%m"))
    tables = source_tables(store)
    ratio = Ratio(tables, ok_m, ok_v)
    print(f"tables ready ({time.time() - t0:.0f}s), {len(months)} months, {ratio.n_months} overlap months")

    # 1-degree grid: key = gy * NGX + gx
    cids = np.concatenate([t["cid"].unique() for t in tables.values()])
    cids = np.unique(cids)
    gkey = (cids // gc.wg.NX // CELL) * NGX + (cids % gc.wg.NX) // CELL
    grid_per_src = series_by_key(tables, pd.DataFrame({"cid": cids, "key": gkey}))
    gproxy = Proxy(store, grid_per_src)

    def grid_cal(key):
        gy, gx = divmod(int(key), NGX)
        w = (gx * CELL, gx * CELL + CELL - 1, gy * CELL, gy * CELL + CELL - 1)
        cell_ids = np.array([y * gc.wg.NX + x for y in range(w[2], w[3] + 1) for x in range(w[0], w[1] + 1)])
        r, basis = ratio.pick(cell_ids, w)
        c = compact_calendar(store, gproxy, grid_per_src, key, r, basis, ratio.n_months, ok_m, ok_v, months)
        lat0, lng0 = gy - 90, gx - 180
        c.update(id=f"g{gx}_{gy}", name=f"{abs(lat0 + .5):.1f}°{'N' if lat0 >= 0 else 'S'}, "
                 f"{abs(lng0 + .5):.1f}°{'E' if lng0 >= 0 else 'W'} (1°×1° cell)",
                 kind="cell", bbox=[lng0, lat0, lng0 + 1, lat0 + 1])
        return c

    gkeys = sorted({int(k) for d in grid_per_src.values() for k in d})

    # ---- check against the live code on a few 1-degree cells
    sample = gkeys[:: max(1, len(gkeys) // 12)][:12]
    bad = 0
    for key in sample:
        c = grid_cal(key)
        ref = gc.region_calendar(store, tuple(c["bbox"]))
        same = (expand(c) == ref["calendar"] and c["ratio"] == ref["ratio"]
                and c["ratio_basis"] == ref["ratio_basis"] and c["current_month"] == ref["current_month"])
        if not same:
            bad += 1
            print("  DIFFERENT:", c["name"])
    print(f"check: {len(sample) - bad}/{len(sample)} sample cells identical to region_calendar()")
    if bad:
        raise SystemExit("Stopping: the static calendars would differ from the server.")
    if check_only:
        return

    for sub in ("regions", "grid", "globe"):
        (OUT / sub).mkdir(parents=True, exist_ok=True)
    dump = lambda p, obj: p.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # ---- grid tiles
    tiles = {}
    for key in gkeys:
        gy, gx = divmod(key, NGX)
        tiles.setdefault(f"{gx // TILE}_{gy // TILE}", {})[f"{gx}_{gy}"] = grid_cal(key)
    for name, cells in tiles.items():
        dump(OUT / "grid" / f"{name}.json", cells)
    print(f"grid: {len(gkeys):,} cells in {len(tiles)} tiles ({time.time() - t0:.0f}s)")

    # ---- countries and states
    reg = load_regions()
    rows = [(i, cid) for i, r in enumerate(reg["regions"]) for cid in r["cell_ids"]]
    keymap = pd.DataFrame(rows, columns=["key", "cid"])
    reg_per_src = series_by_key(tables, keymap)
    rproxy = Proxy(store, reg_per_src)
    search = []
    for i, r in enumerate(reg["regions"]):
        w = gc.bbox_cells(*r["bbox"])
        rt, basis = ratio.pick(r["cell_ids"], w)
        c = compact_calendar(store, rproxy, reg_per_src, i, rt, basis, ratio.n_months, ok_m, ok_v, months)
        cx, cy = r["cell_ids"] % gc.wg.NX, r["cell_ids"] // gc.wg.NX
        center = [round(float(np.median(cx)) * gc.wg.GRID_DEG - 180 + gc.wg.GRID_DEG / 2, 2),
                  round(float(np.median(cy)) * gc.wg.GRID_DEG - 90 + gc.wg.GRID_DEG / 2, 2)]
        c.update(id=r["id"], name=r["name"], kind=r["kind"], country=r["country"], bbox=r["bbox"],
                 ncells=r["ncells"], center=center)
        dump(OUT / "regions" / f"{r['id']}.json", c)
        search.append([r["name"], r["country"] if r["kind"] == "state" else None, r["id"], r["bbox"], center])
    print(f"regions: {len(reg['regions']):,} ({time.time() - t0:.0f}s)")

    # ---- globe time-lapse
    gm = gc.globe_months(store)
    dump(OUT / "globe" / "months.json", gm)
    for mo in gm:
        dump(OUT / "globe" / f"{mo}.json", gc.globe_cells(store, mo))
    print(f"globe: {len(gm)} months ({time.time() - t0:.0f}s)")

    # ---- search index + meta
    dump(OUT / "search.json", {"regions": search, "cities": [c[:5] for c in reg["cities"]]})
    dump(OUT / "meta.json", {
        "built": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "ranges": store.ranges(), "months": [months[0], months[-1]],
        "overlap_months": ratio.n_months,
        "world_ratio": round(ratio.window(gc.VIIRS, gc.WORLD) / ratio.window(gc.MODIS, gc.WORLD), 3),
        "grid_deg": 1, "tile": TILE, "regions_source": reg["source"],
    })
    size = sum(f.stat().st_size for f in OUT.rglob("*.json")) / 1e6
    print(f"done: {size:.0f} MB in {OUT.resolve()} ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    build(gc.GridStore(), check_only="--check" in sys.argv)
