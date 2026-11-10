"""Builds regions.json.gz: Natural Earth countries + states/provinces as sets of 0.25-degree
grid cells (cell centre inside the polygon), plus a city list for search.
Downloads the Natural Earth GeoJSON files (public domain) into ne/ the first time.
Usage: python build_regions.py   (needs: pip install shapely)"""
import gzip, json, re, unicodedata, urllib.request
from pathlib import Path
from collections import Counter
import numpy as np
import shapely
from shapely.geometry import shape

G, NX, NY = 0.25, 1440, 720
NE = Path("ne")
URL = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/{}.geojson"


def ne(name):
    f = NE / f"{name}.geojson"
    if not f.exists():
        NE.mkdir(exist_ok=True)
        print("downloading", name, "...")
        urllib.request.urlretrieve(URL.format(name), f)
    return json.loads(f.read_text(encoding="utf-8"))["features"]


def cells_of(geom):
    w, s, e, n = geom.bounds
    x0, x1 = int(np.floor((w + 180) / G)), min(int(np.ceil((e + 180) / G)) - 1, NX - 1)
    y0, y1 = int(np.floor((s + 90) / G)), min(int(np.ceil((n + 90) / G)) - 1, NY - 1)
    xs, ys = np.meshgrid(np.arange(x0, x1 + 1), np.arange(y0, y1 + 1))
    xs, ys = xs.ravel(), ys.ravel()
    shapely.prepare(geom)
    inside = shapely.contains_xy(geom, xs * G - 180 + G / 2, ys * G - 90 + G / 2)
    xs, ys = xs[inside], ys[inside]
    if len(xs) == 0:  # smaller than one cell: take the cell under a point inside it
        p = geom.representative_point()
        xs = np.array([min(int((p.x + 180) / G), NX - 1)]); ys = np.array([min(int((p.y + 90) / G), NY - 1)])
    return xs, ys


def runs(xs, ys):
    """Row runs [cy, x0, x1, cy, x0, x1, ...]."""
    out = []
    order = np.lexsort((xs, ys))
    xs, ys = xs[order], ys[order]
    i = 0
    while i < len(xs):
        j = i
        while j + 1 < len(xs) and ys[j + 1] == ys[i] and xs[j + 1] == xs[j] + 1:
            j += 1
        out += [int(ys[i]), int(xs[i]), int(xs[j])]
        i = j + 1
    return out


def slug(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "x"


def bbox(xs, ys):
    return [round(float(xs.min() * G - 180), 2), round(float(ys.min() * G - 90), 2),
            round(float((xs.max() + 1) * G - 180), 2), round(float((ys.max() + 1) * G - 90), 2)]


regions, used = [], Counter()
def add(rid, name, kind, country, geom, extra=None):
    xs, ys = cells_of(geom)
    used[rid] += 1
    if used[rid] > 1:
        rid = f"{rid}-{used[rid]}"
    r = {"id": rid, "name": name, "kind": kind, "country": country,
         "bbox": bbox(xs, ys), "ncells": int(len(xs)), "runs": runs(xs, ys)}
    if extra: r.update(extra)
    regions.append(r)

for f in ne("ne_10m_admin_0_countries"):
    p = f["properties"]
    add("c-" + slug(p["NAME"]), p["NAME"], "country", p["NAME"], shape(f["geometry"]),
        {"iso2": p["ISO_A2"] if p["ISO_A2"] != "-99" else None})
for f in ne("ne_10m_admin_1_states_provinces"):
    p = f["properties"]
    name = p.get("name_en") or p.get("name") or "?"
    add("s-" + slug(p["admin"]) + "-" + slug(name), name, "state", p["admin"], shape(f["geometry"]),
        {"type": p.get("type_en")})

cities = []
for f in ne("ne_10m_populated_places_simple"):
    p = f["properties"]
    cities.append([p["name"], p["adm0name"], p.get("adm1name"), round(p["latitude"], 3),
                   round(p["longitude"], 3), int(p.get("pop_max") or 0)])
cities.sort(key=lambda c: -c[5])

with gzip.open("regions.json.gz", "wt", encoding="utf-8") as fh:
    json.dump({"grid_deg": G, "source": "Natural Earth 1:10m (public domain)",
               "regions": regions, "cities": cities}, fh, ensure_ascii=False, separators=(",", ":"))
print(len(regions), "regions,", len(cities), "cities, dup ids:", sum(v > 1 for v in used.values()))
for r in regions:
    if r["name"] in ("California", "Turkey", "Türkiye", "Victoria", "Amazonas", "Portugal", "France", "Russia"):
        print(r["id"], r["kind"], r["country"], r["bbox"], r["ncells"])
