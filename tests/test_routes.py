import html as htmllib
import io
import re
import unittest

from freshroute import repository as repo
from tests.helpers import AppTestCase

HEADER = "date,meal,food_item,quantity_prepared,quantity_consumed,unit,student_count\n"
GOOD_CSV = HEADER + "2026-03-02,Lunch,Lemon Rice,260,231,servings,880\n2026-03-03,Lunch,Lemon Rice,250,240,servings,870\n"


def upload(client, content, name="data.csv"):
    return client.post("/import/preview", data={"file": (io.BytesIO(content), name)},
                       content_type="multipart/form-data")


class Pages(AppTestCase):
    seed_demo = True

    def test_every_page_renders_with_demo_data(self):
        urls = ["/", "/dashboard", "/import", "/forecast", "/plan", "/records", "/records/new", "/analytics",
                "/reports", "/settings", "/import/sample.csv", "/records/export.csv", "/reports/summary.csv",
                "/api/health", "/api/summary", "/api/evaluation"]
        for url in urls:
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, url)
            self.assertNotIn(b"Traceback", r.data, url)

    def test_internal_links_and_static_files_resolve(self):
        seen, bad = set(), []
        for page in ("/", "/dashboard", "/import", "/forecast", "/plan", "/records", "/analytics", "/reports", "/settings"):
            html = self.client.get(page).data.decode()
            for href in re.findall(r'(?:href|src|action)="(/[^"#?]*)', html):
                if href in seen:
                    continue
                seen.add(href)
                if href.endswith(("/delete",)) or "/edit" in href or href in ("/scope", "/import/preview", "/import/confirm",
                        "/plan/save", "/settings/general", "/settings/data/delete") or href.startswith("/settings/"):
                    continue          # POST-only or id-specific endpoints
                with self.client.get(href) as resp:
                    if resp.status_code != 200:
                        bad.append((page, href))
        self.assertEqual(bad, [])
        self.assertIn("/static/css/app.css", seen)
        self.assertIn("/static/js/charts.js", seen | {"/static/js/charts.js"})

    def test_demo_data_is_labelled_synthetic_everywhere_it_appears(self):
        for page in ("/dashboard", "/forecast?item=Dosa&meal=Dinner&date=2026-12-01", "/records", "/analytics"):
            html = self.client.get(page).data.decode()
            self.assertIn("Synthetic demo", html, page)
        self.assertIn("fictional", self.client.get("/dashboard").data.decode())

    def test_dashboard_numbers_come_from_the_database(self):
        data = self.client.get("/api/summary").get_json()
        frame = repo.fetch_records(self.db)
        frame = frame[frame["unit"] == data["unit"]]
        self.assertEqual(data["kpis"]["records"], len(frame))
        self.assertAlmostEqual(data["kpis"]["leftovers"], frame["leftovers"].sum(), places=1)
        self.assertEqual(data["excluded_other_unit_rows"], 90)    # the litres item is kept out of servings totals

    def test_forecast_page_shows_method_observations_and_history(self):
        html = self.client.get("/forecast?item=Sambar+Rice&meal=Lunch&date=2099-01-05").data.decode()
        self.assertIn("Forecast method", html)
        self.assertIn("past observation(s) used", html)
        self.assertIn("Relevant history", html)
        self.assertIn("not a guarantee", html)

    def test_forecast_api_is_computed_server_side(self):
        r = self.client.get("/api/forecast?item=Sambar+Rice&meal=Lunch&date=2099-01-05").get_json()
        fc, rc = r["forecast"], r["recommendation"]
        self.assertGreater(fc["estimate"], 0)
        self.assertGreaterEqual(rc["suggested"], fc["estimate"] * 1.0)
        self.assertIn(fc["method"], ("baseline_weekday_median", "baseline_recent_mean", "ml_random_forest"))
        self.assertGreater(fc["n_obs"], 30)

    def test_forecast_api_validation(self):
        self.assertEqual(self.client.get("/api/forecast?item=Dosa&meal=Dinner&date=bad").status_code, 400)
        self.assertEqual(self.client.get("/api/forecast?item=Dosa&meal=Brunch&date=2099-01-01").status_code, 400)
        self.assertEqual(self.client.get("/api/forecast?item=Nope&meal=Dinner&date=2099-01-01").status_code, 404)
        self.assertEqual(self.client.get("/api/forecast?item=Dosa&meal=Dinner&date=2099-01-01&students=-3").status_code, 400)

    def test_forecast_page_rejects_bad_input_without_crashing(self):
        html = self.client.get("/forecast?item=Dosa&meal=Dinner&date=nonsense").data.decode()
        self.assertIn("valid date", html)
        html = self.client.get("/forecast?item=%3Cscript%3E&meal=Dinner&date=2099-01-01").data.decode()
        self.assertNotIn("<script>", html.replace('<script src', '').replace('<script id', ''))

    def test_plan_save_records_final_quantity_and_override(self):
        page = self.client.get("/plan?date=2099-01-05&meal=Lunch").data.decode()
        self.assertIn("Save preparation plan", page)
        items = [htmllib.unescape(i) for i in re.findall(r'name="item" value="([^"]+)"', page)]
        self.assertTrue(items)
        data = {"date": "2099-01-05", "meal": "Lunch", "item": items, "final": ["111"] + ["50"] * (len(items) - 1),
                "note": [""] * len(items)}
        r = self.client.post("/plan/save", data=data, follow_redirects=True)
        self.assertIn(b"Saved the preparation plan", r.data)
        plans = repo.list_plans(self.db, "2099-01-05")
        self.assertEqual(len(plans), len(items))
        first = [p for p in plans if p["food_item"] == items[0]][0]
        self.assertEqual(first["final_qty"], 111)
        self.assertEqual(first["override"], 1)
        self.assertIsNotNone(first["forecast_qty"])

    def test_plan_save_rejects_invalid_quantities(self):
        r = self.client.post("/plan/save", data={"date": "2099-01-05", "meal": "Lunch", "item": ["Sambar Rice"],
                                                  "final": ["-5"], "note": [""]}, follow_redirects=True)
        self.assertIn(b"must be between", r.data)
        self.assertEqual(repo.list_plans(self.db), [])

    def test_plan_below_minimum_warns_but_saves(self):
        item = repo.get_item_by_name(self.db, "Sambar Rice")
        repo.update_item(self.db, item["id"], cost_per_unit=None, min_prep=200)
        r = self.client.post("/plan/save", data={"date": "2099-01-05", "meal": "Lunch", "item": ["Sambar Rice"],
                                                  "final": ["100"], "note": [""]}, follow_redirects=True)
        self.assertIn(b"Below your configured minimum", r.data)
        self.assertEqual(repo.list_plans(self.db)[0]["final_qty"], 100)

    def test_record_actual_then_analytics_change(self):
        before = self.client.get("/api/summary?meal=Dinner").get_json()["kpis"]["records"]
        r = self.client.post("/records/new", data={"date": "2026-10-01", "meal": "Lunch", "food_item": "Brand New Dish",
                                                     "unit": "servings", "quantity_prepared": "100", "quantity_consumed": "70"},
                             follow_redirects=True)
        self.assertIn(b"Saved Brand New Dish", r.data)
        self.assertIn(b"Waste: 30.0%", r.data)
        after = self.client.get("/api/summary").get_json()
        self.assertTrue(any(i["food_item"] == "Brand New Dish" for i in after["by_item"]))
        self.assertGreater(after["kpis"]["records"], before)


