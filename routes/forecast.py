from datetime import date

from flask import Blueprint, render_template, request

import db
from services import (
    apply_scenario,
    build_cash_forecast,
    first_negative_date,
    lowest_balance,
    parse_cents,
    summary_sentence,
    today,
    weekly_summary,
)

forecast_bp = Blueprint("forecast", __name__, url_prefix="")

FORECAST_DAY_OPTIONS = (30, 60, 90)
SCENARIO_OPTIONS = (
    ("none", "No scenario"),
    ("late_customer", "A customer pays late"),
    ("lose_customer", "A customer stops paying"),
    ("delay_all", "Every customer pays late"),
    ("extra_expense", "Unexpected expense"),
)
SCENARIO_TYPES = tuple(value for value, _ in SCENARIO_OPTIONS)


def _parse_int(text):
    try:
        return int((text or "").strip())
    except ValueError:
        return None


def _parse_scenario(kind, args):
    """Return (scenario_dict or None, error_message or None) built from the query string."""
    if kind == "none":
        return None, None
    scenario = {"type": kind}
    if kind in ("late_customer", "lose_customer"):
        cid = _parse_int(args.get("customer_id"))
        if cid is None:
            return None, "Choose a customer for this scenario."
        scenario["customer_id"] = cid
    if kind in ("late_customer", "delay_all"):
        default_days = 15 if kind == "late_customer" else 7
        raw = (args.get("scenario_days") or "").strip()
        days = default_days if raw == "" else _parse_int(raw)
        if days is None or days < 0:
            return None, "Days must be a whole number, 0 or more."
        scenario["days"] = days
    if kind == "extra_expense":
        cents = parse_cents(args.get("amount"))
        if cents is None or cents <= 0:
            return None, "Enter a positive amount in rands, e.g. 2500.00."
        raw_date = (args.get("date") or "").strip()
        try:
            date.fromisoformat(raw_date)
        except ValueError:
            return None, "Enter a valid date (YYYY-MM-DD)."
        scenario["amount_cents"] = cents
        scenario["date_iso"] = raw_date
    return scenario, None


@forecast_bp.route("/forecast")
def forecast_page():
    try:
        days = int(request.args.get("days", "30"))
    except ValueError:
        days = 30
    if days not in FORECAST_DAY_OPTIONS:
        days = 30

    scenario_type = request.args.get("scenario", "none")
    if scenario_type not in SCENARIO_TYPES:
        scenario_type = "none"
    scenario, scenario_error = _parse_scenario(scenario_type, request.args)

    conn = db.get_db()
    try:
        entries = build_cash_forecast(conn, today(), days)
        scenario_entries = None
        summary = None
        if scenario is not None:
            scenario_entries = apply_scenario(conn, entries, scenario, today())
            summary = summary_sentence(entries, scenario_entries)
        customers = conn.execute("SELECT id, name FROM customers ORDER BY name").fetchall()
    finally:
        conn.close()

    return render_template(
        "forecast.html",
        days=days,
        day_options=FORECAST_DAY_OPTIONS,
        weeks=weekly_summary(entries),
        lowest=lowest_balance(entries),
        negative_date=first_negative_date(entries),
        chart_labels=[e["date"] for e in entries],
        chart_balances=[e["running_balance_cents"] / 100 for e in entries],
        chart_zero=[0 for _ in entries],
        chart_scenario_balances=(
            [e["running_balance_cents"] / 100 for e in scenario_entries] if scenario_entries else []
        ),
        scenario_options=SCENARIO_OPTIONS,
        scenario_type=scenario_type,
        scenario_error=scenario_error,
        summary=summary,
        customers=customers,
        form={
            "customer_id": request.args.get("customer_id", ""),
            "scenario_days": request.args.get("scenario_days", ""),
            "amount": request.args.get("amount", ""),
            "date": request.args.get("date", ""),
        },
    )
