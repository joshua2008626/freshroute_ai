import unittest
from datetime import date, timedelta

import numpy as np
import pandas as pd

from freshroute import forecasting as fc
from tests.helpers import make_frame, weekday_rows


def engine(rows, **kw):
    return fc.ForecastEngine(make_frame(rows), "servings", **kw)


class Baseline(unittest.TestCase):
    def test_weekday_median_used_when_enough_same_weekday_history(self):
        rows = weekday_rows(days=56)
        eng = engine(rows)
        target = date(2026, 1, 5) + timedelta(days=56)           # a Monday after the history
        res = eng.forecast(1, "Rice", "Lunch", target)
        self.assertEqual(res["method"], "baseline_weekday_median")
        self.assertAlmostEqual(res["estimate"], 101, delta=2)
        saturday = date(2026, 1, 5) + timedelta(days=61)
        res_sat = eng.forecast(1, "Rice", "Lunch", saturday)
        self.assertAlmostEqual(res_sat["estimate"], 51, delta=2)  # weekend dip is recognised

    def test_recent_mean_when_few_same_weekday_observations(self):
        rows = [("2026-03-0%d" % d, "Lunch", "Rice", 120, 100) for d in range(2, 6)]   # Mon-Thu
        res = engine(rows).forecast(1, "Rice", "Lunch", date(2026, 3, 9))
        self.assertEqual(res["method"], "baseline_recent_mean")

    def test_limited_history_is_flagged(self):
        res = engine([("2026-03-02", "Lunch", "Rice", 120, 100), ("2026-03-03", "Lunch", "Rice", 120, 110)]
                     ).forecast(1, "Rice", "Lunch", date(2026, 3, 9))
        self.assertEqual(res["method"], "limited_history_mean")
        self.assertEqual(res["estimate"], 105)
        self.assertTrue(any("Very limited" in w for w in res["warnings"]))
        self.assertEqual(res["data_support"]["label"], "Limited")
        self.assertIsNone(res["range"])

    def test_no_history_gives_no_estimate(self):
        eng = engine([("2026-03-02", "Lunch", "Rice", 120, 100)])
        res = eng.forecast(99, "Ghost", "Lunch", date(2026, 3, 9))
        self.assertIsNone(res["estimate"])
        self.assertEqual(res["method"], "no_data")
        self.assertTrue(res["warnings"])

    def test_estimate_never_negative_or_invented(self):
        res = engine(weekday_rows(days=30)).forecast(1, "Rice", "Lunch", date(2026, 3, 1))
        self.assertGreaterEqual(res["estimate"], 0)

    def test_range_only_with_enough_observations(self):
        res = engine(weekday_rows(days=30)).forecast(1, "Rice", "Lunch", date(2026, 3, 1))
        self.assertIsNotNone(res["range"])
        self.assertIn("not a statistical prediction interval", res["range"]["basis"])
        self.assertLessEqual(res["range"]["low"], res["range"]["high"])


class InsufficientData(unittest.TestCase):
    def test_tiny_dataset_reports_no_metrics_and_falls_back(self):
        eng = engine(weekday_rows(days=10))
        ev = eng.evaluate()
        self.assertEqual(ev["status"], "insufficient")
        self.assertIsNone(ev["baseline"])
        self.assertIsNone(ev["ml"])
        self.assertEqual(ev["selected"], "baseline")
        self.assertIn("no accuracy is claimed", ev["message"])
        res = eng.forecast(1, "Rice", "Lunch", date(2026, 3, 1))
        self.assertTrue(res["method"].startswith(("baseline", "limited")))
        self.assertIsNone(res["evaluation"]["baseline_mae"])

    def test_moderate_dataset_gives_baseline_metric_but_no_ml(self):
        ev = engine(weekday_rows(days=60)).evaluate()
        self.assertEqual(ev["status"], "baseline_only")
        self.assertIsNotNone(ev["baseline"])
        self.assertIsNone(ev["ml"])
        self.assertEqual(ev["selected"], "baseline")
        self.assertIn("needs", ev["message"])

    def test_empty_frame(self):
        ev = fc.ForecastEngine(pd.DataFrame(columns=["unit"]), "servings").evaluate()
        self.assertEqual(ev["status"], "insufficient")


