"""Database access layer. Every query is parameterised."""
from __future__ import annotations

import json
import sqlite3
from datetime import date
from typing import Optional

import pandas as pd

from .constants import DEFAULT_SETTINGS, SCOPE_SOURCES
from .validation import ValidationContext

RECORD_COLUMNS = [
    "id", "date", "meal", "food_item", "item_id", "category", "unit", "prepared", "consumed",
    "leftovers", "supplied_leftovers", "mismatch", "redistributed", "student_count", "notes",
    "source", "import_id", "created_at",
]

_RECORD_SELECT = """
    SELECT r.id, r.service_date AS date, r.meal, f.name AS food_item, f.id AS item_id,
           f.category, r.unit, r.quantity_prepared AS prepared, r.quantity_consumed AS consumed,
           r.leftovers, r.supplied_leftovers, r.leftover_mismatch AS mismatch, r.redistributed,
           r.student_count, r.notes, r.source, r.import_id, r.created_at
    FROM meal_records r JOIN food_items f ON f.id = r.food_item_id
"""


# ---------------------------------------------------------------- settings

def get_settings(db: sqlite3.Connection) -> dict:
    settings = dict(DEFAULT_SETTINGS)
    for row in db.execute("SELECT key, value FROM settings"):
        try:
            settings[row["key"]] = json.loads(row["value"])
        except (TypeError, ValueError):
            pass
    return settings


def save_settings(db: sqlite3.Connection, values: dict) -> None:
    for key, value in values.items():
        db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )
    db.commit()


# ------------------------------------------------------------- food items

def list_items(db: sqlite3.Connection) -> list:
    rows = db.execute(
        "SELECT f.id, f.name, f.category, f.unit, f.cost_per_unit, f.min_prep, "
        "(SELECT COUNT(*) FROM meal_records r WHERE r.food_item_id = f.id) AS record_count "
        "FROM food_items f ORDER BY f.name COLLATE NOCASE"
    ).fetchall()
    return [dict(r) for r in rows]


def get_item_by_name(db: sqlite3.Connection, name: str) -> Optional[sqlite3.Row]:
    return db.execute("SELECT * FROM food_items WHERE name = ? COLLATE NOCASE", (name,)).fetchone()


def get_item(db: sqlite3.Connection, item_id: int) -> Optional[sqlite3.Row]:
    return db.execute("SELECT * FROM food_items WHERE id = ?", (item_id,)).fetchone()


def get_or_create_item(db: sqlite3.Connection, name: str, unit: str, category: str = "General") -> int:
    row = get_item_by_name(db, name)
    if row:
        return int(row["id"])
    cur = db.execute(
        "INSERT INTO food_items (name, category, unit) VALUES (?, ?, ?)", (name, category, unit)
    )
    return int(cur.lastrowid)


def update_item(db, item_id: int, *, cost_per_unit, min_prep, category=None) -> None:
    if category:
        db.execute("UPDATE food_items SET category = ? WHERE id = ?", (category, item_id))
    db.execute(
        "UPDATE food_items SET cost_per_unit = ?, min_prep = ? WHERE id = ?",
        (cost_per_unit, min_prep, item_id),
    )
    db.commit()


def prune_unused_items(db: sqlite3.Connection) -> int:
    cur = db.execute(
        "DELETE FROM food_items WHERE id NOT IN (SELECT DISTINCT food_item_id FROM meal_records) "
        "AND id NOT IN (SELECT DISTINCT food_item_id FROM preparation_plans)"
    )
    return cur.rowcount


# ----------------------------------------------------------------- records

def build_context(db: sqlite3.Connection, today: Optional[date] = None) -> ValidationContext:
    settings = get_settings(db)
    items = list_items(db)
    return ValidationContext(
        meals=list(settings["meals"]),
        units=list(settings["units"]),
        default_unit=settings["default_unit"],
        item_units={i["name"].lower(): i["unit"] for i in items},
        item_names={i["name"].lower(): i["name"] for i in items},
        item_categories={i["name"].lower(): i["category"] for i in items},
        today=today or date.today(),
    )


def existing_keys(db: sqlite3.Connection) -> set:
    rows = db.execute(
        "SELECT r.service_date, r.meal, lower(f.name) AS name FROM meal_records r "
        "JOIN food_items f ON f.id = r.food_item_id"
    ).fetchall()
    return {(r["service_date"], r["meal"], r["name"]) for r in rows}


