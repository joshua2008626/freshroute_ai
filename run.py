"""Start FreshRoute AI locally:  python run.py

The database is created automatically on first start and, if it is empty, the
synthetic demo dataset is loaded so the dashboard is useful immediately.
"""
import os

from freshroute import create_app

app = create_app()

if __name__ == "__main__":
    host = os.environ.get("FRESHROUTE_HOST", "127.0.0.1")   # local only by default; there is no login
    port = int(os.environ.get("PORT", "5000"))
    debug = os.environ.get("FRESHROUTE_DEBUG") == "1"
    print(f"FreshRoute AI running at http://{host}:{port}  (Ctrl+C to stop)")
    app.run(host=host, port=port, debug=debug)
