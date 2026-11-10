"""Pack worldgrid/{source}/*.csv.gz into one compact worldgrid/pack/{source}.npz per source.

Arrays (rows sorted by month, then cy, cx):
  m   int16   months since 2000-01 (0 = 2000-01)
  cx  int16   cell x index (0.25 deg)
  cy  int16   cell y index
  n   int32   hot-spot count
  frp float32 summed FRP
  months  int16  distinct month indices present
  offsets int64  row start for each entry of months (+ final end)
Usage: python -m app.pack            (all sources)
       python -m app.pack MODIS_SP   (one source)
"""
import csv, gzip, sys, time
from pathlib import Path

try:
    import numpy as np
except ImportError:
    raise SystemExit("numpy missing: run  pip install numpy  and try again")

ROOT = Path("worldgrid")
OUT = ROOT / "pack"


def mi(month):          # "2026-11" -> months since 2000-01
    y, m = month.split("-")
    return (int(y) - 2000) * 12 + int(m) - 1


def ms(i):              # inverse of mi
    return f"{2000 + i // 12}-{i % 12 + 1:02d}"


def pack_source(src):
    files = sorted((ROOT / src).glob("*.csv.gz"))
    if not files:
        print(f"{src}: no chunks, skipped")
        return
    t0 = time.time()
    open_months = {}            # month index -> {(cx, cy): [n, frp]}
    parts, raw_n, raw_rows = [], 0, 0

    def flush(upto):            # write every buffered month < upto
        for m in sorted(k for k in open_months if k < upto):
            cells = open_months.pop(m)
            keys = sorted(cells, key=lambda k: (k[1], k[0]))
            a = np.empty(len(keys), dtype=[("m", "i2"), ("cx", "i2"), ("cy", "i2"), ("n", "i4"), ("frp", "f4")])
            a["m"] = m
            a["cx"] = [k[0] for k in keys]
            a["cy"] = [k[1] for k in keys]
            a["n"] = [cells[k][0] for k in keys]
            a["frp"] = [cells[k][1] for k in keys]
            parts.append(a)

    for f in files:
        start_m = mi(f.name[:7])            # chunk file name starts with YYYY-MM-DD
        flush(start_m)                      # earlier months can no longer appear
        with gzip.open(f, "rt", newline="") as fh:
            for r in csv.DictReader(fh):
                m = mi(r["month"])
                key = (int(r["cx"]), int(r["cy"]))
                n = int(r["n"]); frp = float(r["frp"])
                cell = open_months.setdefault(m, {}).get(key)
                if cell is None:
                    open_months[m][key] = [n, frp]
                else:
                    cell[0] += n; cell[1] += frp
                raw_n += n; raw_rows += 1
    flush(10**6)

    a = np.concatenate(parts)
    order = np.argsort(a["m"], kind="stable")      # chunks may arrive out of month order
    a = a[order]
    months, offsets = np.unique(a["m"], return_index=True)
    offsets = np.append(offsets, len(a)).astype("i8")

    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / f"{src}.npz"
    starts = np.array(sorted(f.name[:10] for f in files))   # chunk start dates (YYYY-MM-DD)
    np.savez_compressed(out, m=a["m"], cx=a["cx"], cy=a["cy"], n=a["n"], frp=a["frp"],
                        months=months.astype("i2"), offsets=offsets, starts=starts)

    ok = int(a["n"].sum()) == raw_n
    print(f"{src}: {raw_rows:,} raw rows -> {len(a):,} packed rows, "
          f"{ms(int(months[0]))}..{ms(int(months[-1]))} ({len(months)} months), "
          f"{out.stat().st_size/1e6:.1f} MB, n check {'OK' if ok else 'MISMATCH'}, {time.time()-t0:.0f}s")


if __name__ == "__main__":
    srcs = sys.argv[1:] or sorted(p.name for p in ROOT.iterdir() if p.is_dir() and p.name != "pack")
    for s in srcs:
        pack_source(s)