def insert_record(db: sqlite3.Connection, clean: dict, source: str, import_id: Optional[int] = None) -> int:
    """Insert a validated record. Raises sqlite3.IntegrityError on duplicates (never replaces)."""
    item_id = get_or_create_item(db, clean["food_item"], clean["unit"], clean.get("category") or "General")
    cur = db.execute(
        "INSERT INTO meal_records (service_date, meal, food_item_id, unit, quantity_prepared, "
        "quantity_consumed, leftovers, supplied_leftovers, leftover_mismatch, redistributed, "
        "student_count, notes, source, import_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            clean["date"], clean["meal"], item_id, clean["unit"], clean["prepared"], clean["consumed"],
            clean["leftovers"], clean.get("supplied_leftovers"), int(clean.get("mismatch", 0)),
            clean.get("redistributed") or 0.0, clean.get("student_count"), clean.get("notes"),
            source, import_id,
        ),
    )
    return int(cur.lastrowid)


def update_record(db: sqlite3.Connection, record_id: int, clean: dict) -> None:
    item_id = get_or_create_item(db, clean["food_item"], clean["unit"], clean.get("category") or "General")
    db.execute(
        "UPDATE meal_records SET service_date=?, meal=?, food_item_id=?, unit=?, quantity_prepared=?, "
        "quantity_consumed=?, leftovers=?, supplied_leftovers=?, leftover_mismatch=?, redistributed=?, "
        "student_count=?, notes=?, updated_at=datetime('now') WHERE id=?",
        (
            clean["date"], clean["meal"], item_id, clean["unit"], clean["prepared"], clean["consumed"],
            clean["leftovers"], clean.get("supplied_leftovers"), int(clean.get("mismatch", 0)),
            clean.get("redistributed") or 0.0, clean.get("student_count"), clean.get("notes"), record_id,
        ),
    )


def get_record(db: sqlite3.Connection, record_id: int) -> Optional[dict]:
    row = db.execute(_RECORD_SELECT + " WHERE r.id = ?", (record_id,)).fetchone()
    return dict(row) if row else None


def delete_record(db: sqlite3.Connection, record_id: int) -> bool:
    cur = db.execute("DELETE FROM meal_records WHERE id = ?", (record_id,))
    db.commit()
    return cur.rowcount > 0


def fetch_records(
    db: sqlite3.Connection,
    *,
    scope: str = "all",
    start: Optional[str] = None,
    end: Optional[str] = None,
    meal: Optional[str] = None,
    item: Optional[str] = None,
    category: Optional[str] = None,
    search: Optional[str] = None,
    unit: Optional[str] = None,
) -> pd.DataFrame:
    """Records as a DataFrame (oldest first), filtered with bound parameters only."""
    clauses, params = [], []
    sources = SCOPE_SOURCES.get(scope)
    if sources:
        clauses.append("r.source IN (%s)" % ",".join("?" * len(sources)))
        params.extend(sources)
    if start:
        clauses.append("r.service_date >= ?")
        params.append(start)
    if end:
        clauses.append("r.service_date <= ?")
        params.append(end)
    if meal:
        clauses.append("r.meal = ?")
        params.append(meal)
    if item:
        clauses.append("f.name = ? COLLATE NOCASE")
        params.append(item)
    if category:
        clauses.append("f.category = ?")
        params.append(category)
    if unit:
        clauses.append("r.unit = ?")
        params.append(unit)
    if search:
        like = "%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        clauses.append("(f.name LIKE ? ESCAPE '\\' OR IFNULL(r.notes,'') LIKE ? ESCAPE '\\')")
        params.extend([like, like])
    sql = _RECORD_SELECT
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY r.service_date ASC, r.meal ASC, f.name ASC"
    rows = db.execute(sql, params).fetchall()
    df = pd.DataFrame([dict(r) for r in rows], columns=RECORD_COLUMNS)
    if not df.empty:
        df["date_dt"] = pd.to_datetime(df["date"])
    else:
        df["date_dt"] = pd.to_datetime(pd.Series([], dtype="object"))
    return df


def source_counts(db: sqlite3.Connection) -> dict:
    counts = {"demo": 0, "upload": 0, "manual": 0}
    for row in db.execute("SELECT source, COUNT(*) AS n FROM meal_records GROUP BY source"):
        counts[row["source"]] = row["n"]
    return counts


def date_bounds(db: sqlite3.Connection, scope: str = "all") -> tuple:
    sources = SCOPE_SOURCES.get(scope)
    sql = "SELECT MIN(service_date) AS lo, MAX(service_date) AS hi FROM meal_records"
    params: list = []
    if sources:
        sql += " WHERE source IN (%s)" % ",".join("?" * len(sources))
        params = list(sources)
    row = db.execute(sql, params).fetchone()
    return row["lo"], row["hi"]


# ------------------------------------------------------------------ imports