class Records(AppTestCase):
    def test_create_edit_delete(self):
        form = {"date": "2026-05-01", "meal": "Lunch", "food_item": "Rice", "unit": "servings",
                "quantity_prepared": "100", "quantity_consumed": "80", "redistributed": "5", "notes": "x"}
        self.assertIn(b"Saved Rice", self.client.post("/records/new", data=form, follow_redirects=True).data)
        rid = int(repo.fetch_records(self.db).iloc[0]["id"])
        form["quantity_consumed"] = "90"
        self.assertIn(b"Record updated", self.client.post(f"/records/{rid}/edit", data=form, follow_redirects=True).data)
        self.assertEqual(repo.get_record(self.db, rid)["leftovers"], 10)
        self.assertIn(b"Record deleted", self.client.post(f"/records/{rid}/delete", follow_redirects=True).data)
        self.assertTrue(repo.fetch_records(self.db).empty)

    def test_duplicate_manual_entry_is_blocked(self):
        form = {"date": "2026-05-01", "meal": "Lunch", "food_item": "Rice", "unit": "servings",
                "quantity_prepared": "100", "quantity_consumed": "80"}
        self.client.post("/records/new", data=form)
        r = self.client.post("/records/new", data=dict(form, quantity_consumed="10"))
        self.assertIn(b"already exists", r.data)
        self.assertEqual(len(repo.fetch_records(self.db)), 1)
        self.assertEqual(repo.fetch_records(self.db).iloc[0]["consumed"], 80)

    def test_invalid_manual_entry_shows_plain_language_errors(self):
        r = self.client.post("/records/new", data={"date": "2099-01-01", "meal": "Lunch", "food_item": "Rice",
                                                     "unit": "servings", "quantity_prepared": "5", "quantity_consumed": "9"})
        self.assertIn(b"future", r.data)
        self.assertIn(b"greater than prepared", r.data)
        self.assertTrue(repo.fetch_records(self.db).empty)

    def test_leftover_mismatch_is_flagged_in_the_list(self):
        self.client.post("/records/new", data={"date": "2026-05-01", "meal": "Lunch", "food_item": "Rice", "unit": "servings",
                                                 "quantity_prepared": "100", "quantity_consumed": "80", "leftovers": "5"})
        html = self.client.get("/records").data.decode()
        self.assertIn("check", html)
        self.assertEqual(repo.fetch_records(self.db).iloc[0]["mismatch"], 1)

    def test_search_filter_and_export_respect_filters(self):
        self.add("2026-05-01", "Lunch", "Rice", 100, 80)
        self.add("2026-05-02", "Dinner", "Dosa", 50, 40)
        html = self.client.get("/records?q=dosa").data.decode()
        self.assertIn("<b>1</b> record(s)", html)
        self.assertIn("<td>Dosa<", html)
        self.assertNotIn("<td>Rice<", html)
        csv_text = self.client.get("/records/export.csv?meal=Dinner").data.decode()
        self.assertIn("Dosa", csv_text)
        self.assertNotIn("Rice", csv_text)

    def test_unknown_record_is_404(self):
        self.assertEqual(self.client.get("/records/999/edit").status_code, 404)

    def test_zero_prepared_does_not_crash(self):
        self.client.post("/records/new", data={"date": "2026-05-01", "meal": "Lunch", "food_item": "Rice", "unit": "servings",
                                                 "quantity_prepared": "0", "quantity_consumed": "0"})
        self.assertEqual(self.client.get("/records").status_code, 200)
        self.assertEqual(self.client.get("/dashboard").status_code, 200)


