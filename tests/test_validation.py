import unittest
from datetime import date

from freshroute import validation as v
from freshroute.validation import ValidationContext, analyze_csv, validate_record

CTX = ValidationContext(meals=["Breakfast", "Lunch", "Snacks", "Dinner"],
                        units=["servings", "kg", "litres"], today=date(2026, 6, 1))
HEADER = "date,meal,food_item,quantity_prepared,quantity_consumed,unit,student_count\n"


def good(**kw):
    row = {"date": "2026-05-20", "meal": "Lunch", "food_item": "Rice", "quantity_prepared": "100",
           "quantity_consumed": "80", "unit": "servings"}
    row.update(kw)
    return row


class RecordValidation(unittest.TestCase):
    def test_valid_row_derives_leftovers(self):
        clean, errors, warnings = validate_record(good(), CTX)
        self.assertEqual(errors, [])
        self.assertEqual(clean["leftovers"], 20)
        self.assertEqual(clean["date"], "2026-05-20")

    def test_missing_required_values(self):
        for field in ("date", "meal", "food_item", "quantity_prepared", "quantity_consumed"):
            clean, errors, _ = validate_record(good(**{field: ""}), CTX)
            self.assertIsNone(clean, field)
            self.assertTrue(errors, field)

    def test_invalid_date_formats(self):
        for bad in ("2026-13-45", "20/05/2026", "yesterday", "2026-02-30"):
            clean, errors, _ = validate_record(good(date=bad), CTX)
            self.assertIsNone(clean, bad)
            self.assertIn("YYYY-MM-DD", errors[0])

    def test_future_date_rejected(self):
        clean, errors, _ = validate_record(good(date="2026-06-02"), CTX)
        self.assertIsNone(clean)
        self.assertIn("future", errors[0])

    def test_negative_and_non_numeric_quantities(self):
        for field, value in (("quantity_prepared", "-5"), ("quantity_consumed", "abc"),
                             ("quantity_prepared", "nan"), ("quantity_consumed", "inf"),
                             ("quantity_prepared", "1e12")):
            clean, errors, _ = validate_record(good(**{field: value}), CTX)
            self.assertIsNone(clean, value)
            self.assertTrue(errors)

    def test_consumed_more_than_prepared_rejected(self):
        clean, errors, _ = validate_record(good(quantity_consumed="120"), CTX)
        self.assertIsNone(clean)
        self.assertIn("greater than prepared", errors[0])

    def test_supplied_leftovers_conflict_is_flagged_not_overwritten(self):
        clean, errors, warnings = validate_record(good(leftovers="5"), CTX)
        self.assertEqual(errors, [])
        self.assertEqual(clean["mismatch"], 1)
        self.assertEqual(clean["supplied_leftovers"], 5)
        self.assertEqual(clean["leftovers"], 20)          # derived value is what totals use
        self.assertTrue(warnings)

    def test_matching_supplied_leftovers_has_no_flag(self):
        clean, _, warnings = validate_record(good(leftovers="20"), CTX)
        self.assertEqual(clean["mismatch"], 0)
        self.assertEqual(warnings, [])

    def test_redistributed_cannot_exceed_leftovers(self):
        clean, errors, _ = validate_record(good(redistributed="30"), CTX)
        self.assertIsNone(clean)
        clean, errors, _ = validate_record(good(redistributed="10"), CTX)
        self.assertEqual(clean["redistributed"], 10)

    def test_unknown_meal_and_unit(self):
        self.assertIsNone(validate_record(good(meal="Brunch"), CTX)[0])
        self.assertIsNone(validate_record(good(unit="buckets"), CTX)[0])

    def test_unit_synonyms_and_default(self):
        self.assertEqual(validate_record(good(unit="Portions"), CTX)[0]["unit"], "servings")
        self.assertEqual(validate_record(good(unit=""), CTX)[0]["unit"], "servings")

    def test_item_cannot_change_unit(self):
        ctx = ValidationContext(meals=CTX.meals, units=CTX.units, today=CTX.today,
                                item_units={"rice": "kg"}, item_names={"rice": "Rice"})
        clean, errors, _ = validate_record(good(unit="servings"), ctx)
        self.assertIsNone(clean)
        self.assertIn("already tracked in kg", errors[0])

    def test_student_count_must_be_whole(self):
        self.assertIsNone(validate_record(good(student_count="12.5"), CTX)[0])
        self.assertEqual(validate_record(good(student_count="300"), CTX)[0]["student_count"], 300)

    def test_item_name_cleaned_and_limited(self):
        clean, _, _ = validate_record(good(food_item="  Veg   Rice \x07"), CTX)
        self.assertEqual(clean["food_item"], "Veg Rice")
        self.assertIsNone(validate_record(good(food_item="x" * 80), CTX)[0])
        self.assertIsNone(validate_record(good(food_item="<script>alert(1)</script>"), CTX)[0])

    def test_notes_are_stripped_of_control_characters_and_truncated(self):
        clean, _, _ = validate_record(good(notes="a\x00b" + "z" * 500), CTX)
        self.assertNotIn("\x00", clean["notes"])
        self.assertLessEqual(len(clean["notes"]), 300)


