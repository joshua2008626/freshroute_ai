"""Time Machine: counterfactual replay with honest bounds."""
import json
import unittest
from datetime import date, timedelta

import pandas as pd

from freshroute import backtest, recommendations
from tests.helpers import AppTestCase, make_frame


def steady(days=30, prepared=130, consumed=100, last_consumed=None, last_prepared=None):
    start = date(2026, 1, 1)
    rows = []
    for i in range(days):
        d = (start + timedelta(days=i)).isoformat()
        p, c = prepared, consumed
        if i == days - 1:
            p = last_prepared if last_prepared is not None else p
            c = last_consumed if last_consumed is not None else c
        rows.append((d, "Lunch", "Rice", p, c))
    return make_frame(rows)


class PolicyConsistencyTests(unittest.TestCase):
    def test_suggest_quantity_matches_recommend(self):
        hist = steady(20)
        fc = {"unit": "servings", "estimate": 100.0, "item": "Rice", "meal": "Lunch",
              "target_date": "2026-02-01", "method": "m", "method_label": "m", "n_obs": 20}
        settings = {"safety_buffer_pct": 10, "max_reduction_pct": 25, "min_prep_default": 0}
        rec = recommendations.recommend(fc, hist, settings, None)
        s, _, _ = recommendations.suggest_quantity(
            100.0, recommendations.usual_preparation(hist), recommendations.recent_stockouts(hist),
            "servings", 10, 25, 0)
        self.assertEqual(rec["suggested"], s)

    def test_cut_cap_and_stockout_rules(self):
        s, _, notes = recommendations.suggest_quantity(50, 130, 0, "servings", 5, 25)
        self.assertEqual(s, 98)  # capped at 25% below usual 130 -> 97.5 -> 98
        self.assertTrue(any(k == "constraint" for k, _ in notes))
        s, _, notes = recommendations.suggest_quantity(50, 130, 3, "servings", 5, 25)
        self.assertEqual(s, 130)
        self.assertTrue(any(k == "warning" for k, _ in notes))


class BacktestTests(unittest.TestCase):
    def test_no_leakage_final_day_does_not_change_its_own_suggestion(self):
        a = backtest.prepare_rows(steady(30, last_consumed=100), days=7)["rows"]
        b = backtest.prepare_rows(steady(30, last_consumed=9999, last_prepared=10000), days=7)["rows"]
        self.assertEqual(a.iloc[-1]["est"], b.iloc[-1]["est"])
        self.assertEqual(a.iloc[-1]["usual"], b.iloc[-1]["usual"])

    def test_exact_outcome_when_demand_is_observed(self):
        # demand 100, kitchen prepares 130 (30 leftover). Suggestion ~ 105.
        rows = backtest.prepare_rows(steady(30), days=14)["rows"]
        res = backtest.evaluate_policy(rows, 5, 25)
        self.assertEqual(res["n_ran_out"], 0)
        self.assertEqual(res["policy"]["leftovers_low"], res["policy"]["leftovers_high"])
        self.assertLess(res["policy"]["leftovers_high"], res["actual"]["leftovers"])
        self.assertEqual(res["policy"]["short_sure"], 0)

    def test_stockout_rows_give_ranges_not_points(self):
        rows = []
        start = date(2026, 1, 1)
        for i in range(30):
            rows.append(((start + timedelta(days=i)).isoformat(), "Lunch", "Rice", 100, 100))  # always ran out
        res = backtest.evaluate_policy(backtest.prepare_rows(make_frame(rows), days=14)["rows"], 5, 25)
        self.assertEqual(res["n_ran_out"], res["n"])
        # suggestion >= prepared (stock-out rule keeps usual), so leftovers could be anything 0..P-A
        self.assertEqual(res["policy"]["leftovers_low"], 0.0)
        self.assertGreaterEqual(res["policy"]["leftovers_high"], res["policy"]["leftovers_low"])
        # shortfall is never claimed for rows where P >= prepared
        self.assertEqual(res["policy"]["short_sure"], 0)
        self.assertEqual(res["policy"]["short_maybe"], res["n"])

    def test_reduction_claim_uses_worst_case(self):
        rows = backtest.prepare_rows(steady(30), days=14)["rows"]
        res = backtest.evaluate_policy(rows, 5, 25)
        self.assertAlmostEqual(res["leftovers_reduced_at_least"],
                               res["actual"]["leftovers"] - res["policy"]["leftovers_high"])

    def test_higher_buffer_never_increases_certain_shortfalls(self):
        frame = steady(30)
        frame.loc[frame.index[10::3], "consumed"] = 125.0
        frame["leftovers"] = frame["prepared"] - frame["consumed"]
        fr = backtest.frontier(backtest.prepare_rows(frame, days=14)["rows"], 25, 0, "servings")
        shorts = [f["short_sure_pct"] for f in fr]
        self.assertEqual(shorts, sorted(shorts, reverse=True))

    def test_insufficient_history(self):
        res = backtest.run(steady(2), {"safety_buffer_pct": 5, "max_reduction_pct": 25}, "servings")
        self.assertEqual(res["status"], "insufficient")
        self.assertEqual(backtest.run(pd.DataFrame(), {}, "servings")["status"], "insufficient")

    def test_run_is_json_serialisable(self):
        res = backtest.run(steady(40), {"safety_buffer_pct": 5, "max_reduction_pct": 25}, "servings")
        json.dumps(res)
        self.assertNotIn("_P", res)


class TimeMachineRouteTests(AppTestCase):
    def test_empty_state(self):
        r = self.client.get("/time-machine")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Not enough history", r.get_data(as_text=True))
        self.assertEqual(self.client.get("/api/backtest").get_json()["status"], "insufficient")

    def test_page_with_data_and_bad_params(self):
        self.add_series(days=60)
        for url in ("/time-machine", "/time-machine?days=14&buffer=12", "/time-machine?days=zzz&buffer=-5",
                    "/time-machine?days=9999&buffer=1e9"):
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, url)
        body = self.client.get("/time-machine").get_data(as_text=True)
        self.assertIn("ranges", body)
        self.assertIn("tm_frontier", body)
        api = self.client.get("/api/backtest?days=9999").get_json()
        self.assertLessEqual(api["days"], 90)

    def test_nav_has_time_machine(self):
        self.assertIn("/time-machine", self.client.get("/dashboard").get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
