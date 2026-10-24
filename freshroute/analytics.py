"""Dashboard metrics, chart series and rule-based insights.

Every number is computed from stored records. Insights are only produced when the
data supports them, and each states the evidence it is based on.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from . import waste
from .waste import safe_number

MIN_INSIGHT_OBS = 5


def _rows(df: pd.DataFrame) -> pd.DataFrame:
    """Frame with the short column names waste.summarize expects."""
    return df


def kpis(df: pd.DataFrame) -> dict:
    s = waste.summarize(df)
    s["days"] = int(df["date"].nunique()) if not df.empty else 0
    return s


def trend_by_day(df: pd.DataFrame) -> list:
    if df.empty:
        return []
    g = df.groupby("date").agg(prepared=("prepared", "sum"), consumed=("consumed", "sum"),
                               leftovers=("leftovers", "sum")).reset_index()
    g["waste_pct"] = [waste.waste_pct(l, p) for l, p in zip(g["leftovers"], g["prepared"])]
    return [{k: (safe_number(v) if k != "date" else v) for k, v in row.items()} for row in g.to_dict("records")]


def trend_by_week(df: pd.DataFrame) -> list:
    if df.empty:
        return []
    tmp = df.copy()
    tmp["week"] = tmp["date_dt"].dt.to_period("W-SUN").apply(lambda p: p.start_time.strftime("%Y-%m-%d"))
    g = tmp.groupby("week").agg(prepared=("prepared", "sum"), consumed=("consumed", "sum"),
                                leftovers=("leftovers", "sum"), days=("date", "nunique")).reset_index()
    g["waste_pct"] = [waste.waste_pct(l, p) for l, p in zip(g["leftovers"], g["prepared"])]
    return [{k: (safe_number(v) if k not in ("week",) else v) for k, v in row.items()} for row in g.to_dict("records")]


def by_item(df: pd.DataFrame) -> list:
    if df.empty:
        return []
    g = df.groupby(["item_id", "food_item"]).agg(
        records=("id", "count"), prepared=("prepared", "sum"), consumed=("consumed", "sum"),
        leftovers=("leftovers", "sum"), redistributed=("redistributed", "sum"),
    ).reset_index()
    g["waste_pct"] = [waste.waste_pct(l, p) for l, p in zip(g["leftovers"], g["prepared"])]
    g = g.sort_values("leftovers", ascending=False)
    return [{k: (safe_number(v) if k not in ("food_item",) else v) for k, v in row.items()} for row in g.to_dict("records")]


def by_meal(df: pd.DataFrame) -> list:
    if df.empty:
        return []
    g = df.groupby("meal").agg(
        records=("id", "count"), prepared=("prepared", "sum"), consumed=("consumed", "sum"),
        leftovers=("leftovers", "sum"),
    ).reset_index()
    g["waste_pct"] = [waste.waste_pct(l, p) for l, p in zip(g["leftovers"], g["prepared"])]
    return [{k: (safe_number(v) if k != "meal" else v) for k, v in row.items()} for row in g.to_dict("records")]


def by_weekday(df: pd.DataFrame) -> list:
    if df.empty:
        return []
    tmp = df.copy()
    tmp["dow"] = tmp["date_dt"].dt.dayofweek
    g = tmp.groupby("dow").agg(prepared=("prepared", "sum"), leftovers=("leftovers", "sum"),
                               consumed=("consumed", "sum")).reset_index()
    g["waste_pct"] = [waste.waste_pct(l, p) for l, p in zip(g["leftovers"], g["prepared"])]
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    return [{"weekday": names[int(r["dow"])], "waste_pct": safe_number(r["waste_pct"]),
             "consumed": safe_number(r["consumed"]), "prepared": safe_number(r["prepared"])}
            for r in g.to_dict("records")]


def prepared_vs_consumed_by_day(df: pd.DataFrame) -> list:
    return [{"date": r["date"], "prepared": r["prepared"], "consumed": r["consumed"]} for r in trend_by_day(df)]


def meal_summary(df: pd.DataFrame, latest_only: bool = True) -> list:
    """Per-meal summary of the most recent service day (or all days)."""
    if df.empty:
        return []
    frame = df[df["date"] == df["date"].max()] if latest_only else df
    out = by_meal(frame)
    for row in out:
        row["date"] = frame["date"].max()
    return out


# ------------------------------------------------------------------ insights

def insights(df: pd.DataFrame, settings: dict, item_costs: dict) -> list:
    """Rule-based observations, each backed by evidence from stored data.

    Rules:
      1. An item has waste% above the high-waste threshold over >= 5 records and
         in at least 60% of its services leftovers exceeded the acceptable level.
      2. Stock-outs: an item ran out (no leftovers) in >= 50% of its last 10 services (>= 5 records).
      3. A meal has the highest waste and its waste% is clearly above the overall level.
      4. Weekday with notably higher waste% (>= 5 points above overall, >= 8 records).
      5. Estimated cost of avoidable leftovers, only for items with a user-supplied cost.
    """
    out: list = []
    if df.empty:
        return out
    high = float(settings.get("high_waste_threshold_pct", 15.0))
    ok = float(settings.get("acceptable_waste_pct", 5.0))
    overall = waste.waste_pct(float(df["leftovers"].sum()), float(df["prepared"].sum()))

    for name, g in df.groupby("food_item"):
        if len(g) < MIN_INSIGHT_OBS:
            continue
        w = waste.waste_pct(float(g["leftovers"].sum()), float(g["prepared"].sum()))
        per = np.where(g["prepared"] > 0, g["leftovers"] / g["prepared"] * 100, 0)
        share_high = float((per > ok).mean())
        if w is not None and w >= high and share_high >= 0.6:
            out.append({
                "severity": "warning", "kind": "high_waste", "item": name,
                "text": f"{name} leftovers have been consistently high ({w:.1f}% of prepared quantity across "
                        f"{len(g)} records; above {ok:g}% in {share_high * 100:.0f}% of services). "
                        "Consider reviewing the next preparation quantity.",
                "evidence": f"{len(g)} records, waste {w:.1f}% vs threshold {high:g}%",
            })
        tail = g.sort_values("date_dt").tail(10)
        if len(tail) >= MIN_INSIGHT_OBS:
            share_out = float((tail["leftovers"] <= 0).mean())
            if share_out >= 0.5:
                out.append({
                    "severity": "info", "kind": "stockout", "item": name,
                    "text": f"{name} ran out in {share_out * 100:.0f}% of the last {len(tail)} services. "
                            "Actual demand may be higher than recorded consumption; avoid cutting preparation.",
                    "evidence": f"{int((tail['leftovers'] <= 0).sum())} of {len(tail)} recent services had no leftovers",
                })

    meals = [m for m in by_meal(df) if m["records"] >= 8 and m["waste_pct"] is not None]
    if len(meals) >= 2 and overall is not None:
        worst = max(meals, key=lambda m: m["waste_pct"])
        if worst["waste_pct"] >= overall + 3:
            out.append({
                "severity": "info", "kind": "meal_waste", "item": None,
                "text": f"{worst['meal']} has the highest waste ({worst['waste_pct']:.1f}% vs {overall:.1f}% overall).",
                "evidence": f"{worst['records']} {worst['meal']} records",
            })

    if overall is not None:
        counts = df.assign(dow=df["date_dt"].dt.dayofweek).groupby("dow").size()
        for row in by_weekday(df):
            idx = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].index(row["weekday"])
            if counts.get(idx, 0) >= 8 and row["waste_pct"] is not None and row["waste_pct"] >= overall + 5:
                out.append({
                    "severity": "info", "kind": "weekday_waste", "item": None,
                    "text": f"{row['weekday']}s show higher waste ({row['waste_pct']:.1f}% vs {overall:.1f}% overall). "
                            "Check whether preparation is set for weekday attendance.",
                    "evidence": f"{int(counts.get(idx, 0))} records on {row['weekday']}s",
                })

    saving = estimate_avoidable_cost(df, settings, item_costs)
    if saving and saving["estimate"] > 0:
        out.append({
            "severity": "estimate", "kind": "cost", "item": None,
            "text": f"Estimated cost of avoidable leftovers: about {saving['estimate']:,.0f} for items with a cost "
                    f"set ({saving['items']} item(s)). This is an estimate based on your entered costs and an "
                    f"assumption that {ok:g}% waste is unavoidable.",
            "evidence": saving["basis"],
        })
    return out


def estimate_avoidable_cost(df: pd.DataFrame, settings: dict, item_costs: dict) -> Optional[dict]:
    """Cost of leftovers above the acceptable waste level, only where a cost per unit is supplied."""
    if df.empty or not item_costs:
        return None
    ok = float(settings.get("acceptable_waste_pct", 5.0)) / 100.0
    total, used = 0.0, 0
    for name, g in df.groupby("food_item"):
        cost = item_costs.get(name)
        if cost is None or len(g) < MIN_INSIGHT_OBS:
            continue
        avoidable = max(float(g["leftovers"].sum()) - ok * float(g["prepared"].sum()), 0.0)
        total += avoidable * cost
        used += 1
    if used == 0:
        return None
    return {"estimate": round(total, 2), "items": used,
            "basis": f"leftovers above {ok * 100:g}% of prepared x entered cost per unit; items with >= {MIN_INSIGHT_OBS} records"}