class Import(AppTestCase):
    def test_full_preview_confirm_flow_and_scope_switch(self):
        self.add("2026-01-01", "Lunch", "Demo Dish", 10, 8, source="demo")
        r = upload(self.client, GOOD_CSV.encode())
        self.assertIn(b"Rows that will be imported", r.data)
        token = re.search(rb'name="token" value="([^"]+)"', r.data).group(1).decode()
        self.assertEqual(len(repo.fetch_records(self.db, scope="mine")), 0)         # nothing stored by preview
        r = self.client.post("/import/confirm", data={"token": token, "filename": "data.csv"}, follow_redirects=True)
        self.assertIn(b"Imported 2 record(s)", r.data)
        mine = repo.fetch_records(self.db, scope="mine")
        self.assertEqual(len(mine), 2)
        self.assertEqual(set(mine["source"]), {"upload"})
        self.assertEqual(repo.get_settings(self.db)["active_scope"], "mine")
        self.assertEqual(len(repo.list_imports(self.db)), 1)

    def test_reuploading_does_not_duplicate_or_overwrite(self):
        r = upload(self.client, GOOD_CSV.encode())
        token = re.search(rb'name="token" value="([^"]+)"', r.data).group(1).decode()
        self.client.post("/import/confirm", data={"token": token, "filename": "data.csv"})
        r = upload(self.client, GOOD_CSV.encode())
        self.assertIn(b"already imported", r.data)
        self.assertIn(b"already stored", r.data)
        token = re.search(rb'name="token" value="([^"]+)"', r.data).group(1).decode()
        r = self.client.post("/import/confirm", data={"token": token, "filename": "data.csv"}, follow_redirects=True)
        self.assertIn(b"Nothing could be imported", r.data)
        self.assertEqual(len(repo.fetch_records(self.db)), 2)

    def test_invalid_rows_are_skipped_and_explained(self):
        bad = HEADER + "2026-03-02,Lunch,Rice,100,80,servings,\nbad,Lunch,Rice,1,1,servings,\n"
        r = upload(self.client, bad.encode())
        self.assertIn(b"invalid row(s)", r.data)
        self.assertIn(b"Row 3", r.data)
        token = re.search(rb'name="token" value="([^"]+)"', r.data).group(1).decode()
        r = self.client.post("/import/confirm", data={"token": token, "filename": "x.csv"}, follow_redirects=True)
        self.assertIn(b"Skipped 1 invalid", r.data)
        self.assertEqual(len(repo.fetch_records(self.db)), 1)

    def test_rejects_wrong_extension_and_missing_columns(self):
        r = upload(self.client, GOOD_CSV.encode(), name="data.xlsx")
        self.assertEqual(r.status_code, 302)
        r = self.client.post("/import/preview", data={"file": (io.BytesIO(GOOD_CSV.encode()), "data.exe.txt")},
                             content_type="multipart/form-data", follow_redirects=True)
        self.assertIn(b"Only .csv files", r.data)
        r = upload(self.client, b"foo,bar\n1,2\n")
        self.assertIn(b"Required column(s) missing", r.data)
        self.assertEqual(repo.source_counts(self.db), {"demo": 0, "upload": 0, "manual": 0})

    def test_no_file_chosen(self):
        r = self.client.post("/import/preview", data={}, content_type="multipart/form-data", follow_redirects=True)
        self.assertIn(b"Choose a CSV file", r.data)

    def test_oversized_upload_rejected_with_friendly_page(self):
        r = upload(self.client, b"x" * (3 * 1024 * 1024))
        self.assertEqual(r.status_code, 413)
        self.assertIn(b"File too large", r.data)

    def test_filename_is_sanitised_and_path_traversal_is_harmless(self):
        r = upload(self.client, GOOD_CSV.encode(), name="../../etc/passwd.csv")
        self.assertIn(b"etc_passwd.csv", r.data)
        import os
        saved = os.listdir(self.app.config["UPLOAD_DIR"])
        self.assertTrue(all(re.fullmatch(r"[A-Za-z0-9_-]+\.csv", f) for f in saved))

    def test_confirm_rejects_forged_token(self):
        for token in ("../../etc/passwd", "short", "a" * 30):
            r = self.client.post("/import/confirm", data={"token": token, "filename": "x.csv"}, follow_redirects=True)
            self.assertIn(r.status_code, (200, 400))
            self.assertEqual(repo.fetch_records(self.db).shape[0], 0)

    def test_sample_csv_is_valid_and_importable(self):
        sample = self.client.get("/import/sample.csv").data
        r = upload(self.client, sample)
        self.assertIn(b"valid new rows", r.data)
        self.assertNotIn(b"invalid row(s)", r.data)


