"""
Fire Calendar API.

GET /api/series?west=..&south=..&east=..&north=..&start=YYYY-MM-DD&end=YYYY-MM-DD
Returns one harmonized (VIIRS-equivalent) FRP value per day for the region.
"""
import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.archive import fetch_range
from app.harmonize import (
    VIIRS_START_DATE,
    build_harmonized_series,
    daily_totals,
    fit_power_law,
)

MODIS = "MODIS_SP"
VIIRS = "VIIRS_SNPP_SP"
FIT_YEAR = 2015
FIT_MONTHS = [("02-01", "02-28"), ("05-01", "05-31"), ("08-01", "08-31"), ("11-01", "11-30")]
MAX_DAYS = 400  # live-fetch limit for now (5-day chunks => many requests)

_fit_cache = {}

app = FastAPI(title="Fire Calendar API")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


def get_fit(bbox):
    """Fit the MODIS->VIIRS power law for this region (cached)."""
    if bbox in _fit_cache:
        return _fit_cache[bbox]
    m_parts, v_parts = [], []
    for s, e in FIT_MONTHS:
        m_parts.append(fetch_range(bbox, MODIS, f"{FIT_YEAR}-{s}", f"{FIT_YEAR}-{e}"))
        v_parts.append(fetch_range(bbox, VIIRS, f"{FIT_YEAR}-{s}", f"{FIT_YEAR}-{e}"))
    fit = fit_power_law(
        daily_totals(pd.concat(m_parts, ignore_index=True)),
        daily_totals(pd.concat(v_parts, ignore_index=True)),
    )
    _fit_cache[bbox] = fit
    return fit


@app.get("/api/series")
def series(west: float, south: float, east: float, north: float, start: str, end: str):
    try:
        d0, d1 = pd.Timestamp(start), pd.Timestamp(end)
    except ValueError:
        raise HTTPException(400, "start/end must be YYYY-MM-DD")
    if d0 > d1:
        raise HTTPException(400, "start must be before end")
    if (d1 - d0).days + 1 > MAX_DAYS:
        raise HTTPException(400, f"Range too long (max {MAX_DAYS} days for now)")

    bbox = (west, south, east, north)
    try:
        fit = get_fit(bbox)
    except ValueError as err:
        raise HTTPException(422, f"Cannot fit model for this region: {err}")

    modis = daily_totals(fetch_range(bbox, MODIS, start, end))
    viirs_start = max(start, VIIRS_START_DATE)
    if viirs_start <= end:
        viirs = daily_totals(fetch_range(bbox, VIIRS, viirs_start, end))
    else:
        viirs = pd.Series(dtype=float)

    df = build_harmonized_series(modis, viirs, fit, start, end)
    df["harmonized_frp"] = df["harmonized_frp"].round(2)
    return {
        "bbox": bbox,
        "fit": {k: round(v, 3) for k, v in fit.items()},
        "days": len(df),
        "series": df.to_dict(orient="records"),
    }
