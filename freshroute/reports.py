"""Report assembly and exports (CSV and PDF)."""
from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Optional

import pandas as pd

from . import analytics, waste
from .constants import SOURCE_LABELS

EXPORT_COLUMNS = [
    "date", "meal", "food_item", "category", "unit", "prepared", "consumed", "leftovers",
    "supplied_leftovers", "mismatch", "redistributed", "student_count", "notes", "source",
]
EXPORT_HEADERS = {
    "prepared": "quantity_prepared", "consumed": "quantity_consumed",
    "mismatch": "leftover_mismatch_flag", "source": "data_source",
}


def _csv_safe(value):
    """Neutralise spreadsheet formula injection in exported text cells."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def records_csv(df: pd.DataFrame) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow([EXPORT_HEADERS.get(c, c) for c in EXPORT_COLUMNS])
    for row in df.to_dict("records"):
        out = []
        for col in EXPORT_COLUMNS:
            value = row.get(col)
            if value is None or (isinstance(value, float) and pd.isna(value)):
                value = ""
            elif col == "source":
                value = SOURCE_LABELS.get(value, value)
            out.append(_csv_safe(value))
        writer.writerow(out)
    return buf.getvalue()


def summary_csv(report: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["FreshRoute AI report summary"])
    w.writerow(["Cafeteria", _csv_safe(report["cafeteria"])])
    w.writerow(["Period", f"{report['start']} to {report['end']}"])
    w.writerow(["Unit", report["unit"]])
    w.writerow(["Data sources included", ", ".join(report["source_labels"]) or "none"])
    w.writerow(["Generated", report["generated"]])
    w.writerow([])
    k = report["kpis"]
    w.writerow(["Metric", "Value"])
    for label, key in [("Records", "records"), ("Service days", "days"), ("Prepared", "prepared"),
                       ("Consumed", "consumed"), ("Leftovers", "leftovers"),
                       ("Redistributed (recorded)", "redistributed"), ("Waste %", "waste_pct")]:
        w.writerow([label, "" if k.get(key) is None else k.get(key)])
    w.writerow([])
    w.writerow(["By item"])
    w.writerow(["food_item", "records", "prepared", "consumed", "leftovers", "waste_pct"])
    for r in report["by_item"]:
        w.writerow([_csv_safe(r["food_item"]), int(r["records"]), r["prepared"], r["consumed"],
                    r["leftovers"], "" if r["waste_pct"] is None else r["waste_pct"]])
    w.writerow([])
    w.writerow(["By meal"])
    w.writerow(["meal", "records", "prepared", "consumed", "leftovers", "waste_pct"])
    for r in report["by_meal"]:
        w.writerow([_csv_safe(r["meal"]), int(r["records"]), r["prepared"], r["consumed"],
                    r["leftovers"], "" if r["waste_pct"] is None else r["waste_pct"]])
    ev = report.get("evaluation")
    w.writerow([])
    w.writerow(["Forecast evaluation"])
    if ev and ev.get("baseline"):
        w.writerow(["Held-out observations", ev["n_test"]])
        w.writerow(["Split date", ev["split_date"]])
        w.writerow(["Baseline MAE", ev["baseline"]["mae"]])
        if ev.get("ml"):
            w.writerow(["Random Forest MAE", ev["ml"]["mae"]])
        w.writerow(["Method used for forecasts", ev["selected"]])
    else:
        w.writerow(["Not available", ev["message"] if ev else "No data"])
    return buf.getvalue()


def build_report(df: pd.DataFrame, *, cafeteria: str, start: Optional[str], end: Optional[str],
                 unit: str, excluded_rows: int, evaluation: Optional[dict], settings: dict,
                 item_costs: dict, source_counts: dict) -> dict:
    sources = sorted(df["source"].unique().tolist()) if not df.empty else []
    report = {
        "cafeteria": cafeteria,
        "start": start or (df["date"].min() if not df.empty else "-"),
        "end": end or (df["date"].max() if not df.empty else "-"),
        "unit": unit,
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "sources": sources,
        "source_labels": [SOURCE_LABELS.get(s, s) for s in sources],
        "has_demo": "demo" in sources,
        "excluded_rows": excluded_rows,
        "kpis": analytics.kpis(df),
        "by_item": analytics.by_item(df),
        "by_meal": analytics.by_meal(df),
        "weekly": analytics.trend_by_week(df),
        "evaluation": evaluation,
        "insights": analytics.insights(df, settings, item_costs),
        "cost": analytics.estimate_avoidable_cost(df, settings, item_costs),
        "mismatch_rows": int(df["mismatch"].sum()) if not df.empty else 0,
        "coverage": {
            "days": int(df["date"].nunique()) if not df.empty else 0,
            "items": int(df["food_item"].nunique()) if not df.empty else 0,
            "records": int(len(df)),
        },
    }
    return report


def report_pdf(report: dict) -> bytes:
    """Render the report with ReportLab (pure Python, works offline)."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from xml.sax.saxutils import escape

    forest = colors.HexColor("#14532d")
    mint = colors.HexColor("#d9f2e3")
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Title"], textColor=forest, fontSize=20, alignment=0)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], textColor=forest, fontSize=13, spaceBefore=10)
    body = ParagraphStyle("b", parent=styles["BodyText"], fontSize=9.5, leading=13)
    note = ParagraphStyle("n", parent=body, textColor=colors.HexColor("#555555"), fontSize=8.5)

    def table(data, widths=None):
        t = Table(data, colWidths=widths, repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), forest), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5), ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#c8d6cc")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, mint]),
            ("ALIGN", (1, 1), (-1, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        return t

    def fmt(v, suffix=""):
        return "-" if v is None else f"{v:,.1f}{suffix}"

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm,
                            title="FreshRoute AI report", author="FreshRoute AI")
    s = []
    s.append(Paragraph("FreshRoute AI - Cafeteria report", h1))
    s.append(Paragraph(f"{escape(report['cafeteria'])} &middot; {report['start']} to {report['end']} &middot; "
                       f"generated {report['generated']}", body))
    s.append(Paragraph("Data sources: " + escape(", ".join(report["source_labels"]) or "none"), body))
    if report["has_demo"]:
        s.append(Paragraph("<b>This report includes SYNTHETIC DEMONSTRATION data for a fictional cafeteria. "
                           "It is not real cafeteria performance.</b>", body))
    if report["excluded_rows"]:
        s.append(Paragraph(f"{report['excluded_rows']} record(s) in other units were excluded; "
                           f"all quantities below are in <b>{escape(report['unit'])}</b>.", note))

    k = report["kpis"]
    s.append(Paragraph("Recorded results (actual measurements)", h2))
    s.append(table([
        ["Metric", "Value"],
        ["Records / service days", f"{k['records']} / {k['days']}"],
        [f"Prepared ({report['unit']})", fmt(k["prepared"])],
        [f"Consumed ({report['unit']})", fmt(k["consumed"])],
        [f"Leftovers ({report['unit']})", fmt(k["leftovers"])],
        ["Redistributed (recorded)", fmt(k["redistributed"])],
        ["Waste % (leftovers / prepared)", fmt(k["waste_pct"], "%")],
    ], [90 * mm, 40 * mm]))

    s.append(Paragraph("By meal", h2))
    rows = [["Meal", "Records", "Prepared", "Consumed", "Leftovers", "Waste %"]]
    rows += [[r["meal"], int(r["records"]), fmt(r["prepared"]), fmt(r["consumed"]), fmt(r["leftovers"]),
              fmt(r["waste_pct"], "%")] for r in report["by_meal"]]
    s.append(table(rows) if len(rows) > 1 else Paragraph("No records in this period.", body))

    s.append(Paragraph("By food item", h2))
    rows = [["Item", "Records", "Prepared", "Consumed", "Leftovers", "Waste %"]]
    rows += [[r["food_item"][:34], int(r["records"]), fmt(r["prepared"]), fmt(r["consumed"]),
              fmt(r["leftovers"]), fmt(r["waste_pct"], "%")] for r in report["by_item"]]
    s.append(table(rows) if len(rows) > 1 else Paragraph("No records in this period.", body))

    s.append(Paragraph("Forecast evaluation (held-out data only)", h2))
    ev = report.get("evaluation")
    if ev and ev.get("baseline"):
        lines = [["Measure", "Value"],
                 ["Held-out observations", str(ev["n_test"])],
                 ["Chronological split date", str(ev["split_date"])],
                 ["Baseline MAE", f"{ev['baseline']['mae']} {report['unit']}"]]
        if ev.get("ml"):
            lines.append(["Random Forest MAE", f"{ev['ml']['mae']} {report['unit']}"])
        lines.append(["Method used for forecasts", "Random Forest" if ev["selected"] == "ml" else "Baseline"])
        s.append(table(lines, [90 * mm, 40 * mm]))
        s.append(Paragraph(escape(ev.get("message") or ""), note))
    else:
        s.append(Paragraph(escape((ev or {}).get("message") or "Not enough data to evaluate forecasts."), body))

    if report["insights"]:
        s.append(Paragraph("Observations (rule-based, from the data above)", h2))
        for ins in report["insights"]:
            tag = " [ESTIMATE]" if ins["severity"] == "estimate" else ""
            s.append(Paragraph("&bull; " + escape(ins["text"]) + tag, body))
    s.append(Spacer(1, 8))
    s.append(Paragraph(
        "Waste % = leftovers / prepared x 100. Forecasts and cost figures are estimates, not guarantees. "
        "Recommendations support, and do not replace, the cafeteria operator's judgement.", note))
    doc.build(s)
    return buf.getvalue()
