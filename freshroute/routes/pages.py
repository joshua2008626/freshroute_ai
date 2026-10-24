"""HTML pages."""
from __future__ import annotations

import logging
import math
import re
import secrets
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
from flask import (Blueprint, Response, abort, current_app, flash, redirect, render_template,
                   request, url_for)
from werkzeug.utils import secure_filename

from .. import analytics, demo, reports, services, validation
from .. import repository as repo
from ..constants import (ALL_COLUMNS, MAX_ITEM_NAME_LEN, MAX_QUANTITY, OPTIONAL_COLUMNS,
                         REQUIRED_COLUMNS, SCOPE_LABELS, SCOPES, SOURCE_LABELS)
from ..db import get_db

LOG = logging.getLogger("freshroute.pages")
bp = Blueprint("pages", __name__)

PAGE_SIZE = 40
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{20,64}$")
MEAL_NAME_RE = re.compile(r"^[\w][\w &'’()-]{0,29}$", re.UNICODE)
UNIT_NAME_RE = re.compile(r"^[a-z][a-z ]{0,19}$")


# ------------------------------------------------------------------ helpers

def _settings() -> dict:
    return repo.get_settings(get_db())


def seed_demo_now() -> dict:
    return demo.seed_demo(get_db(), repo.build_context, validation.validate_record)


def reset_demo_now() -> dict:
    services.forecasting.clear_cache()
    return demo.reset_demo(get_db(), repo.build_context, validation.validate_record)


def ensure_demo_on_first_run() -> None:
    db = get_db()
    settings = repo.get_settings(db)
    if not settings.get("demo_seeded") and sum(repo.source_counts(db).values()) == 0:
        LOG.info("First run: loading synthetic demo data")
        seed_demo_now()


def _safe_next(value: str | None, fallback: str) -> str:
    if value and value.startswith("/") and not value.startswith("//") and "\\" not in value:
        return value
    return fallback


def _parse_float(raw, label: str, lo: float, hi: float, errors: list):
    text = (raw or "").strip()
    try:
        number = float(text)
    except ValueError:
        errors.append(f"{label} must be a number.")
        return None
    if math.isnan(number) or math.isinf(number) or number < lo or number > hi:
        errors.append(f"{label} must be between {lo:g} and {hi:g}.")
        return None
    return number


def _optional_float(raw, label: str, lo: float, hi: float, errors: list):
    if raw is None or raw.strip() == "":
        return None
    return _parse_float(raw, label, lo, hi, errors)


@bp.app_template_filter("num")
def num_filter(value, digits: int = 1):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "-"
    if abs(value - round(value)) < 1e-9:
        return f"{int(round(value)):,}"
    return f"{value:,.{digits}f}"


@bp.app_template_filter("pct")
def pct_filter(value):
    return "-" if value is None else f"{value:.1f}%"


@bp.app_template_filter("longdate")
def longdate_filter(value):
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").strftime("%a, %d %b %Y")
    except ValueError:
        return value


@bp.app_context_processor
def inject_site():
    db = get_db()
    settings = repo.get_settings(db)
    counts = repo.source_counts(db)
    scope = services.clean_scope(None, settings)
    demo_in_scope = counts["demo"] > 0 and scope in ("all", "demo")
    return {
        "settings": settings, "scope": scope, "scope_labels": SCOPE_LABELS, "scopes": SCOPES,
        "source_counts": counts, "demo_in_scope": demo_in_scope,
        "total_records": sum(counts.values()), "source_labels": SOURCE_LABELS,
        "today": date.today().isoformat(),
    }


def _redirect_back(default: str):
    return redirect(_safe_next(request.form.get("next"), default))


# --------------------------------------------------------------------- pages

@bp.route("/")
def landing():
    return render_template("landing.html")


