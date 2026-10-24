"""FreshRoute AI - cafeteria demand forecasting and food-waste reduction."""
from __future__ import annotations

import logging
import os
import secrets
from logging.handlers import RotatingFileHandler
from pathlib import Path

from flask import Flask, abort, render_template, request, session

from . import db as database
from .constants import APP_NAME, MAX_UPLOAD_BYTES, NAV_ITEMS, TAGLINE

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_secret(instance_path: Path) -> str:
    env = os.environ.get("FRESHROUTE_SECRET_KEY")
    if env:
        return env
    key_file = instance_path / "secret_key"
    if key_file.exists():
        return key_file.read_text().strip()
    key = secrets.token_hex(32)
    key_file.write_text(key)
    return key


def create_app(test_config: dict | None = None) -> Flask:
    instance_path = Path((test_config or {}).get("INSTANCE_PATH") or PROJECT_ROOT / "instance")
    instance_path.mkdir(parents=True, exist_ok=True)
    app = Flask(__name__, instance_path=str(instance_path), instance_relative_config=True)
    app.config.update(
        DATABASE=str(instance_path / "freshroute.sqlite3"),
        MODEL_DIR=str(instance_path / "models"),
        UPLOAD_DIR=str(instance_path / "uploads"),
        MAX_CONTENT_LENGTH=MAX_UPLOAD_BYTES + 64 * 1024,
        AUTO_SEED_DEMO=True,
        SECRET_KEY=_load_secret(instance_path),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        TESTING=False,
    )
    if test_config:
        app.config.update({k: v for k, v in test_config.items() if k != "INSTANCE_PATH"})
    Path(app.config["UPLOAD_DIR"]).mkdir(parents=True, exist_ok=True)

    _configure_logging(app, instance_path)
    database.init_db(app.config["DATABASE"])
    app.teardown_appcontext(database.close_db)

    from .routes import api, pages

    app.register_blueprint(pages.bp)
    app.register_blueprint(api.bp)
    _register_security(app)
    _register_errors(app)
    _register_cli(app)

    @app.context_processor
    def inject_globals():
        vendor = Path(app.static_folder) / "vendor" / "chart.umd.min.js"
        return {"APP_NAME": APP_NAME, "TAGLINE": TAGLINE, "NAV_ITEMS": NAV_ITEMS,
                "csrf_token": _csrf_token, "chartjs_available": vendor.exists()}

    if app.config["AUTO_SEED_DEMO"]:
        with app.app_context():
            pages.ensure_demo_on_first_run()
    return app


def _configure_logging(app: Flask, instance_path: Path) -> None:
    log_dir = instance_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(log_dir / "freshroute.log", maxBytes=512_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.setLevel(logging.INFO)
    root = logging.getLogger("freshroute")
    if not any(isinstance(h, RotatingFileHandler) and h.baseFilename == handler.baseFilename for h in root.handlers):
        root.addHandler(handler)
    root.setLevel(logging.INFO)
    app.logger.setLevel(logging.INFO)


def _csrf_token() -> str:
    token = session.get("_csrf")
    if not token:
        token = secrets.token_hex(16)
        session["_csrf"] = token
    return token


def _register_security(app: Flask) -> None:
    @app.before_request
    def csrf_protect():
        if request.method == "POST" and not app.config.get("TESTING_DISABLE_CSRF"):
            sent = request.form.get("_csrf") or request.headers.get("X-CSRF-Token")
            if not sent or sent != session.get("_csrf"):
                abort(400, description="Your session expired or the form was invalid. Please go back, reload the page and try again.")

    @app.after_request
    def headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; frame-ancestors 'none'; form-action 'self'",
        )
        return resp


def _register_errors(app: Flask) -> None:
    def render_error(code: int, title: str, message: str):
        return render_template("error.html", code=code, title=title, message=message), code

    @app.errorhandler(400)
    def bad_request(err):
        return render_error(400, "That request couldn't be processed", getattr(err, "description", "Bad request."))

    @app.errorhandler(404)
    def not_found(_err):
        return render_error(404, "Page not found", "The page you asked for doesn't exist. Use the menu to get back on track.")

    @app.errorhandler(413)
    def too_large(_err):
        return render_error(413, "File too large", f"Uploads are limited to {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")

    @app.errorhandler(500)
    def server_error(err):
        app.logger.exception("Unhandled error: %s", err)
        return render_error(500, "Something went wrong", "An unexpected error occurred. It has been logged; your data was not changed by this request.")


def _register_cli(app: Flask) -> None:
    import click

    @app.cli.command("init-db")
    def init_db_command():
        """Create the database tables (also happens automatically on start)."""
        database.init_db(app.config["DATABASE"])
        click.echo("Database ready.")

    @app.cli.command("seed-demo")
    def seed_demo_command():
        """Load the synthetic demo dataset (never replaces existing records)."""
        from .routes import pages

        result = pages.seed_demo_now()
        click.echo(f"Demo data: {result}")

    @app.cli.command("reset-demo")
    def reset_demo_command():
        """Delete and recreate only the synthetic demo records."""
        from .routes import pages

        click.echo(f"Demo reset: {pages.reset_demo_now()}")
