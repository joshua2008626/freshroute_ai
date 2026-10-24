"""Application services that combine repository, forecasting and analytics.

Used by both HTML pages and JSON API so both show identical numbers.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

import pandas as pd
from flask import current_app

from . import analytics, forecasting, recommendations, waste
from . import repository as repo
from .constants import SCOPES
from .validation import parse_date


# ------------------------------------------------------------------ filters

def clean_scope(value: Optional[str], settings: dict) -> str:
    value = value or settings.get("active_scope", "all")
    return value if value in SCOPES else "all"


def load_records(db, settings: dict, args) -> tuple:
    """Records for the current filters, restricted to ONE unit so quantities are never mixed.

    Returns (frame, unit, excluded_rows, filters_dict).
    """
    scope = clean_scope(None, settings)
    start = _date_arg(args.get("start"))
    end = _date_arg(args.get("end"))
    meal = args.get("meal") or None
    if meal and meal not in settings["meals"]:
        meal = None
    category = (args.get("category") or "").strip() or None
    raw = repo.fetch_records(db, scope=scope, start=start, end=end, meal=meal, category=category)
    unit_arg = (args.get("unit") or "").strip() or None
    frame, unit, excluded = waste.restrict_to_unit(raw, unit_arg, settings["default_unit"])
    filters = {"scope": scope, "start": start or "", "end": end or "", "meal": meal or "",
               "category": category or "", "unit": unit}
    return frame, unit, excluded, filters


def _date_arg(value) -> Optional[str]:
    d = parse_date(value) if value else None
    return d.isoformat() if d else None


# -------------------------------------------------------------------- engine

def engine_for(db, settings: dict, unit: str) -> tuple:
    """(engine, scope-wide frame) built from ALL records in the active scope (no date filters)."""
    scope = clean_scope(None, settings)
    frame = repo.fetch_records(db, scope=scope)
    key = f"{current_app.config['DATABASE']}|{scope}"
    return forecasting.get_engine(frame, unit, key, current_app.config["MODEL_DIR"]), frame


def forecast_and_recommend(db, settings: dict, item_name: str, meal: str, target: date,
                           expected_students: Optional[int] = None, log: bool = True) -> dict:
    item = repo.get_item_by_name(db, item_name)
    if item is None:
        raise ValueError(f"Unknown food item '{item_name}'.")
    engine, frame = engine_for(db, settings, item["unit"])
    fc = engine.forecast(item["id"], item["name"], meal, target, expected_students)
    hist = pd.DataFrame()
    if not engine.frame.empty:
        hist = engine.frame[(engine.frame["item_id"] == item["id"]) & (engine.frame["meal"] == meal)
                            & (engine.frame["date_dt"] < pd.Timestamp(target))]
    rec = recommendations.recommend(fc, hist, settings, item["min_prep"])
    rec["cost_per_unit"] = item["cost_per_unit"]
    rec["item_id"] = item["id"]
    if log and fc["estimate"] is not None:
        last = repo.forecast_log_for(db, target.isoformat(), meal, item["id"])
        if not last or abs(last["predicted"] - fc["estimate"]) > 1e-9 or last["method"] != fc["method"]:
            repo.log_forecast(db, target_date=target.isoformat(), meal=meal, item_id=item["id"],
                              predicted=fc["estimate"], recommended=rec["suggested"], method=fc["method"],
                              n_obs=fc["n_obs"], scope=clean_scope(None, settings))
    return {"forecast": fc, "recommendation": rec}


def items_for_meal(db, settings: dict, meal: str, target: date) -> list:
    """Items that have been served at `meal`, preferring those served on the target weekday."""
    frame = repo.fetch_records(db, scope=clean_scope(None, settings), meal=meal)
    if frame.empty:
        return []
    same = frame[frame["date_dt"].dt.dayofweek == target.weekday()]
    chosen = same if not same.empty else frame
    names = (chosen.groupby("food_item")["id"].count().sort_values(ascending=False).index.tolist())
    return names


def next_service(db, settings: dict, now: Optional[datetime] = None) -> tuple:
    """(date, meal) of the next meal to plan: the first meal without a record today, else tomorrow's first."""
    now = now or datetime.now()
    today = now.date()
    frame = repo.fetch_records(db, scope=clean_scope(None, settings), start=today.isoformat(), end=today.isoformat())
    done = set(frame["meal"].unique()) if not frame.empty else set()
    for meal in settings["meals"]:
        if meal not in done:
            return today, meal
    return today + timedelta(days=1), settings["meals"][0]