@bp.route("/dashboard")
def dashboard():
    db = get_db()
    settings = repo.get_settings(db)
    frame, unit, excluded, filters = services.load_records(db, settings, request.args)
    kpis = analytics.kpis(frame)
    engine, _ = services.engine_for(db, settings, unit)
    evaluation = engine.evaluate()
    target, meal = services.next_service(db, settings)
    if filters["meal"]:
        meal = filters["meal"]
    nxt = services.next_meal_forecasts(db, settings, target, meal, unit)
    latest_date = frame["date"].max() if not frame.empty else None
    latest = frame[frame["date"] == latest_date] if latest_date else frame
    live = services.logged_forecast_accuracy(db, settings)
    trend = analytics.trend_by_day(frame)
    # 7-day moving average of consumption for the demand-trend chart
    if trend:
        cons = pd.Series([t["consumed"] for t in trend])
        ma = cons.rolling(7, min_periods=3).mean()
        for t, m in zip(trend, ma):
            t["ma7"] = None if pd.isna(m) else round(float(m), 1)
    top_recs = sorted(
        [r["recommendation"] for r in nxt["rows"] if r["recommendation"]["suggested"] is not None],
        key=lambda r: -abs(r["difference"] or 0))[:5]
    charts = {
        "trend": trend,
        "waste_item": analytics.by_item(frame)[:10],
        "meals": analytics.by_meal(frame),
        "pva": evaluation["series"],
        "unit": unit,
    }
    categories = sorted(repo.fetch_records(db, scope=filters["scope"])["category"].unique().tolist())
    return render_template(
        "dashboard.html", frame_empty=frame.empty, kpis=kpis, unit=unit, excluded=excluded,
        filters=filters, nxt=nxt, latest_date=latest_date,
        latest_actual=float(latest["consumed"].sum()) if latest_date else None,
        evaluation=evaluation, live=live, charts=charts, top_recs=top_recs,
        meal_summary=analytics.meal_summary(frame), change=services.waste_change(frame),
        activity=repo.recent_activity(db, 8), categories=categories,
        insights=analytics.insights(frame, settings, services.item_costs(db))[:4],
        sources_in_view=sorted(frame["source"].unique().tolist()) if not frame.empty else [],
    )


# ------------------------------------------------------------------- import

