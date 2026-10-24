"""Shared test helpers."""
import shutil
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from freshroute import create_app
from freshroute import repository as repo
from freshroute import validation
from freshroute.db import get_db

CSRF_OFF = {"TESTING_DISABLE_CSRF": True}


class AppTestCase(unittest.TestCase):
    """Fresh app + empty database (no demo data) per test."""

    seed_demo = False
    csrf = False

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        cfg = {"INSTANCE_PATH": self.tmp, "AUTO_SEED_DEMO": self.seed_demo, "TESTING": True}
        if not self.csrf:
            cfg.update(CSRF_OFF)
        self.app = create_app(cfg)
        self.client = self.app.test_client()
        self.ctx = self.app.app_context()
        self.ctx.push()
        self.db = get_db()

    def tearDown(self):
        self.ctx.pop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # convenience -----------------------------------------------------------
    def add(self, d, meal, item, prepared, consumed, unit="servings", source="manual", **extra):
        raw = {"date": d, "meal": meal, "food_item": item, "unit": unit,
               "quantity_prepared": str(prepared), "quantity_consumed": str(consumed)}
        raw.update({k: str(v) for k, v in extra.items()})
        clean, errors, _ = validation.validate_record(raw, repo.build_context(self.db))
        self.assertIsNotNone(clean, errors)
        repo.insert_record(self.db, clean, source)
        self.db.commit()
        return clean

    def add_series(self, item="Rice", meal="Lunch", days=40, base=100, weekend_factor=0.6,
                   prepared_factor=1.2, source="manual", end=None, seed=1, noise=3.0):
        """Daily history with a weekday pattern. Returns the list of consumed values."""
        rng = np.random.default_rng(seed)
        end = end or (date.today() - timedelta(days=1))
        out = []
        for i in range(days):
            d = end - timedelta(days=days - 1 - i)
            level = base * (weekend_factor if d.weekday() >= 5 else 1.0)
            consumed = max(round(level + rng.normal(0, noise)), 1)
            prepared = round(consumed * prepared_factor) + 1
            self.add(d.isoformat(), meal, item, prepared, consumed, source=source)
            out.append(consumed)
        return out


def make_frame(rows):
    """Build the DataFrame shape repository.fetch_records returns, from simple tuples.

    rows: (date 'YYYY-MM-DD', meal, item, prepared, consumed)
    """
    items = {}
    recs = []
    for i, (d, meal, item, prepared, consumed) in enumerate(rows, start=1):
        item_id = items.setdefault(item, len(items) + 1)
        recs.append({
            "id": i, "date": d, "meal": meal, "food_item": item, "item_id": item_id, "category": "General",
            "unit": "servings", "prepared": float(prepared), "consumed": float(consumed),
            "leftovers": float(prepared - consumed), "supplied_leftovers": None, "mismatch": 0,
            "redistributed": 0.0, "student_count": None, "notes": None, "source": "manual",
            "import_id": None, "created_at": "2026-01-01 00:00:00",
        })
    df = pd.DataFrame(recs)
    df["date_dt"] = pd.to_datetime(df["date"])
    return df


def weekday_rows(days=60, item="Rice", meal="Lunch", start=date(2026, 1, 5), base=100, wk=0.5, prep=1.2):
    rows = []
    for i in range(days):
        d = start + timedelta(days=i)
        consumed = base * (wk if d.weekday() >= 5 else 1) + (i % 3)
        rows.append((d.isoformat(), meal, item, round(consumed * prep), round(consumed)))
    return rows
