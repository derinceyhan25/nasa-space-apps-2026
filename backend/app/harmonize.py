"""
Harmonize MODIS and VIIRS daily fire radiative power (FRP) into one
VIIRS-equivalent time series.

Empirical model fitted on days where both sensors detected fire:
    VIIRS_FRP = a * MODIS_FRP ** b        (fitted in log-log space)
"""
import numpy as np
import pandas as pd

VIIRS_START_DATE = "2012-01-20"  # first VIIRS S-NPP data


def daily_totals(df, date_col="acq_date"):
    """Total FRP per day for a hotspot DataFrame."""
    if df is None or len(df) == 0:
        return pd.Series(dtype=float, name="frp")
    return df.groupby(date_col)["frp"].sum()


def fit_power_law(modis_daily, viirs_daily):
    """Fit VIIRS = a * MODIS**b using days where both sensors saw fire."""
    both = pd.concat(
        [modis_daily.rename("M"), viirs_daily.rename("V")], axis=1
    ).dropna()
    both = both[(both.M > 0) & (both.V > 0)]
    if len(both) < 10:
        raise ValueError(f"Only {len(both)} overlapping days - too few to fit.")
    x, y = np.log(both.M), np.log(both.V)
    b, ln_a = np.polyfit(x, y, 1)
    r = float(np.corrcoef(x, y)[0, 1])
    return {"a": float(np.exp(ln_a)), "b": float(b), "r": r, "n_days": int(len(both))}


def modis_to_viirs_equiv(modis_frp, a, b):
    """Convert MODIS daily total FRP to VIIRS-equivalent FRP."""
    return a * np.power(modis_frp, b)


def build_harmonized_series(modis_daily, viirs_daily, fit, start, end):
    """
    One row per day between start and end (YYYY-MM-DD, inclusive).

    - Before VIIRS exists: MODIS converted with the fitted model (estimated=True).
    - After: real VIIRS value when VIIRS detected fire; otherwise converted
      MODIS if MODIS detected fire (estimated=True); otherwise 0.
    """
    days = pd.date_range(start, end).strftime("%Y-%m-%d")
    m = modis_daily.reindex(days).fillna(0.0).values
    v = viirs_daily.reindex(days).fillna(0.0).values

    converted = modis_to_viirs_equiv(m, fit["a"], fit["b"])
    viirs_era = np.asarray(days >= VIIRS_START_DATE)
    use_viirs = viirs_era & (v > 0)

    harmonized = np.where(use_viirs, v, converted)
    estimated = (~viirs_era) | (viirs_era & (v == 0) & (m > 0))

    return pd.DataFrame(
        {"date": days, "harmonized_frp": harmonized, "estimated": estimated}
    )
