import csv
import io
import unittest
from datetime import date

from freshroute import analytics, demo, reports, validation
from freshroute import repository as repo
from tests.helpers import AppTestCase


class Exports(AppTestCase):
    def test_records_csv_has_headers_units_and_source_labels(self):
        self.add("2026-05-01", "Lunch", "Rice", 100, 80, source="demo", notes="ok")
        text = reports.records_csv(repo.fetch_records(self.db))
        rows = list(csv.reader(io.StringIO(text)))
        self.assertEqual(rows[0][:4], ["date", "meal", "food_item", "category"])
        self.assertIn("quantity_prepared", rows[0])
        row = dict(zip(rows[0], rows[1]))
        self.assertEqual((row["quantity_prepared"], row["quantity_consumed"], row["leftovers"]), ("100.0", "80.0", "20.0"))
        self.assertEqual(row["data_source"], "Synthetic demo")

    def test_csv_formula_injection_is_neutralised(self):
        self.add("2026-05-01", "Lunch", "Rice", 100, 80, notes="=HYPERLINK(\"http://x\")")
        text = reports.records_csv(repo.fetch_records(self.db))
        self.assertIn("'=HYPERLINK", text)
        self.assertNotIn(",=HYPERLINK", text)

    def test_empty_export_still_has_header(self):
        text = reports.records_csv(repo.fetch_records(self.db))
        self.assertEqual(len(text.strip().splitlines()), 1)

    def test_summary_csv_and_pdf(self):
        self.add_series(days=40)
        frame = repo.fetch_records(self.db)
        report = reports.build_report(frame, cafeteria="Test", start=None, end=None, unit="servings",
                                      excluded_rows=0, evaluation=None, settings=repo.get_settings(self.db),
                                      item_costs={}, source_counts=repo.source_counts(self.db))
        summary = reports.summary_csv(report)
        self.assertIn("Waste %", summary)
        self.assertIn("By item", summary)
        pdf = reports.report_pdf(report)
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertGreater(len(pdf), 1500)

    def test_report_labels_demo_data(self):
        self.add_series(days=12, source="demo")
        frame = repo.fetch_records(self.db)
        report = reports.build_report(frame, cafeteria="T", start=None, end=None, unit="servings", excluded_rows=0,
                                      evaluation=None, settings=repo.get_settings(self.db), item_costs={},
                                      source_counts={})
        self.assertTrue(report["has_demo"])
        self.assertIn("Synthetic demo", report["source_labels"])


class Insights(AppTestCase):
    def test_no_insight_without_enough_data(self):
        for i in range(3):
            self.add(f"2026-05-0{i + 1}", "Lunch", "Rice", 100, 40)
        frame = repo.fetch_records(self.db)
        self.assertEqual(analytics.insights(frame, repo.get_settings(self.db), {}), [])

    def test_high_waste_insight_only_when_supported(self):
        for i in range(8):
            self.add(f"2026-05-{i + 1:02d}", "Lunch", "Rice", 100, 60)        # 40% waste, consistently
            self.add(f"2026-05-{i + 1:02d}", "Lunch", "Dosa", 100, 98)        # 2% waste
        frame = repo.fetch_records(self.db)
        found = analytics.insights(frame, repo.get_settings(self.db), {})
        texts = " ".join(i["text"] for i in found)
        self.assertIn("Rice leftovers have been consistently high", texts)
        self.assertNotIn("Dosa leftovers", texts)

    def test_cost_estimate_needs_user_cost_and_is_labelled(self):
        for i in range(8):
            self.add(f"2026-05-{i + 1:02d}", "Lunch", "Rice", 100, 60)
        frame = repo.fetch_records(self.db)
        s = repo.get_settings(self.db)
        self.assertIsNone(analytics.estimate_avoidable_cost(frame, s, {}))
        est = analytics.estimate_avoidable_cost(frame, s, {"Rice": 2.0})
        # leftovers 320 - 5% of 800 prepared (40) = 280 avoidable * 2.0
        self.assertEqual(est["estimate"], 560.0)
        found = analytics.insights(frame, s, {"Rice": 2.0})
        cost = [i for i in found if i["kind"] == "cost"][0]
        self.assertEqual(cost["severity"], "estimate")
        self.assertIn("estimate", cost["text"].lower())

    def test_no_environmental_claims_anywhere(self):
        for i in range(8):
            self.add(f"2026-05-{i + 1:02d}", "Lunch", "Rice", 100, 60)
        text = " ".join(i["text"] for i in analytics.insights(repo.fetch_records(self.db), repo.get_settings(self.db), {"Rice": 1}))
        for word in ("CO2", "carbon", "emission", "trees"):
            self.assertNotIn(word.lower(), text.lower())


class DemoData(AppTestCase):
    def seed(self):
        return demo.seed_demo(self.db, repo.build_context, validation.validate_record, today=date(2026, 6, 1))

    def test_demo_rows_all_valid_and_labelled_demo(self):
        result = self.seed()
        self.assertGreater(result["inserted"], 500)
        self.assertEqual(result["rejected"], 0)
        counts = repo.source_counts(self.db)
        self.assertEqual((counts["upload"], counts["manual"]), (0, 0))
        self.assertEqual(counts["demo"], result["inserted"])
        self.assertIn("fictional", repo.get_settings(self.db)["cafeteria_name"])

    def test_seed_is_deterministic_and_never_duplicates(self):
        first = self.seed()
        again = self.seed()
        self.assertEqual(again["inserted"], 0)
        self.assertEqual(again["skipped"], first["inserted"])

    def test_demo_has_enough_history_for_evaluation(self):
        self.seed()
        from freshroute import forecasting
        frame = repo.fetch_records(self.db)
        eng = forecasting.ForecastEngine(frame[frame["unit"] == "servings"], "servings")
        ev = eng.evaluate()
        self.assertIn(ev["status"], ("ml_selected", "ml_not_better"))

    def test_reset_only_touches_demo_records(self):
        self.seed()
        self.add("2026-05-30", "Lunch", "My Own Dish", 50, 40, source="manual")
        result = demo.reset_demo(self.db, repo.build_context, validation.validate_record, today=date(2026, 6, 1))
        self.assertGreater(result["removed"], 500)
        mine = repo.fetch_records(self.db, scope="mine")
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine.iloc[0]["food_item"], "My Own Dish")

    def test_demo_includes_multiple_meals_items_and_a_second_unit(self):
        self.seed()
        frame = repo.fetch_records(self.db)
        self.assertGreaterEqual(frame["meal"].nunique(), 4)
        self.assertGreaterEqual(frame["food_item"].nunique(), 10)
        self.assertGreaterEqual(frame["unit"].nunique(), 2)


if __name__ == "__main__":
    unittest.main()
