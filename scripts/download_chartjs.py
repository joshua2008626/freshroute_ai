"""Download Chart.js once so charts use it (optional).

    python scripts/download_chartjs.py

Without this file FreshRoute AI still draws every chart with its built-in SVG renderer,
so the app works fully offline either way. The file is saved to
freshroute/static/vendor/chart.umd.min.js and served locally (no CDN at runtime).
"""
import sys
import urllib.request
from pathlib import Path

VERSION = "4.4.7"
URL = f"https://cdn.jsdelivr.net/npm/chart.js@{VERSION}/dist/chart.umd.js"
TARGET = Path(__file__).resolve().parent.parent / "freshroute" / "static" / "vendor" / "chart.umd.min.js"


def main() -> int:
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(URL, timeout=30) as resp:
            data = resp.read()
    except Exception as exc:  # network errors vary widely
        print(f"Could not download Chart.js ({exc}).\nThe built-in SVG charts will be used instead - nothing is broken.")
        return 1
    if len(data) < 100_000 or b"Chart.js" not in data[:2000]:
        print("The downloaded file does not look like Chart.js; not saving it.")
        return 1
    TARGET.write_bytes(data)
    print(f"Saved Chart.js {VERSION} ({len(data) // 1024} KB) to {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