class NoLeakage(unittest.TestCase):
    def test_features_for_a_row_ignore_that_row_and_later_rows(self):
        rows = weekday_rows(days=40)
        base = fc.add_features(make_frame(rows))
        # Wildly change every observation from day 25 onwards.
        changed_rows = [(d, m, i, p, c) if k < 25 else (d, m, i, 5000, 4000) for k, (d, m, i, p, c) in enumerate(rows)]
        changed = fc.add_features(make_frame(changed_rows))
        cols = ["lag1", "roll5", "dow_mean", "n_prior"]
        pd.testing.assert_frame_equal(base.iloc[:25][cols].reset_index(drop=True),
                                      changed.iloc[:25][cols].reset_index(drop=True))
        # ... and the row's own target never feeds its own lag features
        self.assertTrue(pd.isna(base.iloc[0]["lag1"]))
        self.assertEqual(base.iloc[1]["lag1"], make_frame(rows).iloc[0]["consumed"])

    def test_forecast_for_a_date_ignores_records_on_or_after_that_date(self):
        rows = weekday_rows(days=50)
        target = date(2026, 1, 5) + timedelta(days=30)
        clean = engine(rows[:30]).forecast(1, "Rice", "Lunch", target)
        contaminated = rows[:30] + [(d, m, i, 9000, 8000) for (d, m, i, p, c) in rows[30:]]
        poisoned = engine(contaminated).forecast(1, "Rice", "Lunch", target)
        self.assertEqual(clean["estimate"], poisoned["estimate"])
        self.assertEqual(clean["n_obs"], poisoned["n_obs"])

    def test_chronological_split_train_strictly_before_test(self):
        rows = weekday_rows(days=120)
        eng = engine(rows)
        ev = eng.evaluate()
        split = pd.Timestamp(ev["split_date"])
        series_dates = pd.to_datetime([r["date"] for r in ev["series"]])
        self.assertTrue((series_dates >= split).all())
        self.assertGreater(ev["n_train"], 0)
        # test rows are the latest ~30% of dates, never a shuffled sample
        all_dates = sorted(eng.frame["date_dt"].unique())
        self.assertAlmostEqual(len(series_dates) / len(all_dates), fc.TEST_FRACTION, delta=0.06)

    def test_walk_forward_prediction_for_a_row_ignores_later_observations(self):
        rows = weekday_rows(days=100)
        a = engine(rows).evaluate()
        # Corrupt only the final day. Every earlier held-out prediction must be identical,
        # because each is built from strictly earlier observations.
        last = rows[-1]
        b = engine(rows[:-1] + [(last[0], last[1], last[2], 9000, 8000)]).evaluate()
        sa = {r["date"]: r["baseline"] for r in a["series"]}
        sb = {r["date"]: r["baseline"] for r in b["series"]}
        earlier = [d for d in sa if d != last[0]]
        self.assertTrue(earlier)
        for d in earlier:
            self.assertEqual(sa[d], sb[d], d)

    def test_metrics_are_computed_on_heldout_rows_only(self):
        rows = weekday_rows(days=100)
        ev = engine(rows).evaluate()
        self.assertGreater(ev["n_test"], fc.MIN_EVAL_ROWS - 1)
        self.assertLess(ev["n_test"], len(rows))
        self.assertGreater(ev["baseline"]["mae"], 0 - 1e-9)


class MachineLearning(unittest.TestCase):
    def multi_item_rows(self, days=100):
        rows = []
        for item, base in (("Rice", 120), ("Dosa", 80), ("Tea", 200), ("Poha", 60)):
            rows += weekday_rows(days=days, item=item, base=base)
        return rows

    def test_ml_is_evaluated_with_enough_data_and_reports_both_metrics(self):
        ev = engine(self.multi_item_rows()).evaluate()
        self.assertIn(ev["status"], ("ml_selected", "ml_not_better"))
        self.assertIsNotNone(ev["ml"])
        self.assertIsNotNone(ev["baseline"])
        for key in ("mae", "rmse", "bias"):
            self.assertIn(key, ev["ml"])

    def test_selection_rule_is_honoured(self):
        ev = engine(self.multi_item_rows()).evaluate()
        better = ev["ml"]["mae"] <= ev["baseline"]["mae"] * (1 - fc.ML_MIN_IMPROVEMENT)
        self.assertEqual(ev["selected"] == "ml", better)

    def test_ml_never_selected_without_heldout_comparison(self):
        ev = engine(weekday_rows(days=60)).evaluate()
        self.assertNotEqual(ev["selected"], "ml")

    def test_ml_forecast_has_method_label_and_sane_value(self):
        eng = engine(self.multi_item_rows())
        eng._evaluation = dict(eng.evaluate(), selected="ml")     # force the ML path to exercise it
        res = eng.forecast(1, "Rice", "Lunch", date(2026, 1, 5) + timedelta(days=100))
        self.assertEqual(res["method"], "ml_random_forest")
        self.assertGreater(res["estimate"], 0)
        self.assertLess(res["estimate"], 400)

    def test_model_is_persisted_with_joblib(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            eng = engine(self.multi_item_rows(), model_dir=Path(tmp))
            eng._evaluation = dict(eng.evaluate(), selected="ml")
            eng.forecast(1, "Rice", "Lunch", date(2026, 6, 1))
            self.assertTrue(list(Path(tmp).glob("random_forest_*.joblib")))


class PerStudentRate(unittest.TestCase):
    def test_used_only_with_attendance_history_and_input(self):
        rows = weekday_rows(days=30)
        df = make_frame(rows)
        df["student_count"] = 1000
        eng = fc.ForecastEngine(df, "servings")
        res = eng.forecast(1, "Rice", "Lunch", date(2026, 3, 1), expected_students=500)
        self.assertEqual(res["method"], "per_student_rate")
        self.assertTrue(res["used_expected_students"])
        self.assertLess(res["estimate"], 100)                      # half the usual attendance -> ~half the demand
        without = eng.forecast(1, "Rice", "Lunch", date(2026, 3, 1))
        self.assertNotEqual(without["method"], "per_student_rate")

    def test_ignored_with_explanation_when_no_attendance_in_history(self):
        eng = engine(weekday_rows(days=30))
        res = eng.forecast(1, "Rice", "Lunch", date(2026, 3, 1), expected_students=500)
        self.assertFalse(res["used_expected_students"])
        self.assertTrue(any("ignored" in w for w in res["warnings"]))


class Cache(unittest.TestCase):
    def test_engine_cache_refreshes_when_data_changes(self):
        fc.clear_cache()
        df = make_frame(weekday_rows(days=20))
        e1 = fc.get_engine(df, "servings", "k")
        self.assertIs(e1, fc.get_engine(df, "servings", "k"))
        df2 = make_frame(weekday_rows(days=21))
        self.assertIsNot(e1, fc.get_engine(df2, "servings", "k"))


if __name__ == "__main__":
    unittest.main()
