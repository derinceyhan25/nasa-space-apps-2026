"""
Harmonization logic for combining MODIS and VIIRS fire hotspot detections
into a single, sensor-consistent burning-activity time series.
"""
import pandas as pd
import numpy as np

VIIRS_START_DATE = "2012-01-19"


def assign_grid_cell(
    df: pd.DataFrame,
    cell_size_deg: float = 0.045,
    lat_col: str = "latitude",
    lon_col: str = "longitude",
) -> pd.DataFrame:
    df = df.copy()
    df["cell_lat"] = (df[lat_col] // cell_size_deg) * cell_size_deg
    df["cell_lon"] = (df[lon_col] // cell_size_deg) * cell_size_deg
    return df


def daily_cell_frp(
    df: pd.DataFrame,
    date_col: str = "acq_date",
) -> pd.DataFrame:
    grouped = (
        df.groupby([date_col, "cell_lat", "cell_lon", "source"])
        .agg(total_frp=("frp", "sum"), detection_count=("frp", "count"))
        .reset_index()
    )
    return grouped


def compute_calibration_ratio(
    daily_agg: pd.DataFrame,
    modis_source: str = "MODIS_NRT",
    viirs_sources: tuple[str, ...] = ("VIIRS_NOAA20_NRT", "VIIRS_SNPP_NRT"),
    date_col: str = "acq_date",
) -> float:
    dates = pd.to_datetime(daily_agg[date_col])
    overlap = daily_agg[dates >= pd.Timestamp(VIIRS_START_DATE)]

    modis_total = overlap.loc[overlap["source"] == modis_source, "total_frp"].sum()
    viirs_total = overlap.loc[
        overlap["source"].isin(viirs_sources), "total_frp"
    ].sum()

    if modis_total == 0:
        raise ValueError(
            "No MODIS FRP found in the overlap period - cannot compute ratio."
        )

    return viirs_total / modis_total


def build_harmonized_series(
    daily_agg: pd.DataFrame,
    ratio: float,
    modis_source: str = "MODIS_NRT",
    viirs_sources: tuple[str, ...] = ("VIIRS_NOAA20_NRT", "VIIRS_SNPP_NRT"),
    date_col: str = "acq_date",
) -> pd.DataFrame:
    dates = pd.to_datetime(daily_agg[date_col])
    is_pre_viirs = dates < pd.Timestamp(VIIRS_START_DATE)

    pre = daily_agg[is_pre_viirs & (daily_agg["source"] == modis_source)]
    pre_by_date = pre.groupby(date_col)["total_frp"].sum().reset_index()
    pre_by_date["harmonized_frp"] = pre_by_date["total_frp"] * ratio
    pre_by_date["estimated"] = True

    post = daily_agg[~is_pre_viirs]
    viirs_by_date = (
        post[post["source"].isin(viirs_sources)]
        .groupby(date_col)["total_frp"]
        .sum()
        .reset_index()
        .rename(columns={"total_frp": "viirs_frp"})
    )
    modis_by_date = (
        post[post["source"] == modis_source]
        .groupby(date_col)["total_frp"]
        .sum()
        .reset_index()
        .rename(columns={"total_frp": "modis_frp"})
    )
    post_merged = pd.merge(viirs_by_date, modis_by_date, on=date_col, how="outer").fillna(0)
    post_merged["harmonized_frp"] = np.where(
        post_merged["viirs_frp"] > 0,
        post_merged["viirs_frp"],
        post_merged["modis_frp"] * ratio,
    )
    post_merged["estimated"] = post_merged["viirs_frp"] == 0

    result = pd.concat(
        [
            pre_by_date[[date_col, "harmonized_frp", "estimated"]],
            post_merged[[date_col, "harmonized_frp", "estimated"]],
        ],
        ignore_index=True,
    ).sort_values(date_col)

    return result
