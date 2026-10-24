"""Synthetic demonstration dataset for a *fictional* college cafeteria.

Everything generated here is invented. Records are stored with source='demo' so they
are labelled as synthetic everywhere and can be removed or reset without touching a
user's own data.

The generator simulates attendance (lower at weekends and on a few "event" days),
item popularity per meal, and a cafeteria that prepares by habit - slightly too much
of some items (e.g. rice) and sometimes too little of others (e.g. paneer curry, which
sells out). Consumption is demand capped by what was prepared, like real life.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Optional

import numpy as np

from . import repository as repo

# (item, meal, category, unit, per-student share, weekdays served (0=Mon), over-preparation factor)
MENU = [
    ("Idli (2 pcs)", "Breakfast", "Tiffin", "servings", 0.30, (0, 1, 2, 3, 4, 5, 6), 1.04),
    ("Pongal", "Breakfast", "Tiffin", "servings", 0.16, (0, 2, 4), 1.05),
    ("Poha", "Breakfast", "Tiffin", "servings", 0.14, (1, 3, 5), 1.05),
    ("Sambar Rice", "Lunch", "Rice", "servings", 0.34, (0, 1, 2, 3, 4, 5, 6), 1.30),
    ("Chapati & Dal", "Lunch", "Bread", "servings", 0.22, (0, 1, 2, 3, 4, 5, 6), 1.05),
    ("Paneer Curry", "Lunch", "Curry", "servings", 0.14, (1, 3, 5), 0.95),
    ("Curd Rice", "Lunch", "Rice", "servings", 0.18, (0, 2, 4, 6), 1.10),
    ("Veg Samosa", "Snacks", "Snacks", "servings", 0.26, (0, 1, 2, 3, 4), 1.07),
    ("Tea", "Snacks", "Beverage", "servings", 0.40, (0, 1, 2, 3, 4, 5, 6), 1.03),
    ("Dosa", "Dinner", "Tiffin", "servings", 0.28, (0, 1, 2, 3, 4, 5, 6), 1.05),
    ("Fried Rice", "Dinner", "Rice", "servings", 0.20, (1, 3, 5, 6), 1.12),
    ("Sambar", "Dinner", "Curry", "litres", 0.045, (0, 1, 2, 3, 4, 5, 6), 1.12),
]

MEAL_ATTENDANCE_SHARE = {"Breakfast": 0.62, "Lunch": 0.88, "Snacks": 0.55, "Dinner": 0.80}
WEEKDAY_STUDENTS = {0: 1000, 1: 1020, 2: 1010, 3: 990, 4: 940, 5: 640, 6: 520}


def generate_rows(end_date: date, days: int = 90, seed: int = 42) -> list:
    """Return raw record dicts (validated later by the same code path as uploads)."""
    rng = np.random.default_rng(seed)
    rows = []
    # a few deterministic "event" days with unusual attendance (exams, holiday, fest)
    factors = [0.72, 0.65, 1.18, 0.80, 1.22, 0.70]
    pool = np.arange(5, days - 2)
    n_events = min(len(factors), len(pool))
    event_offsets = {int(o): f for o, f in zip(rng.choice(pool, n_events, replace=False), factors)} if n_events else {}
    for offset in range(days):
        day = end_date - timedelta(days=days - 1 - offset)
        base = WEEKDAY_STUDENTS[day.weekday()]
        factor = event_offsets.get(offset, 1.0)
        daily_students = base * factor * rng.normal(1.0, 0.04)
        for item, meal, category, unit, share, weekdays, over in MENU:
            if day.weekday() not in weekdays:
                continue
            students = int(daily_students * MEAL_ATTENDANCE_SHARE[meal] * rng.normal(1.0, 0.05))
            demand = students * share * rng.normal(1.0, 0.07)
            demand = max(demand, 2.0)
            # The kitchen prepares by habit, not by forecast: a noisy multiple of a typical day.
            # The kitchen partly adjusts for weekday attendance, but only by half.
            level = 0.5 + 0.5 * WEEKDAY_STUDENTS[day.weekday()] / WEEKDAY_STUDENTS[1]
            typical = WEEKDAY_STUDENTS[1] * MEAL_ATTENDANCE_SHARE[meal] * share * level
            habitual = typical * rng.normal(over, 0.05)
            prepared = habitual if over >= 1.0 else typical * rng.normal(over, 0.04)
            if unit == "litres":
                prepared, consumed = round(prepared * 2) / 2, round(min(demand, prepared) * 2) / 2
                consumed = min(consumed, prepared)
            else:
                prepared = float(round(prepared))
                consumed = float(round(min(demand, prepared)))
            rows.append({
                "date": day.isoformat(), "meal": meal, "food_item": item, "category": category,
                "unit": unit, "quantity_prepared": f"{prepared:g}", "quantity_consumed": f"{consumed:g}",
                "student_count": str(students),
            })
    return rows


def seed_demo(db: sqlite3.Connection, ctx_builder, validate, *, today: Optional[date] = None,
              days: int = 90, seed: int = 42) -> dict:
    """Insert the demo dataset. Existing records are never replaced (duplicates are skipped)."""
    today = today or date.today()
    ctx = ctx_builder(db, today)
    existing = repo.existing_keys(db)
    inserted = skipped = 0
    rejected = []
    for raw in generate_rows(today - timedelta(days=1), days=days, seed=seed):
        clean, errors, _ = validate(raw, ctx)
        if clean is None:
            rejected.append(errors)
            continue
        key = (clean["date"], clean["meal"], clean["food_item"].lower())
        if key in existing:
            skipped += 1
            continue
        repo.insert_record(db, clean, "demo")
        existing.add(key)
        ctx.item_units.setdefault(clean["food_item"].lower(), clean["unit"])
        inserted += 1
    repo.save_settings(db, {"demo_seeded": True, "cafeteria_name": _name_if_default(db)})
    db.commit()
    return {"inserted": inserted, "skipped": skipped, "rejected": len(rejected)}


def _name_if_default(db: sqlite3.Connection) -> str:
    current = repo.get_settings(db)["cafeteria_name"]
    return "Demo College Cafeteria (fictional)" if current == "My College Cafeteria" else current


def reset_demo(db: sqlite3.Connection, ctx_builder, validate, **kwargs) -> dict:
    """Remove only demo-sourced records and recreate them. User data is untouched."""
    removed = db.execute("DELETE FROM meal_records WHERE source = 'demo'").rowcount
    db.execute("DELETE FROM forecast_records")
    repo.prune_unused_items(db)
    db.commit()
    result = seed_demo(db, ctx_builder, validate, **kwargs)
    result["removed"] = removed
    return result
