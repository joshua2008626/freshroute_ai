import sqlite3
import unittest

from freshroute import repository as repo
from freshroute.db import connect
from tests.helpers import AppTestCase


class Database(AppTestCase):
    def test_tables_and_default_settings_are_created_automatically(self):
        names = {r["name"] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ("settings", "food_items", "meal_records", "preparation_plans", "forecast_records", "import_history"):
            self.assertIn(table, names)
        self.assertEqual(repo.get_settings(self.db)["meals"][0], "Breakfast")

    def test_insert_update_delete_roundtrip(self):
        clean = self.add("2026-05-01", "Lunch", "Rice", 100, 80)
        row = repo.fetch_records(self.db).iloc[0]
        self.assertEqual((row["prepared"], row["consumed"], row["leftovers"]), (100, 80, 20))
        clean.update(consumed=90, leftovers=10)
        repo.update_record(self.db, int(row["id"]), clean)
        self.assertEqual(repo.fetch_records(self.db).iloc[0]["leftovers"], 10)
        self.assertTrue(repo.delete_record(self.db, int(row["id"])))
        self.assertTrue(repo.fetch_records(self.db).empty)

    def test_duplicates_are_rejected_not_replaced(self):
        clean = self.add("2026-05-01", "Lunch", "Rice", 100, 80)
        clean2 = dict(clean, consumed=10, leftovers=90)
        with self.assertRaises(sqlite3.IntegrityError):
            repo.insert_record(self.db, clean2, "manual")
        self.db.rollback()
        self.assertEqual(repo.fetch_records(self.db).iloc[0]["consumed"], 80)

    def test_item_names_are_case_insensitive_unique(self):
        self.add("2026-05-01", "Lunch", "Rice", 100, 80)
        self.add("2026-05-02", "Lunch", "rice", 100, 80)
        self.assertEqual(len(repo.list_items(self.db)), 1)

    def test_database_constraints_block_bad_data_even_with_raw_sql(self):
        self.add("2026-05-01", "Lunch", "Rice", 100, 80)
        bad = [
            "UPDATE meal_records SET quantity_consumed = 500",           # consumed > prepared
            "UPDATE meal_records SET quantity_prepared = -1",            # negative
            "UPDATE meal_records SET service_date = '01/05/2026'",       # date format
            "UPDATE meal_records SET source = 'hacked'",                 # unknown source
            "UPDATE meal_records SET redistributed = 999",               # more than leftovers
        ]
        for sql in bad:
            with self.assertRaises(sqlite3.IntegrityError, msg=sql):
                self.db.execute(sql)
            self.db.rollback()

    def test_foreign_keys_enforced(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "INSERT INTO meal_records (service_date, meal, food_item_id, unit, quantity_prepared, "
                "quantity_consumed, leftovers, source) VALUES ('2026-05-01','Lunch',999,'servings',1,1,0,'manual')")

    def test_search_is_parameterised_against_sql_injection(self):
        self.add("2026-05-01", "Lunch", "Rice", 100, 80)
        for evil in ("'; DROP TABLE meal_records; --", "%' OR '1'='1", "Rice' OR 1=1 --"):
            frame = repo.fetch_records(self.db, search=evil)
            self.assertTrue(frame.empty, evil)
        self.assertEqual(len(repo.fetch_records(self.db)), 1)     # table still exists with its row
        self.assertEqual(len(repo.fetch_records(self.db, search="ric")), 1)

    def test_like_wildcards_in_search_are_escaped(self):
        self.add("2026-05-01", "Lunch", "Rice", 100, 80)
        self.assertTrue(repo.fetch_records(self.db, search="%").empty)
        self.assertTrue(repo.fetch_records(self.db, search="_ice").empty)

    def test_filters(self):
        self.add("2026-05-01", "Lunch", "Rice", 100, 80)
        self.add("2026-05-02", "Dinner", "Dosa", 50, 40)
        self.assertEqual(len(repo.fetch_records(self.db, meal="Dinner")), 1)
        self.assertEqual(len(repo.fetch_records(self.db, start="2026-05-02")), 1)
        self.assertEqual(len(repo.fetch_records(self.db, end="2026-05-01")), 1)
        self.assertEqual(len(repo.fetch_records(self.db, item="dosa")), 1)

    def test_scope_separates_demo_from_user_data(self):
        self.add("2026-05-01", "Lunch", "Rice", 100, 80, source="demo")
        self.add("2026-05-02", "Lunch", "Rice", 100, 80, source="manual")
        self.assertEqual(len(repo.fetch_records(self.db, scope="demo")), 1)
        self.assertEqual(len(repo.fetch_records(self.db, scope="mine")), 1)
        self.assertEqual(len(repo.fetch_records(self.db, scope="all")), 2)

    def test_delete_by_source_leaves_other_sources(self):
        self.add("2026-05-01", "Lunch", "Rice", 100, 80, source="demo")
        self.add("2026-05-02", "Lunch", "Rice", 100, 80, source="manual")
        self.assertEqual(repo.delete_by_source(self.db, "demo"), 1)
        remaining = repo.fetch_records(self.db)
        self.assertEqual(remaining["source"].tolist(), ["manual"])
        self.assertEqual(len(repo.list_items(self.db)), 1)       # item still used by the manual record

    def test_unused_items_are_pruned(self):
        self.add("2026-05-01", "Lunch", "Rice", 100, 80, source="demo")
        repo.delete_by_source(self.db, "demo")
        self.assertEqual(repo.list_items(self.db), [])

    def test_settings_roundtrip(self):
        repo.save_settings(self.db, {"cafeteria_name": "Test Hall", "meals": ["A", "B"], "safety_buffer_pct": 7.5})
        s = repo.get_settings(self.db)
        self.assertEqual((s["cafeteria_name"], s["meals"], s["safety_buffer_pct"]), ("Test Hall", ["A", "B"], 7.5))

    def test_plan_upsert_and_forecast_log(self):
        self.add("2026-05-01", "Lunch", "Rice", 100, 80)
        item = repo.get_item_by_name(self.db, "Rice")
        kw = dict(plan_date="2026-05-10", meal="Lunch", item_id=item["id"], forecast_qty=80, suggested_qty=84,
                  unit="servings", method="baseline_recent_mean", note=None, override=False)
        repo.save_plan(self.db, final_qty=84, **kw)
        repo.save_plan(self.db, final_qty=90, **kw)
        plans = repo.list_plans(self.db, "2026-05-10")
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]["final_qty"], 90)
        repo.log_forecast(self.db, target_date="2026-05-10", meal="Lunch", item_id=item["id"], predicted=80,
                          recommended=84, method="baseline_recent_mean", n_obs=1, scope="all")
        self.assertEqual(repo.forecast_log_for(self.db, "2026-05-10", "Lunch", item["id"])["predicted"], 80)

    def test_import_history(self):
        i = repo.log_import(self.db, filename="a.csv", sha="x" * 64, total=3, valid=2, invalid=1, duplicate=0, imported=2)
        self.db.commit()
        self.assertEqual(repo.find_import_by_hash(self.db, "x" * 64)["id"], i)
        self.assertEqual(len(repo.list_imports(self.db)), 1)

    def test_init_db_is_idempotent(self):
        from freshroute.db import init_db
        self.add("2026-05-01", "Lunch", "Rice", 100, 80)
        init_db(self.app.config["DATABASE"])
        self.assertEqual(len(repo.fetch_records(self.db)), 1)


if __name__ == "__main__":
    unittest.main()