class SettingsAndData(AppTestCase):
    seed_demo = True

    def settings_form(self, **over):
        s = repo.get_settings(self.db)
        form = {"cafeteria_name": s["cafeteria_name"], "meals": "\n".join(s["meals"]), "units": "\n".join(s["units"]),
                "default_unit": s["default_unit"], "safety_buffer_pct": "5", "min_prep_default": "0",
                "max_reduction_pct": "25", "acceptable_waste_pct": "5", "high_waste_threshold_pct": "15"}
        form.update(over)
        return form

    def test_save_valid_settings(self):
        r = self.client.post("/settings/general", data=self.settings_form(cafeteria_name="Hall <b>9</b>", safety_buffer_pct="8"),
                             follow_redirects=True)
        self.assertIn(b"Settings saved", r.data)
        self.assertEqual(repo.get_settings(self.db)["safety_buffer_pct"], 8)
        self.assertIn(b"Hall &lt;b&gt;9&lt;/b&gt;", self.client.get("/settings").data)     # user text is escaped

    def test_invalid_settings_are_rejected(self):
        for over, text in (({"safety_buffer_pct": "999"}, b"between 0 and 50"), ({"cafeteria_name": " "}, b"cannot be empty"),
                           ({"acceptable_waste_pct": "20", "high_waste_threshold_pct": "10"}, b"must be above"),
                           ({"meals": "Lunch"}, b"cannot be removed"), ({"units": "kg"}, b"cannot be removed"),
                           ({"max_reduction_pct": "abc"}, b"must be a number")):
            r = self.client.post("/settings/general", data=self.settings_form(**over), follow_redirects=True)
            self.assertIn(text, r.data, over)

    def test_item_cost_and_minimum(self):
        item = repo.get_item_by_name(self.db, "Dosa")
        r = self.client.post(f"/settings/items/{item['id']}", data={"cost_per_unit": "12.5", "min_prep": "40"}, follow_redirects=True)
        self.assertIn(b"Updated Dosa", r.data)
        item = repo.get_item_by_name(self.db, "Dosa")
        self.assertEqual((item["cost_per_unit"], item["min_prep"]), (12.5, 40))
        r = self.client.post(f"/settings/items/{item['id']}", data={"cost_per_unit": "-1", "min_prep": ""}, follow_redirects=True)
        self.assertIn(b"between 0", r.data)

    def test_delete_requires_typed_confirmation(self):
        before = repo.source_counts(self.db)["demo"]
        r = self.client.post("/settings/data/delete", data={"target": "demo", "confirm": "yes"}, follow_redirects=True)
        self.assertIn(b"Nothing was deleted", r.data)
        self.assertEqual(repo.source_counts(self.db)["demo"], before)
        r = self.client.post("/settings/data/delete", data={"target": "demo", "confirm": "DELETE"}, follow_redirects=True)
        self.assertIn(b"Deleted", r.data)
        self.assertEqual(repo.source_counts(self.db)["demo"], 0)

    def test_delete_demo_keeps_user_records(self):
        self.add("2026-05-01", "Lunch", "Mine", 10, 8, source="manual")
        self.client.post("/settings/data/delete", data={"target": "demo", "confirm": "DELETE"})
        self.assertEqual(len(repo.fetch_records(self.db)), 1)

    def test_delete_everything_and_unknown_target(self):
        self.client.post("/settings/data/delete", data={"target": "all", "confirm": "DELETE"})
        self.assertEqual(sum(repo.source_counts(self.db).values()), 0)
        self.assertEqual(self.client.post("/settings/data/delete", data={"target": "weird", "confirm": "DELETE"}).status_code, 400)
        self.assertEqual(self.client.get("/dashboard").status_code, 200)         # empty state still renders

    def test_reset_demo_keeps_user_data(self):
        self.add("2026-05-01", "Lunch", "Mine", 10, 8, source="manual")
        r = self.client.post("/settings/demo/reset", follow_redirects=True)
        self.assertIn(b"Your own records were not touched", r.data)
        self.assertEqual(len(repo.fetch_records(self.db, scope="mine")), 1)
        self.assertGreater(repo.source_counts(self.db)["demo"], 500)

    def test_scope_switch_and_safe_redirect(self):
        r = self.client.post("/scope", data={"scope": "mine", "next": "/analytics"})
        self.assertEqual(r.headers["Location"].split("?")[0].endswith("/analytics"), True)
        self.assertEqual(repo.get_settings(self.db)["active_scope"], "mine")
        r = self.client.post("/scope", data={"scope": "all", "next": "//evil.example/x"})
        self.assertNotIn("evil.example", r.headers["Location"])
        self.assertEqual(self.client.post("/scope", data={"scope": "bogus"}).status_code, 400)

    def test_demo_and_user_data_are_not_mixed_when_scope_is_mine(self):
        self.add("2026-05-01", "Lunch", "Mine", 10, 8, source="manual")
        self.client.post("/scope", data={"scope": "mine"})
        data = self.client.get("/api/summary").get_json()
        self.assertEqual(data["kpis"]["records"], 1)
        self.assertNotIn(b"Synthetic demo data is included", self.client.get("/dashboard").data)