def next_meal_forecasts(db, settings: dict, target: date, meal: str, unit: str) -> dict:
    rows = []
    for name in items_for_meal(db, settings, meal, target):
        item = repo.get_item_by_name(db, name)
        if item["unit"] != unit:
            continue
        res = forecast_and_recommend(db, settings, name, meal, target, log=False)
        rows.append(res)
    total = sum(r["forecast"]["estimate"] for r in rows if r["forecast"]["estimate"] is not None)
    return {"date": target.isoformat(), "meal": meal, "rows": rows,
            "total": round(total, 1) if rows else None,
            "methods": sorted({r["forecast"]["method_label"] for r in rows})}


# ------------------------------------------------------------ live evaluation

def logged_forecast_accuracy(db, settings: dict) -> dict:
    """Compare logged forecasts with actuals that were recorded AFTER the forecast was made.

    Only the latest forecast created at or before the moment the actual was recorded counts,
    so a forecast logged after the fact cannot flatter the result. Demo records (seeded in bulk)
    therefore never count; only forecasts you make and then record actuals for do.
    """
    rows = db.execute(
        """
        SELECT fr.target_date, fr.meal, f.name AS item, fr.predicted, fr.method, r.quantity_consumed AS actual, r.unit
        FROM forecast_records fr
        JOIN food_items f ON f.id = fr.food_item_id
        JOIN meal_records r ON r.food_item_id = fr.food_item_id AND r.service_date = fr.target_date AND r.meal = fr.meal
        WHERE fr.created_at <= r.created_at
          AND fr.id = (SELECT MAX(x.id) FROM forecast_records x
                       WHERE x.food_item_id = fr.food_item_id AND x.target_date = fr.target_date
                         AND x.meal = fr.meal AND x.created_at <= r.created_at)
        ORDER BY fr.target_date
        """
    ).fetchall()
    pairs = [dict(r) for r in rows]
    result = {"pairs": pairs, "n": len(pairs), "mae": None, "min_required": 5}
    if len(pairs) >= 5:
        errors = [abs(p["predicted"] - p["actual"]) for p in pairs]
        result["mae"] = round(sum(errors) / len(errors), 2)
    return result


def waste_change(df: pd.DataFrame, days: int = 7) -> Optional[dict]:
    """Waste % over the latest `days` service days vs the `days` before."""
    if df.empty:
        return None
    dates = sorted(df["date"].unique())
    if len(dates) < days * 2:
        return None
    recent, previous = dates[-days:], dates[-2 * days:-days]
    a = df[df["date"].isin(recent)]
    b = df[df["date"].isin(previous)]
    wa = waste.waste_pct(float(a["leftovers"].sum()), float(a["prepared"].sum()))
    wb = waste.waste_pct(float(b["leftovers"].sum()), float(b["prepared"].sum()))
    if wa is None or wb is None:
        return None
    return {"recent": wa, "previous": wb, "delta": round(wa - wb, 2), "days": days}


def item_costs(db) -> dict:
    return {i["name"]: i["cost_per_unit"] for i in repo.list_items(db) if i["cost_per_unit"] is not None}


def run_backtest(db, settings: dict, args) -> dict:
    """Time Machine result for the active scope; `args` may carry unit, days and buffer."""
    from . import backtest
    frame, unit, excluded, _ = load_records(db, settings, {"unit": args.get("unit")})
    try:
        days = int(args.get("days") or backtest.DEFAULT_DAYS)
    except ValueError:
        days = backtest.DEFAULT_DAYS
    buf = None
    if args.get("buffer"):
        try:
            buf = min(max(float(args.get("buffer")), 0.0), 50.0)
        except ValueError:
            buf = None
    mins = {i["name"]: i["min_prep"] for i in repo.list_items(db) if i["min_prep"] is not None}
    out = backtest.run(frame, settings, unit, days, item_min_prep=mins, buffer_pct=buf)
    out["excluded"] = excluded
    return out