class CsvAnalysis(unittest.TestCase):
    def run_csv(self, text, existing=None):
        return analyze_csv(text.encode("utf-8"), CTX, existing or set())

    def test_valid_file(self):
        r = self.run_csv(HEADER + "2026-05-20,Lunch,Rice,100,80,servings,500\n2026-05-21,Lunch,Rice,90,85,servings,\n")
        self.assertTrue(r["ok"])
        self.assertEqual(r["counts"]["total"], 2)
        self.assertEqual(r["counts"]["importable"], 2)
        self.assertEqual(r["counts"]["invalid"], 0)

    def test_missing_required_column_is_fatal_and_explained(self):
        r = self.run_csv("date,meal,food_item,quantity_prepared\n2026-05-20,Lunch,Rice,100\n")
        self.assertFalse(r["ok"])
        self.assertIn("quantity_consumed", r["fatal"])

    def test_mixed_valid_and_invalid_rows(self):
        r = self.run_csv(HEADER + "2026-05-20,Lunch,Rice,100,80,servings,\n"
                                  "not-a-date,Lunch,Rice,100,80,servings,\n"
                                  "2026-05-22,Lunch,Rice,abc,80,servings,\n"
                                  "2026-05-23,Lunch,Rice,50,60,servings,\n")
        self.assertTrue(r["ok"])
        self.assertEqual(r["counts"]["valid"], 1)
        self.assertEqual(r["counts"]["invalid"], 3)
        self.assertEqual({e["row"] for e in r["errors"]}, {3, 4, 5})
        self.assertEqual(len(r["importable"]), 1)

    def test_duplicates_inside_file_and_against_database(self):
        r = self.run_csv(HEADER + "2026-05-20,Lunch,Rice,100,80,servings,\n2026-05-20,Lunch,rice,100,70,servings,\n"
                                  "2026-05-19,Lunch,Rice,100,80,servings,\n",
                         existing={("2026-05-19", "Lunch", "rice")})
        self.assertEqual(r["counts"]["valid"], 1)
        self.assertEqual(r["counts"]["invalid"], 1)       # in-file duplicate
        self.assertEqual(r["counts"]["duplicate"], 1)     # already stored -> skipped, never replaced
        self.assertEqual(len(r["importable"]), 1)

    def test_headers_are_case_and_synonym_tolerant(self):
        r = self.run_csv("Date,Meal,Item,Prepared,Consumed\n2026-05-20,Lunch,Rice,100,80\n")
        self.assertTrue(r["ok"], r["fatal"])
        self.assertEqual(r["counts"]["valid"], 1)

    def test_unknown_columns_are_ignored_not_trusted(self):
        r = self.run_csv(HEADER.strip() + ",=cmd|calc\n2026-05-20,Lunch,Rice,100,80,servings,1,x\n")
        self.assertTrue(r["ok"])
        self.assertIn("=cmd|calc", r["columns"]["ignored"])

    def test_empty_binary_and_non_utf8_files(self):
        self.assertIn("empty", analyze_csv(b"", CTX, set())["fatal"].lower())
        self.assertIn("binary", analyze_csv(b"PK\x03\x04\x00\x00data", CTX, set())["fatal"].lower())
        self.assertIn("UTF-8", analyze_csv("date,meal\n\xe9".encode("latin-1"), CTX, set())["fatal"])

    def test_header_only_file(self):
        self.assertIn("no data rows", self.run_csv(HEADER)["fatal"])

    def test_oversized_file(self):
        big = (HEADER + "2026-05-20,Lunch,Rice,100,80,servings,\n" * 1).encode() + b" " * (3 * 1024 * 1024)
        self.assertIn("larger than", analyze_csv(big, CTX, set())["fatal"])

    def test_semicolon_delimiter_supported(self):
        r = self.run_csv("date;meal;food_item;quantity_prepared;quantity_consumed\n2026-05-20;Lunch;Rice;100;80\n")
        self.assertTrue(r["ok"], r["fatal"])

    def test_extra_values_in_row_reported(self):
        r = self.run_csv(HEADER + "2026-05-20,Lunch,Rice,1,2,servings,5,extra,more\n")
        self.assertEqual(r["counts"]["invalid"], 1)
        self.assertIn("quotes", r["errors"][0]["messages"][0])

    def test_same_item_two_units_in_one_file(self):
        r = self.run_csv(HEADER + "2026-05-20,Lunch,Rice,10,8,kg,\n2026-05-21,Lunch,Rice,100,80,servings,\n")
        self.assertEqual(r["counts"]["valid"], 1)
        self.assertEqual(r["counts"]["invalid"], 1)

    def test_sha256_is_stable(self):
        a = self.run_csv(HEADER + "2026-05-20,Lunch,Rice,100,80,servings,\n")
        b = self.run_csv(HEADER + "2026-05-20,Lunch,Rice,100,80,servings,\n")
        self.assertEqual(a["sha256"], b["sha256"])


if __name__ == "__main__":
    unittest.main()
