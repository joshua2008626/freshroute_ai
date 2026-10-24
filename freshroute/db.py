"""SQLite schema and connection helpers.

All dates are stored as ISO-8601 text (YYYY-MM-DD). Foreign keys are enforced and
CHECK constraints protect the most important invariants, so a bug in the
application layer cannot silently store nonsense (negative quantities,
consumption above preparation, unknown sources...).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from flask import current_app, g

from .constants import DEFAULT_SETTINGS

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL            -- JSON encoded
);

CREATE TABLE IF NOT EXISTS food_items (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL UNIQUE COLLATE NOCASE,
    category      TEXT NOT NULL DEFAULT 'General',
    unit          TEXT NOT NULL DEFAULT 'servings',
    cost_per_unit REAL CHECK (cost_per_unit IS NULL OR cost_per_unit >= 0),
    min_prep      REAL CHECK (min_prep IS NULL OR min_prep >= 0),
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS import_history (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    filename       TEXT NOT NULL,
    file_sha256    TEXT NOT NULL,
    uploaded_at    TEXT NOT NULL DEFAULT (datetime('now')),
    total_rows     INTEGER NOT NULL DEFAULT 0,
    valid_rows     INTEGER NOT NULL DEFAULT 0,
    invalid_rows   INTEGER NOT NULL DEFAULT 0,
    duplicate_rows INTEGER NOT NULL DEFAULT 0,
    imported_rows  INTEGER NOT NULL DEFAULT 0,
    status         TEXT NOT NULL DEFAULT 'imported' CHECK (status IN ('imported', 'failed'))
);
CREATE INDEX IF NOT EXISTS idx_import_hash ON import_history(file_sha256);

CREATE TABLE IF NOT EXISTS meal_records (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    service_date       TEXT NOT NULL
        CHECK (service_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    meal               TEXT NOT NULL,
    food_item_id       INTEGER NOT NULL REFERENCES food_items(id) ON DELETE RESTRICT,
    unit               TEXT NOT NULL,
    quantity_prepared  REAL NOT NULL CHECK (quantity_prepared >= 0),
    quantity_consumed  REAL NOT NULL CHECK (quantity_consumed >= 0),
    leftovers          REAL NOT NULL CHECK (leftovers >= 0),   -- prepared - consumed
    supplied_leftovers REAL CHECK (supplied_leftovers IS NULL OR supplied_leftovers >= 0),
    leftover_mismatch  INTEGER NOT NULL DEFAULT 0 CHECK (leftover_mismatch IN (0, 1)),
    redistributed      REAL NOT NULL DEFAULT 0 CHECK (redistributed >= 0),
    student_count      INTEGER CHECK (student_count IS NULL OR student_count >= 0),
    notes              TEXT,
    source             TEXT NOT NULL CHECK (source IN ('demo', 'upload', 'manual')),
    import_id          INTEGER REFERENCES import_history(id) ON DELETE SET NULL,
    created_at         TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at         TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (service_date, meal, food_item_id),
    CHECK (quantity_consumed <= quantity_prepared),
    CHECK (redistributed <= leftovers)
);
CREATE INDEX IF NOT EXISTS idx_records_date ON meal_records(service_date);
CREATE INDEX IF NOT EXISTS idx_records_item ON meal_records(food_item_id);
CREATE INDEX IF NOT EXISTS idx_records_source ON meal_records(source);

CREATE TABLE IF NOT EXISTS preparation_plans (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_date     TEXT NOT NULL
        CHECK (plan_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    meal          TEXT NOT NULL,
    food_item_id  INTEGER NOT NULL REFERENCES food_items(id) ON DELETE CASCADE,
    forecast_qty  REAL,
    suggested_qty REAL,
    final_qty     REAL NOT NULL CHECK (final_qty >= 0),
    unit          TEXT NOT NULL,
    method        TEXT,
    note          TEXT,
    override      INTEGER NOT NULL DEFAULT 0 CHECK (override IN (0, 1)),
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (plan_date, meal, food_item_id)
);

CREATE TABLE IF NOT EXISTS forecast_records (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    target_date      TEXT NOT NULL
        CHECK (target_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    meal             TEXT NOT NULL,
    food_item_id     INTEGER NOT NULL REFERENCES food_items(id) ON DELETE CASCADE,
    predicted        REAL NOT NULL CHECK (predicted >= 0),
    recommended      REAL,
    method           TEXT NOT NULL,
    n_observations   INTEGER NOT NULL DEFAULT 0,
    scope            TEXT NOT NULL DEFAULT 'all',
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_forecast_key ON forecast_records(target_date, meal, food_item_id);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    """Open a connection with row access by name and foreign keys on."""
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(path: str | Path) -> None:
    """Create all tables and default settings. Safe to call repeatedly."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, json.dumps(value)),
            )
        conn.commit()
    finally:
        conn.close()


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_exc=None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()