def _cleanup_old_uploads(directory: Path) -> None:
    cutoff = time.time() - 24 * 3600
    for f in directory.glob("*.csv"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass


@bp.route("/import")
def import_page():
    db = get_db()
    return render_template("import.html", imports=repo.list_imports(db), preview=None,
                           required=REQUIRED_COLUMNS, optional=OPTIONAL_COLUMNS)


@bp.route("/import/sample.csv")
def sample_csv():
    rows = demo.generate_rows(date(2026, 3, 14), days=10, seed=7)
    keep = {"Idli (2 pcs)", "Sambar Rice", "Veg Samosa", "Dosa"}
    rows = [r for r in rows if r["food_item"] in keep]
    # Rename so the sample can be imported without mixing with the synthetic demo items.
    rename = {"Idli (2 pcs)": "Mini Idli", "Sambar Rice": "Lemon Rice", "Veg Samosa": "Onion Pakoda", "Dosa": "Masala Dosa"}
    lines = ["date,meal,food_item,quantity_prepared,quantity_consumed,unit,student_count,leftovers,notes"]
    for r in rows:
        prepared, consumed = float(r["quantity_prepared"]), float(r["quantity_consumed"])
        lines.append(",".join([r["date"], r["meal"], rename[r["food_item"]], r["quantity_prepared"],
                               r["quantity_consumed"], r["unit"], r["student_count"],
                               f"{prepared - consumed:g}", "sample row"]))
    return Response("\n".join(lines) + "\n", mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=freshroute_sample.csv"})


@bp.route("/import/preview", methods=["POST"])
def import_preview():
    db = get_db()
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        flash("Choose a CSV file first.", "error")
        return redirect(url_for("pages.import_page"))
    filename = secure_filename(upload.filename) or "upload.csv"
    if not filename.lower().endswith(".csv"):
        flash("Only .csv files are accepted. In Excel use 'Save As > CSV UTF-8'.", "error")
        return redirect(url_for("pages.import_page"))
    content = upload.read(current_app.config["MAX_CONTENT_LENGTH"] + 1)
    ctx = repo.build_context(db)
    result = validation.analyze_csv(content, ctx, repo.existing_keys(db))
    token = None
    previous = None
    if result["ok"]:
        directory = Path(current_app.config["UPLOAD_DIR"])
        _cleanup_old_uploads(directory)
        token = secrets.token_urlsafe(24)
        (directory / f"{token}.csv").write_bytes(content)
        previous = repo.find_import_by_hash(db, result["sha256"])
    LOG.info("CSV preview %s: ok=%s counts=%s", filename, result["ok"], result["counts"])
    return render_template("import.html", imports=repo.list_imports(db), preview=result, token=token,
                           filename=filename, previous=previous, required=REQUIRED_COLUMNS,
                           optional=OPTIONAL_COLUMNS)


@bp.route("/import/confirm", methods=["POST"])
def import_confirm():
    db = get_db()
    token = request.form.get("token", "")
    filename = secure_filename(request.form.get("filename", "")) or "upload.csv"
    if not TOKEN_RE.match(token):
        abort(400, description="The upload reference is invalid. Please upload the file again.")
    path = Path(current_app.config["UPLOAD_DIR"]) / f"{token}.csv"
    if not path.exists():
        flash("That upload has expired. Please choose the file again.", "error")
        return redirect(url_for("pages.import_page"))
    content = path.read_bytes()
    # Re-validate on the server: the preview is never trusted.
    result = validation.analyze_csv(content, repo.build_context(db), repo.existing_keys(db))
    if not result["ok"] or not result["importable"]:
        flash("Nothing could be imported - no valid new rows.", "error")
        return redirect(url_for("pages.import_page"))
    had_user_data = (lambda c: c["upload"] + c["manual"])(repo.source_counts(db)) > 0
    try:
        import_id = repo.log_import(
            db, filename=filename, sha=result["sha256"], total=result["counts"]["total"],
            valid=result["counts"]["valid"] + result["counts"]["warning"],
            invalid=result["counts"]["invalid"], duplicate=result["counts"]["duplicate"],
            imported=len(result["importable"]))
        for clean in result["importable"]:
            repo.insert_record(db, clean, "upload", import_id)
        db.commit()
    except sqlite3.Error:
        db.rollback()
        LOG.exception("Import failed")
        flash("The import failed and no records were saved. Please check the file and try again.", "error")
        return redirect(url_for("pages.import_page"))
    finally:
        try:
            path.unlink()
        except OSError:
            pass
    services.forecasting.clear_cache()
    msg = f"Imported {len(result['importable'])} record(s) from {filename}."
    if result["counts"]["invalid"] or result["counts"]["duplicate"]:
        msg += f" Skipped {result['counts']['invalid']} invalid and {result['counts']['duplicate']} duplicate row(s); nothing was overwritten."
    flash(msg, "success")
    settings = repo.get_settings(db)
    if not had_user_data and settings.get("active_scope") == "all" and repo.source_counts(db)["demo"] > 0:
        repo.save_settings(db, {"active_scope": "mine"})
        flash("Showing your data on its own. Use the data-source switcher in the top bar to include the synthetic demo data again.", "info")
    return redirect(url_for("pages.dashboard"))


# ----------------------------------------------------------------- forecast

def _parse_target(raw: str | None):
    d = validation.parse_date(raw) if raw else None
    return d


@bp.route("/forecast")
def forecast_page():
    db = get_db()
    settings = repo.get_settings(db)
    items = repo.list_items(db)
    target_default = (date.today()).isoformat()
    form = {"item": request.args.get("item", ""), "meal": request.args.get("meal", settings["meals"][0]),
            "date": request.args.get("date", target_default), "students": request.args.get("students", "")}
    result, error, actual = None, None, None
    if request.args.get("item"):
        target = _parse_target(form["date"])
        students = None
        if form["students"].strip():
            try:
                students = int(form["students"])
                if students <= 0 or students > 1_000_000:
                    raise ValueError
            except ValueError:
                error = "Expected students must be a positive whole number."
        if target is None:
            error = "Choose a valid date (YYYY-MM-DD)."
        elif form["meal"] not in settings["meals"]:
            error = "Choose one of your configured meals."
        elif repo.get_item_by_name(db, form["item"]) is None:
            error = "Choose a food item from the list."
        if not error:
            result = services.forecast_and_recommend(db, settings, form["item"], form["meal"], target, students)
            rec = repo.fetch_records(db, scope="all", start=target.isoformat(), end=target.isoformat(),
                                     meal=form["meal"], item=form["item"])
            if not rec.empty:
                actual = rec.iloc[0].to_dict()
    return render_template("forecast.html", items=items, form=form, result=result, error=error, actual=actual)


# --------------------------------------------------------------------- plan

@bp.route("/plan")
def plan_page():
    db = get_db()
    settings = repo.get_settings(db)
    nxt_date, nxt_meal = services.next_service(db, settings)
    plan_date = _parse_target(request.args.get("date")) or nxt_date
    meal = request.args.get("meal") if request.args.get("meal") in settings["meals"] else nxt_meal
    rows = []
    for name in services.items_for_meal(db, settings, meal, plan_date):
        rows.append(services.forecast_and_recommend(db, settings, name, meal, plan_date, log=False))
    saved = {(p["food_item"]): p for p in repo.list_plans(db, plan_date.isoformat()) if p["meal"] == meal}
    return render_template("plan.html", rows=rows, plan_date=plan_date.isoformat(), meal=meal,
                           saved=saved, plans=repo.list_plans(db, None, 25),
                           weekday=plan_date.strftime("%A"))


@bp.route("/plan/save", methods=["POST"])
def plan_save():
    db = get_db()
    settings = repo.get_settings(db)
    plan_date = _parse_target(request.form.get("date"))
    meal = request.form.get("meal", "")
    if plan_date is None or meal not in settings["meals"]:
        abort(400, description="Invalid plan date or meal.")
    names = request.form.getlist("item")
    finals = request.form.getlist("final")
    notes = request.form.getlist("note")
    if not names or len(names) != len(finals) or len(names) > 100:
        abort(400, description="The plan form was incomplete.")
    saved, below_min, errors = 0, [], []
    for i, (name, final_raw) in enumerate(zip(names, finals)):
        item = repo.get_item_by_name(db, name)
        if item is None:
            errors.append(f"Unknown item '{name[:40]}'.")
            continue
        e: list = []
        final = _parse_float(final_raw, f"Final quantity for {item['name']}", 0, MAX_QUANTITY, e)
        if e:
            errors.extend(e)
            continue
        res = services.forecast_and_recommend(db, settings, item["name"], meal, plan_date, log=True)
        rec, fc = res["recommendation"], res["forecast"]
        floor = max(float(settings.get("min_prep_default") or 0), float(item["min_prep"] or 0))
        if floor and final < floor:
            below_min.append(f"{item['name']} ({final:g} < minimum {floor:g})")
        note = validation.clean_text(notes[i] if i < len(notes) else "", 200) or None
        repo.save_plan(db, plan_date=plan_date.isoformat(), meal=meal, item_id=item["id"],
                       forecast_qty=fc["estimate"], suggested_qty=rec["suggested"], final_qty=final,
                       unit=item["unit"], method=fc["method"], note=note,
                       override=rec["suggested"] is None or abs(final - rec["suggested"]) > 1e-9)
        saved += 1
    if saved:
        flash(f"Saved the preparation plan for {meal} on {plan_date.isoformat()} ({saved} item(s)). "
              "Recommendations are estimates; the final quantities are yours.", "success")
    for e_msg in errors[:5]:
        flash(e_msg, "error")
    if below_min:
        flash("Below your configured minimum: " + "; ".join(below_min[:6]) + ". Saved as entered - please double-check.", "warning")
    return redirect(url_for("pages.plan_page", date=plan_date.isoformat(), meal=meal))


# ------------------------------------------------------------------ records

def _filtered_records(db, args) -> pd.DataFrame:
    frame = repo.fetch_records(
        db, scope="all", start=services._date_arg(args.get("start")), end=services._date_arg(args.get("end")),
        meal=args.get("meal") or None, item=args.get("item") or None,
        search=(args.get("q") or "").strip()[:60] or None)
    source = args.get("source")
    if source in ("demo", "upload", "manual") and not frame.empty:
        frame = frame[frame["source"] == source]
    return frame


@bp.route("/records")
def records_page():
    db = get_db()
    frame = _filtered_records(db, request.args)
    frame = frame.sort_values(["date", "meal", "food_item"], ascending=[False, True, True])
    try:
        page = max(int(request.args.get("page", 1)), 1)
    except ValueError:
        page = 1
    total = len(frame)
    pages = max(math.ceil(total / PAGE_SIZE), 1)
    page = min(page, pages)
    chunk = frame.iloc[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
    rows = []
    for r in chunk.to_dict("records"):
        r["waste_pct"] = services.waste.waste_pct(r["leftovers"], r["prepared"])
        rows.append(r)
    unit = services.waste.primary_unit(frame, repo.get_settings(db)["default_unit"]) if total else None
    one_unit = frame[frame["unit"] == unit] if total else frame
    args = {k: v for k, v in request.args.items() if k != "page" and v}
    return render_template("records.html", rows=rows, total=total, page=page, pages=pages, args=args,
                           items=repo.list_items(db), summary=analytics.kpis(one_unit), unit=unit,
                           mixed_units=bool(total and frame["unit"].nunique() > 1))


@bp.route("/records/export.csv")
def records_export():
    db = get_db()
    frame = _filtered_records(db, request.args)
    return Response(reports.records_csv(frame), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=freshroute_records.csv"})


def _form_record(db, settings):
    ctx = repo.build_context(db)
    raw = {k: request.form.get(k, "") for k in
           ("date", "meal", "food_item", "unit", "quantity_prepared", "quantity_consumed",
            "leftovers", "redistributed", "student_count", "notes", "category")}
    return raw, validation.validate_record(raw, ctx)


@bp.route("/records/new", methods=["GET", "POST"])
def record_new():
    db = get_db()
    settings = repo.get_settings(db)
    form = {k: request.args.get(k, "") for k in ("date", "meal", "food_item", "quantity_prepared", "unit")}
    form["date"] = form["date"] or (date.today() - timedelta(days=0)).isoformat()
    form["unit"] = form["unit"] or settings["default_unit"]
    errors, warnings = [], []
    if request.method == "POST":
        form, (clean, errors, warnings) = _form_record(db, settings)
        if clean:
            try:
                repo.insert_record(db, clean, "manual")
                db.commit()
                services.forecasting.clear_cache()
                flash(f"Saved {clean['food_item']} - {clean['meal']} on {clean['date']}. "
                      f"Waste: {services.waste.waste_pct(clean['leftovers'], clean['prepared']) or 0:.1f}% "
                      f"({clean['leftovers']:g} {clean['unit']} left over).", "success")
                for w in warnings:
                    flash(w, "warning")
                return redirect(url_for("pages.records_page", item=clean["food_item"]))
            except sqlite3.IntegrityError:
                db.rollback()
                errors = [f"A record for {clean['food_item']} at {clean['meal']} on {clean['date']} already exists. "
                          "Edit that record instead of adding a new one."]
    return render_template("record_form.html", form=form, errors=errors, warnings=warnings,
                           items=repo.list_items(db), record=None)


@bp.route("/records/<int:record_id>/edit", methods=["GET", "POST"])
def record_edit(record_id: int):
    db = get_db()
    settings = repo.get_settings(db)
    record = repo.get_record(db, record_id)
    if record is None:
        abort(404)
    form = {"date": record["date"], "meal": record["meal"], "food_item": record["food_item"],
            "unit": record["unit"], "quantity_prepared": f"{record['prepared']:g}",
            "quantity_consumed": f"{record['consumed']:g}",
            "leftovers": "" if record["supplied_leftovers"] is None else f"{record['supplied_leftovers']:g}",
            "redistributed": f"{record['redistributed']:g}" if record["redistributed"] else "",
            "student_count": "" if record["student_count"] is None else str(record["student_count"]),
            "notes": record["notes"] or ""}
    errors, warnings = [], []
    if request.method == "POST":
        form, (clean, errors, warnings) = _form_record(db, settings)
        if clean:
            try:
                repo.update_record(db, record_id, clean)
                repo.prune_unused_items(db)
                db.commit()
                services.forecasting.clear_cache()
                flash("Record updated.", "success")
                for w in warnings:
                    flash(w, "warning")
                return redirect(url_for("pages.records_page"))
            except sqlite3.IntegrityError:
                db.rollback()
                errors = ["Another record already exists for that date, meal and item."]
    return render_template("record_form.html", form=form, errors=errors, warnings=warnings,
                           items=repo.list_items(db), record=record)


@bp.route("/records/<int:record_id>/delete", methods=["POST"])
def record_delete(record_id: int):
    db = get_db()
    if repo.delete_record(db, record_id):
        repo.prune_unused_items(db)
        db.commit()
        services.forecasting.clear_cache()
        flash("Record deleted.", "success")
    else:
        flash("That record no longer exists.", "error")
    return _redirect_back(url_for("pages.records_page"))


# ---------------------------------------------------------------- analytics

@bp.route("/analytics")
def analytics_page():
    db = get_db()
    settings = repo.get_settings(db)
    frame, unit, excluded, filters = services.load_records(db, settings, request.args)
    engine, _ = services.engine_for(db, settings, unit)
    evaluation = engine.evaluate()
    costs = services.item_costs(db)
    live = services.logged_forecast_accuracy(db, settings)
    charts = {
        "daily": analytics.trend_by_day(frame), "weekly": analytics.trend_by_week(frame),
        "items": analytics.by_item(frame), "meals": analytics.by_meal(frame),
        "weekday": analytics.by_weekday(frame), "pva": evaluation["series"],
        "pvc": analytics.prepared_vs_consumed_by_day(frame), "unit": unit,
    }
    return render_template(
        "analytics.html", frame_empty=frame.empty, filters=filters, unit=unit, excluded=excluded,
        charts=charts, evaluation=evaluation, live=live, kpis=analytics.kpis(frame),
        insights=analytics.insights(frame, settings, costs), cost=analytics.estimate_avoidable_cost(frame, settings, costs),
        by_item=analytics.by_item(frame), has_costs=bool(costs),
        categories=sorted(repo.fetch_records(db, scope=filters["scope"])["category"].unique().tolist()))


@bp.route("/time-machine")
def time_machine_page():
    db = get_db()
    settings = repo.get_settings(db)
    tm = services.run_backtest(db, settings, request.args)
    charts = {"tm_frontier": tm["frontier"], "tm_daily": tm["daily"], "unit": tm["unit"]}
    return render_template("time_machine.html", tm=tm, charts=charts, unit=tm["unit"])


# ------------------------------------------------------------------ reports

def _build_report(db, settings, args):
    frame, unit, excluded, filters = services.load_records(db, settings, args)
    engine, _ = services.engine_for(db, settings, unit)
    report = reports.build_report(
        frame, cafeteria=settings["cafeteria_name"], start=filters["start"] or None,
        end=filters["end"] or None, unit=unit, excluded_rows=excluded, evaluation=engine.evaluate(),
        settings=settings, item_costs=services.item_costs(db), source_counts=repo.source_counts(db))
    return report, filters


@bp.route("/reports")
def reports_page():
    db = get_db()
    settings = repo.get_settings(db)
    report, filters = _build_report(db, settings, request.args)
    lo, hi = repo.date_bounds(db, filters["scope"])
    return render_template("reports.html", report=report, filters=filters, bounds=(lo, hi))


@bp.route("/reports/summary.csv")
def report_summary_csv():
    db = get_db()
    report, _ = _build_report(db, repo.get_settings(db), request.args)
    return Response(reports.summary_csv(report), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=freshroute_summary.csv"})


@bp.route("/reports/report.pdf")
def report_pdf():
    db = get_db()
    report, _ = _build_report(db, repo.get_settings(db), request.args)
    return Response(reports.report_pdf(report), mimetype="application/pdf",
                    headers={"Content-Disposition": "attachment; filename=freshroute_report.pdf"})


# ----------------------------------------------------------------- settings

@bp.route("/settings")
def settings_page():
    db = get_db()
    return render_template("settings.html", items=repo.list_items(db), imports=repo.list_imports(db, 5))


@bp.route("/scope", methods=["POST"])
def set_scope():
    scope = request.form.get("scope")
    if scope not in SCOPES:
        abort(400, description="Unknown data source selection.")
    repo.save_settings(get_db(), {"active_scope": scope})
    flash(f"Now showing: {SCOPE_LABELS[scope]}.", "info")
    return _redirect_back(url_for("pages.dashboard"))


@bp.route("/settings/general", methods=["POST"])
def settings_general():
    db = get_db()
    current = repo.get_settings(db)
    errors: list = []
    name = validation.clean_text(request.form.get("cafeteria_name"), 80)
    if not name:
        errors.append("Cafeteria name cannot be empty.")

    meals, seen = [], set()
    for line in re.split(r"[\n,]+", request.form.get("meals", "")):
        m = validation.clean_text(line, 30)
        if m and m.lower() not in seen:
            if not MEAL_NAME_RE.match(m):
                errors.append(f"Meal name '{m}' has unsupported characters.")
            seen.add(m.lower())
            meals.append(m)
    if not 1 <= len(meals) <= 12:
        errors.append("Provide between 1 and 12 meal categories.")
    in_use_meals = {r["meal"] for r in db.execute("SELECT DISTINCT meal FROM meal_records")}
    missing = sorted(m for m in in_use_meals if m.lower() not in seen)
    if missing:
        errors.append("These meals are used by existing records and cannot be removed: " + ", ".join(missing))

    units, seen_u = [], set()
    for line in re.split(r"[\n,]+", request.form.get("units", "")):
        u = validation.clean_text(line, 20).lower()
        if u and u not in seen_u:
            if not UNIT_NAME_RE.match(u):
                errors.append(f"Unit '{u}' has unsupported characters (lowercase letters only).")
            seen_u.add(u)
            units.append(u)
    if not 1 <= len(units) <= 12:
        errors.append("Provide between 1 and 12 units.")
    in_use_units = {r["unit"] for r in db.execute("SELECT DISTINCT unit FROM meal_records")}
    missing_u = sorted(u for u in in_use_units if u.lower() not in seen_u)
    if missing_u:
        errors.append("These units are used by existing records and cannot be removed: " + ", ".join(missing_u))
    default_unit = (request.form.get("default_unit") or "").strip().lower()
    if default_unit not in seen_u:
        errors.append("The default unit must be one of the configured units.")

    buffer_pct = _parse_float(request.form.get("safety_buffer_pct"), "Safety buffer", 0, 50, errors)
    min_prep = _parse_float(request.form.get("min_prep_default"), "Global minimum preparation", 0, MAX_QUANTITY, errors)
    max_cut = _parse_float(request.form.get("max_reduction_pct"), "Maximum reduction", 0, 100, errors)
    ok_pct = _parse_float(request.form.get("acceptable_waste_pct"), "Acceptable waste", 0, 100, errors)
    high_pct = _parse_float(request.form.get("high_waste_threshold_pct"), "High-waste threshold", 0, 100, errors)
    if ok_pct is not None and high_pct is not None and high_pct <= ok_pct:
        errors.append("The high-waste threshold must be above the acceptable waste level.")
    if errors:
        for e in errors[:6]:
            flash(e, "error")
        return redirect(url_for("pages.settings_page"))
    repo.save_settings(db, {
        "cafeteria_name": name, "meals": meals, "units": units, "default_unit": default_unit,
        "safety_buffer_pct": buffer_pct, "min_prep_default": min_prep, "max_reduction_pct": max_cut,
        "acceptable_waste_pct": ok_pct, "high_waste_threshold_pct": high_pct})
    services.forecasting.clear_cache()
    flash("Settings saved.", "success")
    return redirect(url_for("pages.settings_page"))


@bp.route("/settings/items/<int:item_id>", methods=["POST"])
def settings_item(item_id: int):
    db = get_db()
    item = repo.get_item(db, item_id)
    if item is None:
        abort(404)
    errors: list = []
    cost = _optional_float(request.form.get("cost_per_unit"), "Cost per unit", 0, 1_000_000, errors)
    min_prep = _optional_float(request.form.get("min_prep"), "Minimum preparation", 0, MAX_QUANTITY, errors)
    if errors:
        for e in errors:
            flash(e, "error")
    else:
        repo.update_item(db, item_id, cost_per_unit=cost, min_prep=min_prep)
        flash(f"Updated {item['name']}.", "success")
    return redirect(url_for("pages.settings_page") + "#items")


@bp.route("/settings/demo/<action>", methods=["POST"])
def settings_demo(action: str):
    if action == "load":
        result = seed_demo_now()
        flash(f"Synthetic demo data loaded ({result['inserted']} records added, {result['skipped']} already present).", "success")
    elif action == "reset":
        result = reset_demo_now()
        flash(f"Demo data reset: removed {result['removed']} and recreated {result['inserted']} synthetic records. "
              "Your own records were not touched.", "success")
    else:
        abort(404)
    services.forecasting.clear_cache()
    return redirect(url_for("pages.settings_page"))


@bp.route("/settings/data/delete", methods=["POST"])
def settings_delete():
    db = get_db()
    target = request.form.get("target")
    if request.form.get("confirm", "").strip() != "DELETE":
        flash("Nothing was deleted. Type DELETE in the confirmation box to confirm.", "error")
        return redirect(url_for("pages.settings_page") + "#data")
    if target in ("demo", "upload", "manual"):
        removed = repo.delete_by_source(db, target)
        flash(f"Deleted {removed} {SOURCE_LABELS[target].lower()} record(s).", "success")
    elif target == "all":
        repo.delete_everything(db)
        repo.save_settings(db, {"demo_seeded": True})
        flash("All records, plans, forecasts and import history were deleted. Settings were kept.", "success")
    else:
        abort(400, description="Unknown deletion target.")
    services.forecasting.clear_cache()
    return redirect(url_for("pages.settings_page") + "#data")
