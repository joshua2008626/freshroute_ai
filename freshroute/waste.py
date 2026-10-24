"""Waste calculations.

Documented formulas (also shown in the UI and README):

* leftovers            = quantity_prepared - quantity_consumed
* waste %              = leftovers / quantity_prepared x 100
* unredistributed %    = (leftovers - redistributed) / quantity_prepared x 100

Aggregate percentages are ratio-of-sums (total leftovers / total prepared), *not*
the mean of per-row percentages, so large and small batches are weighted fairly.
Quantities are only ever summed within a single unit - kilograms and servings are
never added together.
"""
from __future__ import annotations

import math
from typing import Optional

import pandas as pd

TOLERANCE = 0.01


def derive_leftovers(prepared: float, consumed: float) -> float:
    """prepared - consumed. Raises ValueError when consumed exceeds prepared."""
    if prepared < 0 or consumed < 0:
        raise ValueError("Quantities cannot be negative.")
    if consumed - prepared > TOLERANCE:
        raise ValueError("Consumed quantity cannot exceed prepared quantity.")
    return round(max(prepared - consumed, 0.0), 3)


def waste_pct(leftover: float, prepared: float) -> Optional[float]:
    """Waste percentage, or None when nothing was prepared (division by zero)."""
    if prepared is None or leftover is None:
        return None
    if prepared <= 0:
        return None
    return round(leftover / prepared * 100.0, 2)


def unredistributed_pct(leftover: float, redistributed: float, prepared: float) -> Optional[float]:
    if prepared is None or prepared <= 0:
        return None
    return round(max(leftover - (redistributed or 0.0), 0.0) / prepared * 100.0, 2)


def leftovers_conflict(supplied: Optional[float], derived: float) -> bool:
    """True when a supplied leftovers value disagrees with prepared - consumed."""
    if supplied is None:
        return False
    return abs(supplied - derived) > TOLERANCE


def primary_unit(df: pd.DataFrame, default: str = "servings") -> str:
    """Most frequent unit in the frame (ties resolved by the default unit)."""
    if df is None or df.empty:
        return default
    counts = df["unit"].value_counts()
    top = counts[counts == counts.max()].index.tolist()
    return default if default in top else sorted(top)[0]


def restrict_to_unit(df: pd.DataFrame, unit: Optional[str], default: str = "servings"):
    """Return (frame_in_one_unit, unit_used, number_of_rows_excluded)."""
    if df is None or df.empty:
        return df, unit or default, 0
    chosen = unit or primary_unit(df, default)
    kept = df[df["unit"] == chosen]
    return kept, chosen, int(len(df) - len(kept))


def summarize(df: pd.DataFrame) -> dict:
    """Totals and waste percentages for a frame that is already in one unit."""
    if df is None or df.empty:
        return {
            "records": 0, "prepared": 0.0, "consumed": 0.0, "leftovers": 0.0,
            "redistributed": 0.0, "waste_pct": None, "unredistributed_pct": None,
            "mismatch_rows": 0,
        }
    prepared = float(df["prepared"].sum())
    consumed = float(df["consumed"].sum())
    leftovers = float(df["leftovers"].sum())
    redistributed = float(df["redistributed"].sum())
    return {
        "records": int(len(df)),
        "prepared": round(prepared, 2),
        "consumed": round(consumed, 2),
        "leftovers": round(leftovers, 2),
        "redistributed": round(redistributed, 2),
        "waste_pct": waste_pct(leftovers, prepared),
        "unredistributed_pct": unredistributed_pct(leftovers, redistributed, prepared),
        "mismatch_rows": int(df["mismatch"].sum()) if "mismatch" in df else 0,
    }


def safe_number(value) -> Optional[float]:
    """JSON-safe float (NaN/inf become None)."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f
