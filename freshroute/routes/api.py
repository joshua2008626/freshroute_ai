"""Small JSON API. The pages use the same services, so numbers always agree."""
from __future__ import annotations

from datetime import date

from flask import Blueprint, jsonify, request

from .. import analytics, services, validation
from .. import repository as repo
from ..db import get_db

bp = Blueprint("api", __name__, url_prefix="/api")


def _error(message: str, status: int = 400):
    return jsonify({"error": message}), status


@bp.route("/health")
def health():
    db = get_db()
    counts = repo.source_counts(db)
    return jsonify({"status": "ok", "records": counts, "database": "sqlite"})


@bp.route("/backtest")
def backtest_api():
    db = get_db()
    return jsonify(services.run_backtest(db, repo.get_settings(db), request.args))


@bp.route("/forecast")
def forecast():
    db = get_db()
    settings = repo.get_settings(db)
    item, meal = request.args.get("item", ""), request.args.get("meal", "")
    target = validation.parse_date(request.args.get("date"))
    if target is None:
        return _error("date must be YYYY-MM-DD")
    if meal not in settings["meals"]:
        return _error("meal must be one of: " + ", ".join(settings["meals"]))
    if repo.get_item_by_name(db, item) is None:
        return _error("unknown food item", 404)
    students = None
    if request.args.get("students"):
        try:
            students = int(request.args["students"])
        except ValueError:
            return _error("students must be a whole number")
        if students <= 0:
            return _error("students must be positive")
    result = services.forecast_and_recommend(db, settings, item, meal, target, students)
    return jsonify(result)


@bp.route("/evaluation")
def evaluation():
    db = get_db()
    settings = repo.get_settings(db)
    frame, unit, excluded, filters = services.load_records(db, settings, request.args)
    engine, _ = services.engine_for(db, settings, unit)
    return jsonify({"unit": unit, "evaluation": engine.evaluate()})


@bp.route("/summary")
def summary():
    db = get_db()
    settings = repo.get_settings(db)
    frame, unit, excluded, filters = services.load_records(db, settings, request.args)
    return jsonify({
        "unit": unit, "excluded_other_unit_rows": excluded, "filters": filters,
        "kpis": analytics.kpis(frame), "by_meal": analytics.by_meal(frame),
        "by_item": analytics.by_item(frame),
    })