def find_import_by_hash(db: sqlite3.Connection, sha: str) -> Optional[dict]:
    row = db.execute(
        "SELECT * FROM import_history WHERE file_sha256 = ? AND status = 'imported' "
        "ORDER BY id DESC LIMIT 1", (sha,)
    ).fetchone()
    return dict(row) if row else None


def log_import(db, *, filename, sha, total, valid, invalid, duplicate, imported, status="imported") -> int:
    cur = db.execute(
        "INSERT INTO import_history (filename, file_sha256, total_rows, valid_rows, invalid_rows, "
        "duplicate_rows, imported_rows, status) VALUES (?,?,?,?,?,?,?,?)",
        (filename, sha, total, valid, invalid, duplicate, imported, status),
    )
    return int(cur.lastrowid)


def list_imports(db: sqlite3.Connection, limit: int = 20) -> list:
    rows = db.execute(
        "SELECT * FROM import_history ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


# -------------------------------------------------------- plans / forecasts

def save_plan(db, *, plan_date, meal, item_id, forecast_qty, suggested_qty, final_qty, unit,
              method, note, override) -> None:
    db.execute(
        "INSERT INTO preparation_plans (plan_date, meal, food_item_id, forecast_qty, suggested_qty, "
        "final_qty, unit, method, note, override) VALUES (?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(plan_date, meal, food_item_id) DO UPDATE SET forecast_qty=excluded.forecast_qty, "
        "suggested_qty=excluded.suggested_qty, final_qty=excluded.final_qty, unit=excluded.unit, "
        "method=excluded.method, note=excluded.note, override=excluded.override, "
        "updated_at=datetime('now')",
        (plan_date, meal, item_id, forecast_qty, suggested_qty, final_qty, unit, method, note,
         int(override)),
    )
    db.commit()


def list_plans(db, plan_date: Optional[str] = None, limit: int = 100) -> list:
    sql = ("SELECT p.*, f.name AS food_item FROM preparation_plans p "
           "JOIN food_items f ON f.id = p.food_item_id")
    params: list = []
    if plan_date:
        sql += " WHERE p.plan_date = ?"
        params.append(plan_date)
    sql += " ORDER BY p.plan_date DESC, p.meal, f.name LIMIT ?"
    params.append(limit)
    return [dict(r) for r in db.execute(sql, params).fetchall()]


def log_forecast(db, *, target_date, meal, item_id, predicted, recommended, method, n_obs, scope) -> None:
    db.execute(
        "INSERT INTO forecast_records (target_date, meal, food_item_id, predicted, recommended, "
        "method, n_observations, scope) VALUES (?,?,?,?,?,?,?,?)",
        (target_date, meal, item_id, predicted, recommended, method, n_obs, scope),
    )
    db.commit()


def forecast_log_for(db, target_date: str, meal: str, item_id: int) -> Optional[dict]:
    row = db.execute(
        "SELECT * FROM forecast_records WHERE target_date=? AND meal=? AND food_item_id=? "
        "ORDER BY id DESC LIMIT 1", (target_date, meal, item_id)
    ).fetchone()
    return dict(row) if row else None


def recent_activity(db: sqlite3.Connection, limit: int = 10) -> list:
    rows = db.execute(
        "SELECT * FROM ("
        " SELECT 'record' AS kind, r.created_at AS at, "
        "  f.name || ' - ' || r.meal || ' ' || r.service_date AS text, r.source AS source "
        "  FROM meal_records r JOIN food_items f ON f.id = r.food_item_id "
        " UNION ALL"
        " SELECT 'plan', p.updated_at, 'Plan saved: ' || f.name || ' - ' || p.meal || ' ' || p.plan_date, 'manual' "
        "  FROM preparation_plans p JOIN food_items f ON f.id = p.food_item_id "
        " UNION ALL"
        " SELECT 'import', h.uploaded_at, 'Imported ' || h.imported_rows || ' rows from ' || h.filename, 'upload' "
        "  FROM import_history h"
        ") ORDER BY at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------- deletion

def delete_by_source(db: sqlite3.Connection, source: str) -> int:
    cur = db.execute("DELETE FROM meal_records WHERE source = ?", (source,))
    db.execute("DELETE FROM forecast_records")
    if source == "upload":
        db.execute("DELETE FROM import_history")
    prune_unused_items(db)
    db.commit()
    return cur.rowcount


def delete_everything(db: sqlite3.Connection) -> None:
    for table in ("forecast_records", "preparation_plans", "meal_records", "import_history", "food_items"):
        db.execute(f"DELETE FROM {table}")  # fixed table names, no user input
    db.commit()
