"""
Calendar API routes: monthly harmonized hotspot calendar + CSV download.

GET /api/calendar?west=..&south=..&east=..&north=..
GET /api/hotspots.csv?west=..&south=..&east=..&north=..&start=YYYY-MM-DD&end=YYYY-MM-DD

The 25-year calendar needs ~3000 chunks per region. Those are downloaded once
by `python -m app.prefetch`; the API refuses to start a huge live download.
"""
import io
import json
from datetime import date, timedelta

import pandas as pd
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.archive import _cache_path, _chunk_starts, fetch_range
from app.monthly import build_calendar, count_ratio, monthly_counts

router = APIRouter()

MODIS, VIIRS = "MODIS_SP", "VIIRS_SNPP_SP"
MODIS_START, VIIRS_START = "2000-11-11", "2012-01-20"
CAL_START = "2000-12"  # 2000-11 is a partial month
OVERLAP = [f"{y}-{m:02d}" for y in range(2013, 2020) for m in range(1, 13)]
LAG_DAYS = 150  # standard-processing data arrives with a lag of several months
MAX_LIVE_CHUNKS = 60  # more missing chunks than this -> ask for prefetch
MAX_CSV_DAYS = 366

_calendar_cache = {}


def _bbox(west, south, east, north):
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise HTTPException(400, "Invalid bounding box (need west<east, south<north)")
    return tuple(round(float(x), 4) for x in (west, south, east, north))


def _missing_chunks(bbox, source, start, end):
    return sum(1 for s in _chunk_starts(start, end) if not _cache_path(source, bbox, s).exists())


def _last_complete_month():
    return str(pd.Period(date.today() - timedelta(days=LAG_DAYS), "M") - 1)


@router.get("/api/calendar")
def calendar(west: float, south: float, east: float, north: float):
    bbox = _bbox(west, south, east, north)
    end_month = _last_complete_month()
    end_day = pd.Period(end_month, "M").end_time.strftime("%Y-%m-%d")

    key = (bbox, end_month)
    if key not in _calendar_cache:
        missing = _missing_chunks(bbox, MODIS, MODIS_START, end_day) + _missing_chunks(
            bbox, VIIRS, VIIRS_START, end_day
        )
        if missing > MAX_LIVE_CHUNKS:
            raise HTTPException(
                409,
                f"This region is not prefetched yet: {missing} chunks missing "
                f"(~{round(missing / 1.2 / 60)} min of downloading). "
                "Add it to REGIONS in app/prefetch.py and run the prefetch.",
            )
        modis = monthly_counts(fetch_range(bbox, MODIS, MODIS_START, end_day, workers=6))
        viirs = monthly_counts(fetch_range(bbox, VIIRS, VIIRS_START, end_day, workers=6))
        try:
            ratio = count_ratio(modis, viirs, OVERLAP)
        except ValueError as err:
            raise HTTPException(422, f"Cannot calibrate for this region: {err}")
        _calendar_cache[key] = (ratio, build_calendar(modis, viirs, ratio, CAL_START, end_month))

    ratio, cal = _calendar_cache[key]
    return {
        "bbox": bbox,
        "ratio": round(ratio, 3),
        "overlap": "2013-2019",
        "months": len(cal),
        "calendar": json.loads(cal.to_json(orient="records")),  # NaN -> null
    }


@router.get("/api/hotspots.csv")
def hotspots_csv(west: float, south: float, east: float, north: float, start: str, end: str):
    bbox = _bbox(west, south, east, north)
    try:
        d0, d1 = pd.Timestamp(start), pd.Timestamp(end)
    except ValueError:
        raise HTTPException(400, "start/end must be YYYY-MM-DD")
    if d0 > d1:
        raise HTTPException(400, "start must be before end")
    if (d1 - d0).days + 1 > MAX_CSV_DAYS:
        raise HTTPException(400, f"Range too long (max {MAX_CSV_DAYS} days per download)")

    frames = []
    for source, first in ((MODIS, MODIS_START), (VIIRS, VIIRS_START)):
        s = max(start, first)
        if s <= end:
            if _missing_chunks(bbox, source, s, end) > MAX_LIVE_CHUNKS:
                raise HTTPException(409, "Too many chunks to download live for this request")
            df = fetch_range(bbox, source, s, end, workers=6)
            if len(df):
                frames.append(df)

    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    buf = io.StringIO()
    out.to_csv(buf, index=False)
    return Response(
        buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="hotspots.csv"'},
    )
