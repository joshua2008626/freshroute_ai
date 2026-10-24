"""Turn forecasts into preparation suggestions.

The suggestion is an *estimate to support a human decision*. It is shaped by
operator-configured constraints so it can never silently drive production to an
unsafe level:

    suggested = ceil(forecast x (1 + safety buffer))
    suggested = max(suggested, item minimum prep, global minimum prep)
    suggested = max(suggested, usual prep x (1 - max reduction))     # cap on cuts
    suggested = usual prep (no cut)       if recent stock-outs were observed
"""
from __future__ import annotations

import math
from typing import Optional

import pandas as pd


def usual_preparation(hist: pd.DataFrame, window: int = 10) -> Optional[float]:
    """Median quantity prepared over the most recent `window` observations."""
    if hist is None or hist.empty:
        return None
    return float(hist.sort_values("date_dt")["prepared"].tail(window).median())


def recent_stockouts(hist: pd.DataFrame, window: int = 5) -> int:
    """How many of the last `window` services ran out (nothing left over)."""
    if hist is None or hist.empty:
        return 0
    tail = hist.sort_values("date_dt").tail(window)
    return int((tail["leftovers"] <= 0).sum())


def _round_up(value: float, unit: str) -> float:
    if unit in ("kg", "litres"):
        return math.ceil(value * 2) / 2  # nearest 0.5 above
    return float(math.ceil(value))


def suggest_quantity(est: float, usual: Optional[float], stockouts: int, unit: str,
                     buffer_pct: float, max_cut_pct: float, floor: float = 0.0) -> tuple:
    """The preparation policy as a pure function.

    Returns (suggested, raw_with_buffer, notes) where notes is a list of
    (kind, text) with kind "constraint" or "warning". Shared by live
    recommendations and the Time Machine backtest so both use identical rules.
    """
    notes: list = []
    raw = est * (1 + buffer_pct / 100.0)
    suggested = _round_up(raw, unit)
    if floor and suggested < floor:
        suggested = floor
        notes.append(("constraint", f"Raised to the configured minimum preparation of {floor:g} {unit}."))
    if usual is not None:
        cut_floor = _round_up(usual * (1 - max_cut_pct / 100.0), unit)
        if suggested < cut_floor:
            suggested = cut_floor
            notes.append(("constraint",
                          f"Limited to at most a {max_cut_pct:g}% reduction from your usual {usual:.0f} {unit} "
                          "(change this cap in Settings)."))
        if stockouts >= 2 and suggested < usual:
            suggested = _round_up(usual, unit)
            notes.append(("constraint",
                          f"Kept at your usual quantity: {stockouts} of the last 5 services ran out, so recorded "
                          "consumption may understate real demand."))
            notes.append(("warning",
                          "Recent stock-outs: when food runs out, consumption equals what was prepared, not what students wanted."))
    return suggested, raw, notes


def recommend(forecast: dict, hist: pd.DataFrame, settings: dict, item_min_prep: Optional[float]) -> dict:
    """Build a recommendation dict from a forecast result and the item's history."""
    unit = forecast["unit"]
    est = forecast.get("estimate")
    usual = usual_preparation(hist)
    result = {
        "item": forecast["item"], "meal": forecast["meal"], "unit": unit,
        "target_date": forecast["target_date"], "forecast": est,
        "suggested": None, "usual": round(usual, 1) if usual is not None else None,
        "difference": None, "difference_pct": None, "method": forecast["method"],
        "method_label": forecast["method_label"], "reasons": [], "constraints": [],
        "warnings": list(forecast.get("warnings", [])), "n_obs": forecast["n_obs"],
        "stockouts": 0,
    }
    if est is None:
        result["reasons"].append("No forecast was possible, so no quantity is suggested. Keep your usual preparation.")
        return result

    buffer_pct = float(settings.get("safety_buffer_pct", 5.0))
    max_cut = float(settings.get("max_reduction_pct", 25.0))
    global_min = float(settings.get("min_prep_default", 0.0) or 0.0)
    stockouts = recent_stockouts(hist)
    result["stockouts"] = stockouts

    suggested, raw, notes = suggest_quantity(
        est, usual, stockouts, unit, buffer_pct, max_cut, max(global_min, item_min_prep or 0.0))
    result["reasons"].append(
        f"Forecast demand {est:.1f} {unit} plus a {buffer_pct:g}% safety buffer = {raw:.1f} {unit}."
    )
    for kind, text in notes:
        result["constraints" if kind == "constraint" else "warnings"].append(text)

    result["suggested"] = suggested
    if usual is not None:
        diff = suggested - usual
        result["difference"] = round(diff, 1)
        result["difference_pct"] = round(diff / usual * 100, 1) if usual > 0 else None
        if diff < -0.5:
            result["reasons"].append(
                f"That is {abs(diff):.0f} {unit} less than your usual {usual:.0f}: recent consumption has been below what is usually prepared."
            )
        elif diff > 0.5:
            result["reasons"].append(
                f"That is {diff:.0f} {unit} more than your usual {usual:.0f}: the forecast demand exceeds what is usually prepared."
            )
        else:
            result["reasons"].append("That is in line with your usual preparation.")
    result["reasons"].extend(forecast.get("explanation", []))
    result["reasons"].append(
        "This is an estimate, not a guarantee. The final quantity is your decision; adjust it for events, weather or menu changes."
    )
    return result
