import unittest

from freshroute import recommendations as rec
from tests.helpers import make_frame

SETTINGS = {"safety_buffer_pct": 5.0, "max_reduction_pct": 25.0, "min_prep_default": 0.0}


def fcast(estimate, unit="servings", method="baseline_weekday_median", n=30):
    return {"item": "Rice", "meal": "Lunch", "unit": unit, "target_date": "2026-05-01", "estimate": estimate,
            "method": method, "method_label": "Baseline", "n_obs": n, "warnings": [], "explanation": ["because"]}


def hist(prepared=120, consumed=90, n=10):
    return make_frame([(f"2026-04-{i + 1:02d}", "Lunch", "Rice", prepared, consumed) for i in range(n)])


class Recommendations(unittest.TestCase):
    def test_buffer_and_rounding_up(self):
        r = rec.recommend(fcast(100), hist(prepared=105, consumed=95), SETTINGS, None)
        self.assertEqual(r["suggested"], 105)                       # 100 * 1.05
        r = rec.recommend(fcast(100.2), hist(prepared=105, consumed=95), SETTINGS, None)
        self.assertEqual(r["suggested"], 106)                       # rounded up, never down

    def test_difference_from_usual(self):
        r = rec.recommend(fcast(90), hist(prepared=120, consumed=100), SETTINGS, None)
        self.assertEqual(r["usual"], 120)
        self.assertEqual(r["suggested"], 95)
        self.assertEqual(r["difference"], -25)

    def test_reduction_is_capped(self):
        r = rec.recommend(fcast(40), hist(prepared=120, consumed=100), SETTINGS, None)
        self.assertEqual(r["suggested"], 90)                        # 120 * 0.75
        self.assertTrue(any("at most a 25% reduction" in c for c in r["constraints"]))

    def test_item_and_global_minimums(self):
        r = rec.recommend(fcast(10), hist(prepared=12, consumed=10), SETTINGS, 50)
        self.assertEqual(r["suggested"], 50)
        r = rec.recommend(fcast(10), hist(prepared=12, consumed=10), dict(SETTINGS, min_prep_default=60), None)
        self.assertEqual(r["suggested"], 60)
        self.assertTrue(r["constraints"])

    def test_recent_stockouts_prevent_cuts(self):
        h = hist(prepared=100, consumed=100)                       # always sold out
        r = rec.recommend(fcast(80), h, SETTINGS, None)
        self.assertGreaterEqual(r["suggested"], 100)
        self.assertEqual(r["stockouts"], 5)
        self.assertTrue(any("Recent stock-outs" in w for w in r["warnings"]))

    def test_no_forecast_means_no_suggestion(self):
        r = rec.recommend(fcast(None, method="no_data"), hist(), SETTINGS, None)
        self.assertIsNone(r["suggested"])
        self.assertIn("No forecast", r["reasons"][0])

    def test_always_states_it_is_an_estimate(self):
        r = rec.recommend(fcast(100), hist(), SETTINGS, None)
        self.assertTrue(any("estimate, not a guarantee" in x for x in r["reasons"]))

    def test_kg_rounds_to_half_units(self):
        r = rec.recommend(fcast(10.1, unit="kg"), hist(prepared=11, consumed=10), SETTINGS, None)
        self.assertEqual(r["suggested"] % 0.5, 0)
        self.assertGreaterEqual(r["suggested"], 10.1 * 1.05)

    def test_usual_preparation_is_median_of_recent(self):
        self.assertEqual(rec.usual_preparation(hist(prepared=120)), 120)
        self.assertIsNone(rec.usual_preparation(hist().iloc[0:0]))


if __name__ == "__main__":
    unittest.main()