class Reports(AppTestCase):
    seed_demo = True

    def test_report_page_has_coverage_labels_and_evaluation(self):
        html = self.client.get("/reports?start=2026-09-01&end=2026-09-30").data.decode()
        self.assertIn("Actual measurements", html)
        self.assertIn("Held-out estimate", html)
        self.assertIn("synthetic demonstration data", html.lower())
        self.assertIn("2026-09-01 to 2026-09-30", html)

    def test_report_period_filters_data(self):
        a = self.client.get("/api/summary?start=2026-09-01&end=2026-09-07").get_json()["kpis"]
        b = self.client.get("/api/summary").get_json()["kpis"]
        self.assertLess(a["records"], b["records"])
        self.assertLessEqual(a["days"], 7)

    def test_pdf_and_csv_downloads(self):
        pdf = self.client.get("/reports/report.pdf")
        self.assertEqual(pdf.mimetype, "application/pdf")
        self.assertTrue(pdf.data.startswith(b"%PDF"))
        self.assertIn("attachment", pdf.headers["Content-Disposition"])
        csv_r = self.client.get("/reports/summary.csv")
        self.assertIn(b"Forecast evaluation", csv_r.data)

    def test_empty_period_shows_empty_state(self):
        html = self.client.get("/reports?start=2001-01-01&end=2001-01-02").data.decode()
        self.assertIn("No records in this period", html)
        self.assertEqual(self.client.get("/reports/report.pdf?start=2001-01-01&end=2001-01-02").status_code, 200)


