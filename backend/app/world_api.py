"""
World API: place name or coordinates -> bounding box -> monthly fire calendar
computed from the world grid (app/worldgrid.py + app/gridcalendar.py).

  GET /api/geocode?q=Izmir            -> {name, bbox, lat, lon}
  GET /api/geocode?q=38.4,27.1        -> a +-0.5 degree box around the point
  GET /api/world/calendar?west=&south=&east=&north=
  GET /api/world/status               -> how much of the grid is downloaded
"""
import re
import threading
import time

import requests
from fastapi import APIRouter, HTTPException, Query

from app.gridcalendar import GridStore, NotReady, globe_cells, globe_months, region_calendar

router = APIRouter()
store = GridStore()

NOMINATIM = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "fire-calendar-spaceapps/0.1"  # generic on purpose: no personal contact data
COORD = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*[,; ]\s*(-?\d+(?:\.\d+)?)\s*$")
HALF = 0.5  # half-width (degrees) of the box around a bare coordinate

_geo_cache = {}
_geo_lock = threading.Lock()
_last_call = [0.0]


def _bbox_around(lat, lon):
    return [max(lon - HALF, -180.0), max(lat - HALF, -90.0),
            min(lon + HALF, 180.0), min(lat + HALF, 90.0)]


def geocode(q):
    q = q.strip()
    m = COORD.match(q)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise HTTPException(400, "Latitude must be in [-90, 90] and longitude in [-180, 180].")
        return {"name": f"{lat:.3f}, {lon:.3f}", "lat": lat, "lon": lon, "bbox": _bbox_around(lat, lon)}
    key = q.lower()
    with _geo_lock:
        if key in _geo_cache:
            return _geo_cache[key]
        wait = 1.1 - (time.time() - _last_call[0])  # Nominatim policy: max 1 request/second
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.time()
        try:
            r = requests.get(NOMINATIM, params={"q": q, "format": "jsonv2", "limit": 1},
                             headers={"User-Agent": USER_AGENT}, timeout=15)
            r.raise_for_status()
            hits = r.json()
        except Exception:
            raise HTTPException(502, "The place search service did not answer. Try again, or enter lat,lon.")
        if not hits:
            raise HTTPException(404, f"No place found for '{q}'.")
        h = hits[0]
        s, n, w, e = (float(v) for v in h["boundingbox"])  # Nominatim: south, north, west, east
        out = {"name": h["display_name"], "lat": float(h["lat"]), "lon": float(h["lon"]),
               "bbox": [w, s, e, n]}
        _geo_cache[key] = out
        return out


@router.get("/api/geocode")
def api_geocode(q: str = Query(..., min_length=1, max_length=200)):
    return geocode(q)


@router.get("/api/world/calendar")
def api_world_calendar(west: float, south: float, east: float, north: float):
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise HTTPException(400, "Invalid box: need -180<=west<east<=180 and -90<=south<north<=90.")
    try:
        res = region_calendar(store, (west, south, east, north))
    except NotReady as e:
        raise HTTPException(409, str(e))
    res["bbox"] = [west, south, east, north]
    res["cells"] = list(res["cells"])
    return res


@router.get("/api/world/status")
def api_world_status():
    from app.gridcalendar import MODIS, VIIRS
    return {"modis_months": len(store.complete_months(MODIS)),
            "viirs_months": len(store.complete_months(VIIRS))}


_globe_cache = {}


@router.get("/api/world/globe/months")
def api_globe_months():
    return {"months": globe_months(store)}


@router.get("/api/world/globe")
def api_globe(month: str = Query(..., pattern=r"^\d{4}-\d{2}$")):
    if month not in globe_months(store):
        raise HTTPException(404, f"No complete worldwide data for {month} yet.")
    hit = _globe_cache.get(month)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    out = {"month": month, "cell_deg": 1.0, "cells": globe_cells(store, month)}
    _globe_cache[month] = (time.time(), out)
    return out
