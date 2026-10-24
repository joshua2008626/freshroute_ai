"""CSV and record validation.

Nothing in an uploaded file is ever executed or trusted: the content is decoded
as UTF-8 text, parsed with the standard `csv` module, and every field is
validated against strict rules. Problems are reported in plain language, per row.
"""
from __future__ import annotations

import csv
import hashlib
import io
import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from . import waste
from .constants import (
    ALL_COLUMNS,
    COLUMN_SYNONYMS,
    MAX_CSV_ROWS,
    MAX_ITEM_NAME_LEN,
    MAX_NOTES_LEN,
    MAX_QUANTITY,
    MAX_REPORTED_ERRORS,
    MAX_UPLOAD_BYTES,
    OPTIONAL_COLUMNS,
    PREVIEW_ROWS,
    REQUIRED_COLUMNS,
    UNIT_SYNONYMS,
)

ITEM_NAME_RE = re.compile(r"^[\w][\w &()'’.,/+-]*$", re.UNICODE)
CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d")


@dataclass
class ValidationContext:
    """What the validator needs to know about the configured cafeteria."""

    meals: list
    units: list
    default_unit: str = "servings"
    item_units: dict = field(default_factory=dict)      # lower-name -> unit
    item_names: dict = field(default_factory=dict)      # lower-name -> canonical name
    item_categories: dict = field(default_factory=dict)  # lower-name -> category
    today: date = field(default_factory=date.today)


# ---------------------------------------------------------------- helpers

