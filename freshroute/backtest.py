"""Time Machine: replay recent history under the current preparation policy.

For every past service in a window we ask: *if the kitchen had followed
FreshRoute's suggestion that morning, using only data available before that
day, what would have happened?*

Honesty about what the records can and cannot tell us
-----------------------------------------------------
Records hold what was **consumed**, not what students **wanted**.

* Service did NOT run out (leftovers > 0): consumption equals demand, so the
  outcome of any other quantity is exact.
* Service DID run out (leftovers == 0): demand was *at least* what was
  prepared, but unknown beyond that. Any outcome is therefore a range:
  leftovers between 0 and max(P - prepared, 0), shortfall at least
  max(prepared - P, 0) and possibly much more.

Results are reported as bounds, never as a single "savings" number. Forecasts
use the walk-forward baseline (no refit per day), so no future data leaks in.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from . import forecasting, recommendations, waste

MIN_HISTORY = 3
DEFAULT_DAYS = 28
BUFFER_SWEEP = [0, 5, 10, 15, 20, 25, 30]


def prepare_rows(frame: pd.DataFrame, days: int = DEFAULT_DAYS, min_history: int = MIN_HISTORY,
                 item_min_prep: Optional[dict] = None) -> dict:
    """Policy inputs for each service in the last `days` days, using only earlier rows.

    Returns {"rows": DataFrame, "skipped": int, "window": (start, end) or None}.
    """
    item_min_prep = item_min_prep or {}
    if frame is None or frame.empty:
        return {"rows": pd.DataFrame(), "skipped": 0, "window": None}
    df = frame.sort_values("date_dt")
    end = df["date_dt"].max()
    start = end - pd.Timedelta(days=days - 1)
    out, skipped = [], 0
    for (item_id, meal), g in df.groupby(["item_id", "meal"], sort=False):
        g = g.sort_values("date_dt").reset_index(drop=True)
        for i in range(len(g)):
            row = g.iloc[i]
            if row["date_dt"] < start:
                continue
            hist = g.iloc[:i]
            hist = hist[hist["date_dt"] < row["date_dt"]]
            if len(hist) < min_history:
                skipped += 1
                continue
            est, _, _ = forecasting.baseline_predict(hist, row["date_dt"].date())
            if est is None:
                skipped += 1
                continue
            out.append({
                "date": row["date"], "item": row["food_item"], "meal": meal,
                "unit": row["unit"] if "unit" in g.columns else "",
                "est": float(est),
                "usual": recommendations.usual_preparation(hist),
                "stockouts": recommendations.recent_stockouts(hist),
                "floor_item": float(item_min_prep.get(row["food_item"]) or 0.0),
                "prepared": float(row["prepared"]), "consumed": float(row["consumed"]),
                "ran_out": bool(row["leftovers"] <= 0),
            })
    return {"rows": pd.DataFrame(out), "skipped": skipped,
            "window": (start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))}


def evaluate_policy(rows: pd.DataFrame, buffer_pct: float, max_cut_pct: float,
                    global_min: float = 0.0, unit: str = "servings") -> dict:
    """Counterfactual outcome (as bounds) for the policy over prepared `rows`."""
    if rows is None or rows.empty:
        return {"n": 0}
    sugg = []
    for r in rows.itertuples(index=False):
        s, _, _ = recommendations.suggest_quantity(
            r.est, r.usual, r.stockouts, unit, buffer_pct, max_cut_pct, max(global_min, r.floor_item))
        sugg.append(s)
    P = np.array(sugg, float)
    A = rows["prepared"].to_numpy(float)
    C = rows["consumed"].to_numpy(float)
    so = rows["ran_out"].to_numpy(bool)

    # leftovers under the policy: exact when demand is known, a range otherwise
    left_lo = np.where(so, 0.0, np.maximum(P - C, 0.0))
    left_hi = np.where(so, np.maximum(P - A, 0.0), np.maximum(P - C, 0.0))
    # services that were certainly short / possibly short under the policy
    sure_short = np.where(so, P < A, C > P)
    maybe_short = so & (P >= A)
    short_min = np.where(so, np.maximum(A - P, 0.0), np.maximum(C - P, 0.0))

    act_left = float((A - C).sum())
    prep_pol, prep_act = float(P.sum()), float(A.sum())
    n = len(rows)
    out = {
        "n": int(n), "n_ran_out": int(so.sum()),
        "actual": {"prepared": prep_act, "leftovers": act_left,
                   "waste_pct": waste.waste_pct(act_left, prep_act),
                   "ran_out": int(so.sum()), "ran_out_pct": round(so.mean() * 100, 1)},
        "policy": {"prepared": prep_pol,
                   "leftovers_low": float(left_lo.sum()), "leftovers_high": float(left_hi.sum()),
                   "waste_pct_low": waste.waste_pct(float(left_lo.sum()), prep_pol),
                   "waste_pct_high": waste.waste_pct(float(left_hi.sum()), prep_pol),
                   "short_sure": int(sure_short.sum()), "short_maybe": int(maybe_short.sum()),
                   "short_sure_pct": round(sure_short.mean() * 100, 1),
                   "short_units_min": float(short_min.sum())},
        "buffer_pct": buffer_pct,
    }
    # Only claim a reduction when it holds even under the least favourable reading.
    out["leftovers_reduced_at_least"] = max(act_left - out["policy"]["leftovers_high"], 0.0)
    out["leftovers_change_best_case"] = act_left - out["policy"]["leftovers_low"]
    out["_P"] = P
    out["_left_lo"], out["_left_hi"] = left_lo, left_hi
    return out


def daily_series(rows: pd.DataFrame, result: dict) -> list:
    if rows is None or rows.empty or result.get("n", 0) == 0:
        return []
    tmp = pd.DataFrame({"date": rows["date"].to_numpy(),
                        "actual": (rows["prepared"] - rows["consumed"]).to_numpy(),
                        "policy_high": result["_left_hi"], "policy_low": result["_left_lo"]})
    g = tmp.groupby("date").sum().reset_index()
    return [{"date": r.date, "actual": round(r.actual, 1), "policy_high": round(r.policy_high, 1),
             "policy_low": round(r.policy_low, 1)} for r in g.itertuples()]


def frontier(rows: pd.DataFrame, max_cut_pct: float, global_min: float, unit: str,
             buffers: Optional[list] = None) -> list:
    """Sweep the safety buffer: waste (upper bound) against services certainly short."""
    out = []
    for b in buffers or BUFFER_SWEEP:
        r = evaluate_policy(rows, b, max_cut_pct, global_min, unit)
        if r["n"] == 0:
            return []
        out.append({"buffer_pct": b, "waste_pct_high": r["policy"]["waste_pct_high"],
                    "waste_pct_low": r["policy"]["waste_pct_low"],
                    "short_sure_pct": r["policy"]["short_sure_pct"],
                    "actual_waste_pct": r["actual"]["waste_pct"],
                    "actual_ran_out_pct": r["actual"]["ran_out_pct"]})
    return out


def run(frame: pd.DataFrame, settings: dict, unit: str, days: int = DEFAULT_DAYS,
        item_min_prep: Optional[dict] = None, buffer_pct: Optional[float] = None) -> dict:
    """Full Time Machine result for the page/API (JSON-safe)."""
    days = max(7, min(int(days), 90))
    prepared = prepare_rows(frame, days, item_min_prep=item_min_prep)
    rows = prepared["rows"]
    buf = float(settings.get("safety_buffer_pct", 5.0)) if buffer_pct is None else float(buffer_pct)
    cut = float(settings.get("max_reduction_pct", 25.0))
    gmin = float(settings.get("min_prep_default", 0.0) or 0.0)
    res = evaluate_policy(rows, buf, cut, gmin, unit)
    base = {"days": days, "unit": unit, "window": prepared["window"], "skipped": prepared["skipped"],
            "buffer_pct": buf, "max_reduction_pct": cut}
    if res["n"] == 0:
        return {**base, "status": "insufficient", "n": 0, "frontier": [], "daily": []}
    daily = daily_series(rows, res)
    fr = frontier(rows, cut, gmin, unit)
    status = "ok" if res["n"] >= 30 else "thin"
    for k in ("_P", "_left_lo", "_left_hi"):
        res.pop(k)
    return {**base, "status": status, **res, "frontier": fr, "daily": daily}
