"""Optional: checks that the Chart.js adapter in charts.js builds valid configs.

A stub `Chart` class is injected, so the real Chart.js library is not needed.
Usage: python tests/e2e_chartjs_adapter.py http://127.0.0.1:5000 [chromium-path]
"""
import sys

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5000"
CHROMIUM = sys.argv[2] if len(sys.argv) > 2 else None
STUB = """
window.__configs = [];
window.Chart = class { constructor(canvas, cfg) { window.__configs.push(cfg); } };
"""
with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=CHROMIUM) if CHROMIUM else p.chromium.launch()
    page = browser.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(STUB)
    page.goto(BASE + "/analytics")
    page.wait_for_timeout(500)
    cfgs = page.evaluate("window.__configs")
    assert len(cfgs) >= 6, f"expected several Chart.js charts, got {len(cfgs)}"
    for c in cfgs:
        assert c["type"] in ("line", "bar")
        assert c["data"]["labels"] and c["data"]["datasets"]
        assert all(len(d["data"]) == len(c["data"]["labels"]) for d in c["data"]["datasets"])
    assert any(c["options"]["indexAxis"] == "y" for c in cfgs), "horizontal bar chart missing"
    assert not errors, errors
    print(f"Chart.js adapter produced {len(cfgs)} valid chart configs.")
    browser.close()
