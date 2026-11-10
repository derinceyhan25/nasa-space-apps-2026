"""
Monthly harmonized hotspot counts and comparison with the historical median.

- Months with full VIIRS coverage (2012-02 onwards): the real VIIRS count.
- Earlier months (MODIS only): MODIS count * ratio, flagged estimated=True.
  The ratio = VIIRS count / MODIS count over a multi-year overlap period.
"""
import numpy as np
import pandas as pd

VIIRS_FIRST_FULL_MONTH = "2012-02"


def monthly_counts(df):
    """Hotspot count per 'YYYY-MM' as a Series (empty input -> empty Series)."""
    if df is None or len(df) == 0:
        return pd.Series(dtype=float)
    return df.groupby(df["acq_date"].str[:7]).size().astype(float)


def count_ratio(modis_m, viirs_m, months):
    """VIIRS/MODIS hotspot-count ratio summed over the given 'YYYY-MM' months."""
    m = float(modis_m.reindex(months).fillna(0).sum())
    v = float(viirs_m.reindex(months).fillna(0).sum())
    if m == 0:
        raise ValueError("No MODIS hotspots in the overlap period - cannot compute ratio.")
    return v / m


def build_calendar(modis_m, viirs_m, ratio, start_month, end_month):
    """
    One row per month: harmonized count, estimated flag, historical median
    of the same calendar month, and count / median.
    """
    months = pd.period_range(start_month, end_month, freq="M").strftime("%Y-%m")
    m = modis_m.reindex(months).fillna(0.0).to_numpy()
    v = viirs_m.reindex(months).fillna(0.0).to_numpy()

    viirs_era = np.asarray(months >= VIIRS_FIRST_FULL_MONTH)
    use_viirs = viirs_era & (v > 0)
    count = np.where(use_viirs, v, m * ratio)
    estimated = (~viirs_era) | (viirs_era & (v == 0) & (m > 0))

    df = pd.DataFrame(
        {"month": list(months), "count": np.round(count, 1), "estimated": estimated}
    )
    df["median"] = df.groupby(df["month"].str[5:7])["count"].transform("median")
    df["vs_median"] = np.where(df["median"] > 0, df["count"] / df["median"], np.nan)
    df["vs_median"] = df["vs_median"].round(2)
    df["median"] = df["median"].round(1)
    return df
