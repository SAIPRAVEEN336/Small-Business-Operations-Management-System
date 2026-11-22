from datetime import date, timedelta

from flask import Blueprint, render_template, session

import db
from routes.quick import find_customer_id
from services import (
    PAYMENT_METHODS,
    build_cash_forecast,
    format_rands,
    generate_alerts,
    sort_alerts,
    today,
    unpaid_invoices,
)

main_bp = Blueprint("main", __name__)

ATTENTION_HORIZON_DAYS = 3


@main_bp.route("/")
def index():
    today_iso = today()
    horizon_iso = (date.fromisoformat(today_iso) + timedelta(days=ATTENTION_HORIZON_DAYS)).isoformat()
    quick_draft = session.pop("quick_draft", None)
    draft_customer_id = None
    draft_invoices = []
    conn = db.get_db()
    try:
        alerts = sort_alerts(generate_alerts(conn, today_iso))
        week = build_cash_forecast(conn, today_iso, 30)[:7]
        # Overdue invoices and invoices due within the horizon both need attention.
        attention_count = sum(
            1 for inv in unpaid_invoices(conn) if inv["due_date"] <= horizon_iso
        )
        if quick_draft:
            draft_customer_id = find_customer_id(conn, quick_draft.get("customer_name"))
            if draft_customer_id is not None:
                draft_invoices = [
                    inv for inv in unpaid_invoices(conn) if inv["customer_id"] == draft_customer_id
                ]
    finally:
        conn.close()

    inflows = sum(e["expected_inflows_cents"] for e in week)
    outflows = sum(e["expected_outflows_cents"] for e in week)
    closing = week[-1]["running_balance_cents"]
    cash_sentence = (
        f"Over the next 7 days you expect {format_rands(inflows)} in and "
        f"{format_rands(outflows)} out, with a closing balance of {format_rands(closing)}."
    )
    return render_template(
        "index.html",
        top_alerts=alerts[:3],
        cash_sentence=cash_sentence,
        attention_count=attention_count,
        quick_draft=quick_draft,
        draft_customer_id=draft_customer_id,
        draft_invoices=draft_invoices,
        methods=PAYMENT_METHODS,
    )
