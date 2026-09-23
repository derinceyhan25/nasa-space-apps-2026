"""
Thin client for NASA FIRMS Area API.
Docs: https://firms.modaps.eosdis.nasa.gov/api/area/
"""
import os
import io
import requests
import pandas as pd

FIRMS_BASE_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"

# Sources we care about for the harmonization challenge.
# NRT = near real-time (recent), SP = standard processing (archive, more accurate)
SOURCES = {
    "modis": "MODIS_NRT",
    "viirs_noaa20": "VIIRS_NOAA20_NRT",
    "viirs_snpp": "VIIRS_SNPP_NRT",
}


def get_map_key() -> str:
    key = os.environ.get("FIRMS_MAP_KEY")
    if not key:
        raise RuntimeError(
            "FIRMS_MAP_KEY environment variable not set. "
            "Get one at https://firms.modaps.eosdis.nasa.gov/api/map_key/"
        )
    return key


def fetch_hotspots(
    bbox: tuple[float, float, float, float],
    source: str = "MODIS_NRT",
    day_range: int = 1,
    start_date: str | None = None,
) -> pd.DataFrame:
    """
    Fetch active fire hotspots for a bounding box.

    bbox: (west, south, east, north) in decimal degrees
    source: one of the FIRMS source codes, e.g. MODIS_NRT, VIIRS_NOAA20_NRT
    day_range: 1-5 days (FIRMS Area API hard limit)
    start_date: 'YYYY-MM-DD', optional (defaults to most recent available)
    """
    key = get_map_key()
    west, south, east, north = bbox
    coords = f"{west},{south},{east},{north}"

    url = f"{FIRMS_BASE_URL}/{key}/{source}/{coords}/{day_range}"
    if start_date:
        url += f"/{start_date}"

    resp = requests.get(url, timeout=30)
    resp.raise_for_status()

    text = resp.text
    if text.strip().lower().startswith(("invalid", "error")):
        raise RuntimeError(f"FIRMS API error: {text[:200]}")

    df = pd.read_csv(io.StringIO(text))
    df["source"] = source
    return df


if __name__ == "__main__":
    bbox = (14.0, 49.0, 24.2, 55.0)
    df = fetch_hotspots(bbox, source="MODIS_NRT", day_range=1)
    print(df.head())
    print(f"\n{len(df)} hotspots found")
    print(f"Columns: {list(df.columns)}")