class Security(AppTestCase):
    csrf = True

    def test_post_without_csrf_token_is_rejected(self):
        r = self.client.post("/records/new", data={"date": "2026-05-01"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.client.post("/settings/data/delete", data={"target": "all", "confirm": "DELETE"}).status_code, 400)

    def test_post_with_valid_token_works(self):
        html = self.client.get("/records/new").data.decode()
        token = re.search(r'name="_csrf" value="([^"]+)"', html).group(1)
        r = self.client.post("/records/new", data={"_csrf": token, "date": "2026-05-01", "meal": "Lunch", "food_item": "Rice",
                                                     "unit": "servings", "quantity_prepared": "10", "quantity_consumed": "5"})
        self.assertEqual(r.status_code, 302)

    def test_security_headers(self):
        r = self.client.get("/dashboard")
        self.assertEqual(r.headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("script-src 'self'", r.headers["Content-Security-Policy"])
        self.assertEqual(r.headers["X-Frame-Options"], "DENY")

    def test_errors_do_not_leak_tracebacks(self):
        r = self.client.get("/definitely/not/here")
        self.assertEqual(r.status_code, 404)
        self.assertNotIn(b"Traceback", r.data)
        self.assertIn(b"Page not found", r.data)

    def test_no_inline_scripts_so_csp_holds(self):
        for page in ("/", "/dashboard", "/analytics", "/settings"):
            html = self.client.get(page).data.decode()
            for tag in re.findall(r"<script\b[^>]*>", html):
                self.assertTrue("src=" in tag or "application/json" in tag, (page, tag))
            self.assertNotRegex(html, r"\son(click|change|submit|load)=")


class Landing(AppTestCase):
    def test_landing_has_required_sections_and_no_false_claims(self):
        html = self.client.get("/").data.decode()
        for needle in ("Smarter Meals. Less Waste.", "Upload data", "Forecast demand", "Reduce waste",
                       "Demand forecasting", "Preparation recommendations", "Waste tracking", "Reports",
                       "Open dashboard", "Illustrative example"):
            self.assertIn(needle, html)
        self.assertIn("No customers, savings or environmental impact are claimed", html)
        self.assertEqual(self.client.get("/dashboard").status_code, 200)

    def test_empty_database_pages_show_empty_states(self):
        for page, text in (("/dashboard", "No records for these filters yet"), ("/analytics", "Nothing to analyse yet"),
                           ("/records", "No records match"), ("/plan", "No history for"), ("/reports", "No records in this period")):
            self.assertIn(text, self.client.get(page).data.decode(), page)


if __name__ == "__main__":
    unittest.main()