def clean_text(value, max_len: int) -> str:
    text = CONTROL_CHARS_RE.sub("", str(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_len]


def parse_date(value) -> Optional[date]:
    text = str(value or "").strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _parse_number(raw, label: str, errors: list, *, required: bool, integer: bool = False):
    text = "" if raw is None else str(raw).strip()
    if text == "":
        if required:
            errors.append(f"{label} is missing.")
        return None
    try:
        number = float(text)
    except ValueError:
        errors.append(f"{label} '{text[:20]}' is not a number (plain digits only, e.g. 120 or 12.5).")
        return None
    if math.isnan(number) or math.isinf(number):
        errors.append(f"{label} must be a finite number.")
        return None
    if number < 0:
        errors.append(f"{label} cannot be negative (got {text[:20]}).")
        return None
    if number > MAX_QUANTITY:
        errors.append(f"{label} is unrealistically large (limit {MAX_QUANTITY:,}).")
        return None
    if integer and abs(number - round(number)) > 1e-9:
        errors.append(f"{label} must be a whole number (got {text[:20]}).")
        return None
    return int(round(number)) if integer else round(number, 3)


def normalise_unit(value, ctx: ValidationContext) -> Optional[str]:
    text = str(value or "").strip().lower()
    if not text:
        return ctx.default_unit
    text = UNIT_SYNONYMS.get(text, text)
    for allowed in ctx.units:
        if allowed.lower() == text:
            return allowed
    return None


def normalise_meal(value, ctx: ValidationContext) -> Optional[str]:
    text = str(value or "").strip().lower()
    for allowed in ctx.meals:
        if allowed.lower() == text:
            return allowed
    return None


# ------------------------------------------------------------ single record

def validate_record(raw: dict, ctx: ValidationContext, *, allow_future: bool = False):
    """Validate one record.

    Returns (clean_dict_or_None, errors, warnings). `clean` is None whenever there
    is at least one error. Works for CSV rows and for the manual entry form.
    """
    errors: list = []
    warnings: list = []

    # --- date
    d = parse_date(raw.get("date"))
    if d is None:
        shown = str(raw.get("date") or "").strip()[:20]
        errors.append(
            f"Date '{shown}' is missing or not valid - use YYYY-MM-DD (e.g. 2026-03-14)."
            if shown else "Date is missing - use YYYY-MM-DD (e.g. 2026-03-14)."
        )
    elif d > ctx.today and not allow_future:
        errors.append(
            f"Date {d.isoformat()} is in the future - only meals that have already been served can be recorded."
        )
    elif d.year < 2000:
        errors.append(f"Date {d.isoformat()} looks wrong (before year 2000).")

    # --- meal
    meal = normalise_meal(raw.get("meal"), ctx)
    if meal is None:
        shown = str(raw.get("meal") or "").strip()[:30]
        allowed = ", ".join(ctx.meals)
        errors.append(
            f"Meal '{shown}' is not one of your configured meals ({allowed}). Add it in Settings first."
            if shown else f"Meal is missing (expected one of: {allowed})."
        )

    # --- food item
    name = clean_text(raw.get("food_item"), MAX_ITEM_NAME_LEN + 1)
    if not name:
        errors.append("Food item name is missing.")
    elif len(name) > MAX_ITEM_NAME_LEN:
        errors.append(f"Food item name is longer than {MAX_ITEM_NAME_LEN} characters.")
    elif not ITEM_NAME_RE.match(name):
        errors.append("Food item name contains unsupported characters (letters, digits, spaces and & ( ) ' . , / + - are allowed).")
    key = name.lower()
    if key in ctx.item_names:
        name = ctx.item_names[key]

    # --- unit
    unit = normalise_unit(raw.get("unit"), ctx)
    if unit is None:
        shown = str(raw.get("unit") or "").strip()[:20]
        errors.append(f"Unit '{shown}' is not supported (allowed: {', '.join(ctx.units)}).")
    elif name and key in ctx.item_units and ctx.item_units[key] != unit:
        errors.append(
            f"'{name}' is already tracked in {ctx.item_units[key]} but this row uses {unit}. "
            "Mixing units for one item would make totals and forecasts meaningless - convert before importing."
        )

    # --- quantities
    prepared = _parse_number(raw.get("quantity_prepared"), "Quantity prepared", errors, required=True)
    consumed = _parse_number(raw.get("quantity_consumed"), "Quantity consumed", errors, required=True)
    supplied = _parse_number(raw.get("leftovers"), "Leftovers", errors, required=False)
    redistributed = _parse_number(raw.get("redistributed"), "Redistributed quantity", errors, required=False)
    students = _parse_number(raw.get("student_count"), "Student count", errors, required=False, integer=True)

    derived = None
    mismatch = 0
    if prepared is not None and consumed is not None:
        if consumed - prepared > waste.TOLERANCE:
            errors.append(
                f"Consumed ({consumed:g}) is greater than prepared ({prepared:g}). "
                "Check the numbers, or record the true prepared amount including reserve stock."
            )
        else:
            derived = waste.derive_leftovers(prepared, consumed)
            if waste.leftovers_conflict(supplied, derived):
                mismatch = 1
                warnings.append(
                    f"Supplied leftovers ({supplied:g}) differ from prepared - consumed ({derived:g}). "
                    "Both values are stored and the row is flagged; totals use prepared - consumed."
                )
    if redistributed is not None and derived is not None and redistributed - derived > waste.TOLERANCE:
        errors.append(
            f"Redistributed ({redistributed:g}) is more than the leftovers available ({derived:g})."
        )

    # --- optional text
    notes = clean_text(raw.get("notes"), MAX_NOTES_LEN) or None
    category = clean_text(raw.get("category"), 40) or ctx.item_categories.get(key) or "General"

    if errors:
        return None, errors, warnings

    clean = {
        "date": d.isoformat(),
        "meal": meal,
        "food_item": name,
        "category": category,
        "unit": unit,
        "prepared": prepared,
        "consumed": consumed,
        "leftovers": derived,
        "supplied_leftovers": supplied,
        "mismatch": mismatch,
        "redistributed": redistributed or 0.0,
        "student_count": students,
        "notes": notes,
    }
    return clean, errors, warnings


# --------------------------------------------------------------- CSV import

def normalise_header(name: str) -> str:
    text = re.sub(r"[\s\-]+", "_", (name or "").strip().lower().lstrip("﻿"))
    return COLUMN_SYNONYMS.get(text, text)


def file_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def analyze_csv(content: bytes, ctx: ValidationContext, existing_keys: set) -> dict:
    """Parse and validate a CSV upload without touching the database.

    `existing_keys` is a set of (date, meal, lower-case item name) already stored,
    used to flag duplicates instead of silently replacing records.
    """
    result = {
        "ok": False,
        "fatal": None,
        "columns": {"found": [], "missing": [], "ignored": [], "optional_present": []},
        "counts": {"total": 0, "valid": 0, "warning": 0, "invalid": 0, "duplicate": 0, "importable": 0},
        "rows": [],
        "errors": [],
        "importable": [],
        "sha256": file_sha256(content),
    }

    if not content or not content.strip():
        result["fatal"] = "The file is empty."
        return result
    if len(content) > MAX_UPLOAD_BYTES:
        result["fatal"] = f"The file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
        return result
    if b"\x00" in content:
        result["fatal"] = "This does not look like a text CSV file (it contains binary data)."
        return result
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        result["fatal"] = "The file is not UTF-8 text. In Excel use 'Save As > CSV UTF-8'."
        return result

    sample = text[:4096]
    delimiter = ","
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
    except csv.Error:
        pass

    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    try:
        header_raw = next(reader)
    except StopIteration:
        result["fatal"] = "The file has no header row."
        return result

    headers = [normalise_header(h) for h in header_raw]
    if len(set(headers)) != len(headers):
        dupes = sorted({h for h in headers if headers.count(h) > 1})
        result["fatal"] = f"Duplicate column names found: {', '.join(dupes)}."
        return result

    missing = [c for c in REQUIRED_COLUMNS if c not in headers]
    result["columns"]["found"] = [h for h in headers if h in ALL_COLUMNS]
    result["columns"]["ignored"] = [h for h in headers if h and h not in ALL_COLUMNS]
    result["columns"]["optional_present"] = [h for h in headers if h in OPTIONAL_COLUMNS]
    result["columns"]["missing"] = missing
    if missing:
        result["fatal"] = (
            "Required column(s) missing: " + ", ".join(missing)
            + ". Required columns are: " + ", ".join(REQUIRED_COLUMNS) + "."
        )
        return result

    item_units = dict(ctx.item_units)
    seen_in_file: dict = {}
    counts = result["counts"]
    local_ctx = ValidationContext(
        meals=ctx.meals, units=ctx.units, default_unit=ctx.default_unit,
        item_units=item_units, item_names=dict(ctx.item_names),
        item_categories=dict(ctx.item_categories), today=ctx.today,
    )

    for line_no, values in enumerate(reader, start=2):
        if not any((v or "").strip() for v in values):
            continue  # skip fully blank lines
        counts["total"] += 1
        if counts["total"] > MAX_CSV_ROWS:
            result["fatal"] = f"The file has more than {MAX_CSV_ROWS:,} data rows. Please split it."
            return result

        raw = {h: (values[i] if i < len(values) else "") for i, h in enumerate(headers)}
        row_info = {"row": line_no, "status": "valid", "errors": [], "warnings": [], "data": None}

        if len(values) > len(headers) and any((v or "").strip() for v in values[len(headers):]):
            row_info["status"] = "invalid"
            row_info["errors"].append(
                f"This row has {len(values)} values but the header has {len(headers)} columns "
                "(a comma inside a value must be wrapped in quotes)."
            )
            counts["invalid"] += 1
            _record_row(result, row_info)
            continue

        clean, errors, warnings = validate_record(raw, local_ctx)
        row_info["warnings"] = warnings
        if errors:
            row_info["status"] = "invalid"
            row_info["errors"] = errors
            counts["invalid"] += 1
        else:
            key = (clean["date"], clean["meal"], clean["food_item"].lower())
            if key in seen_in_file:
                row_info["status"] = "invalid"
                row_info["errors"] = [
                    f"Duplicate of row {seen_in_file[key]} in this file "
                    f"({clean['date']}, {clean['meal']}, {clean['food_item']})."
                ]
                counts["invalid"] += 1
            elif key in existing_keys:
                row_info["status"] = "duplicate"
                row_info["warnings"].append(
                    "A record for this date, meal and item already exists - it will be skipped, not replaced."
                )
                row_info["data"] = clean
                counts["duplicate"] += 1
            else:
                seen_in_file[key] = line_no
                item_units.setdefault(clean["food_item"].lower(), clean["unit"])
                local_ctx.item_names.setdefault(clean["food_item"].lower(), clean["food_item"])
                local_ctx.item_categories.setdefault(clean["food_item"].lower(), clean["category"])
                row_info["data"] = clean
                if warnings:
                    row_info["status"] = "warning"
                    counts["warning"] += 1
                else:
                    counts["valid"] += 1
                result["importable"].append(clean)
        _record_row(result, row_info)

    counts["importable"] = len(result["importable"])
    if counts["total"] == 0:
        result["fatal"] = "The file has a header but no data rows."
        return result
    result["ok"] = True
    return result


def _record_row(result: dict, row_info: dict) -> None:
    if len(result["rows"]) < PREVIEW_ROWS or row_info["status"] in ("invalid", "warning"):
        if len(result["rows"]) < PREVIEW_ROWS * 4:
            result["rows"].append(row_info)
    if row_info["status"] == "invalid" and len(result["errors"]) < MAX_REPORTED_ERRORS:
        result["errors"].append({"row": row_info["row"], "messages": row_info["errors"]})
