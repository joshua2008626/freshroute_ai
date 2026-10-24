"""Optional end-to-end browser check (needs `pip install playwright` and a Chromium build).

Usage:  python tests/e2e_browser.py http://127.0.0.1:5000 [path-to-chromium]
Walks the main demo flow in a real browser and fails on any console error.
"""
import sys
import tempfile
import urllib.request

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5000"
CHROMIUM = sys.argv[2] if len(sys.argv) > 2 else None


def step(msg):
    print("OK  ", msg)


with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=CHROMIUM) if CHROMIUM else p.chromium.launch()
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    problems = []
    page.on("console", lambda m: problems.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: problems.append(str(e)))

    # 1. dashboard with demo data and charts
    page.goto(BASE + "/dashboard")
    assert page.locator("svg, canvas").count() > 0, "no chart rendered"
    assert "Synthetic demo data is included" in page.content()
    step("dashboard shows demo banner and charts")

    # chart hover tooltip (SVG renderer)
    chart = page.locator('[data-chart="trend"] svg').first
    chart.scroll_into_view_if_needed()
    if chart.count():
        box = chart.bounding_box()
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        page.wait_for_timeout(200)
        assert page.locator('[data-chart="trend"] .chart-tip.on').count() == 1, "tooltip did not show"
        step("chart tooltip appears on hover")

    # Time Machine page renders both charts and the honesty notice
    page.goto(BASE + "/time-machine")
    assert page.locator('[data-chart="tm_frontier"] svg').count() == 1, "frontier chart missing"
    assert page.locator('[data-chart="tm_daily"] svg').count() == 1, "daily chart missing"
    assert "ranges" in page.content()
    step("time machine renders charts and notice")

    # 2. upload the downloadable sample CSV
    sample = tempfile.NamedTemporaryFile(suffix=".csv", delete=False)
    sample.write(urllib.request.urlopen(BASE + "/import/sample.csv").read())
    sample.close()
    page.goto(BASE + "/import")
    page.set_input_files('input[type=file]', sample.name)
    page.click("#upload-submit")
    page.wait_for_selector("text=Rows that will be imported")
    assert "valid new rows" in page.content()
    step("preview shows validation counts")
    page.click("text=/^Import \\d+ row/")
    page.wait_for_selector("text=Imported")
    assert "Showing your data on its own" in page.content()
    step("import confirmed, view switched to My data")

    # 3. a re-upload must not duplicate or overwrite
    page.goto(BASE + "/import")
    page.set_input_files('input[type=file]', sample.name)
    page.click("#upload-submit")
    page.wait_for_selector("text=already imported")
    step("re-uploading the same file is flagged as already imported")

    # 4. forecast for an uploaded item (small data -> baseline, clear limitation)
    page.goto(BASE + "/forecast?item=Lemon+Rice&meal=Lunch&date=2026-04-01")
    assert "Forecast method" in page.content()
    step("forecast page renders for uploaded item")

    # 5. record a meal by hand
    page.goto(BASE + "/records/new")
    page.fill("#food_item", "Lemon Rice")
    page.select_option("#meal", "Lunch")
    page.fill("#date", "2026-03-30")
    page.fill("#qp", "200")
    page.fill("#qc", "170")
    page.click("text=Save record")
    page.wait_for_selector("text=Waste: 15.0%")
    step("manual record saved with computed waste")

    # 6. delete confirmation is required
    page.goto(BASE + "/settings")
    page.select_option("#d-target", "manual")
    page.once("dialog", lambda d: d.accept())
    page.click("text=Delete selected data")
    page.wait_for_selector("text=Nothing was deleted")
    step("deletion refused without typing DELETE")

    browser.close()
    assert not problems, f"console errors: {problems}"
    print("All browser checks passed.")
