import unittest

import pandas as pd

from freshroute import waste
from tests.helpers import make_frame


class WasteMath(unittest.TestCase):
    def test_leftovers_and_percentage(self):
        self.assertEqual(waste.derive_leftovers(100, 80), 20)
        self.assertEqual(waste.waste_pct(20, 100), 20.0)
        self.assertEqual(waste.waste_pct(0, 100), 0.0)

    def test_zero_prepared_is_safe(self):
        self.assertIsNone(waste.waste_pct(0, 0))
        self.assertIsNone(waste.waste_pct(5, 0))
        self.assertIsNone(waste.unredistributed_pct(0, 0, 0))

    def test_consumed_above_prepared_raises(self):
        with self.assertRaises(ValueError):
            waste.derive_leftovers(10, 12)
        with self.assertRaises(ValueError):
            waste.derive_leftovers(-1, 0)

    def test_tiny_rounding_difference_is_tolerated(self):
        self.assertEqual(waste.derive_leftovers(10.004, 10.0), 0.004)
        self.assertEqual(waste.derive_leftovers(10.0, 10.004), 0.0)

    def test_leftover_conflict_detection(self):
        self.assertFalse(waste.leftovers_conflict(None, 20))
        self.assertFalse(waste.leftovers_conflict(20.005, 20))
        self.assertTrue(waste.leftovers_conflict(18, 20))

    def test_unredistributed_percentage(self):
        self.assertEqual(waste.unredistributed_pct(20, 5, 100), 15.0)

    def test_aggregate_is_ratio_of_sums_not_mean_of_percentages(self):
        df = make_frame([("2026-01-01", "Lunch", "A", 10, 5), ("2026-01-02", "Lunch", "A", 1000, 990)])
        s = waste.summarize(df)
        # per-row: 50% and 1% (mean 25.5%); correct aggregate: 15 / 1010 = 1.49%
        self.assertEqual(s["waste_pct"], 1.49)
        self.assertEqual(s["leftovers"], 15)

    def test_empty_summary(self):
        s = waste.summarize(pd.DataFrame())
        self.assertEqual(s["records"], 0)
        self.assertIsNone(s["waste_pct"])

    def test_units_are_never_mixed(self):
        df = make_frame([("2026-01-01", "Lunch", "A", 10, 5), ("2026-01-02", "Lunch", "B", 10, 5)])
        df.loc[1, "unit"] = "kg"
        df = pd.concat([df, df.iloc[[0]]], ignore_index=True)   # servings is the majority unit
        frame, unit, excluded = waste.restrict_to_unit(df, None)
        self.assertEqual(unit, "servings")
        self.assertEqual(excluded, 1)
        self.assertTrue((frame["unit"] == "servings").all())

    def test_safe_number(self):
        self.assertIsNone(waste.safe_number(float("nan")))
        self.assertIsNone(waste.safe_number(float("inf")))
        self.assertEqual(waste.safe_number("3.5"), 3.5)


if __name__ == "__main__":
    unittest.main()
