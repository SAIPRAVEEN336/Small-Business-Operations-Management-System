from flask import Blueprint, render_template

import db
from services import (
    active_bills_total_cents,
    bills_by_category,
    dashboard_summary,
    monthly_series,
    outstanding_by_customer,
    recent_invoices,
    today,
    upcoming_bills,
)

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/dashboard")
def show_dashboard():
    today_iso = today()
    conn = db.get_db()
    try:
        summary = dashboard_summary(conn, today_iso)
        customers = outstanding_by_customer(conn, today_iso)
        monthly = monthly_series(conn, today_iso)
        bill_categories = bills_by_category(conn)
        monthly_bills_cents = active_bills_total_cents(conn)
        recent = recent_invoices(conn)
        bills = upcoming_bills(conn)
    finally:
        conn.close()
    return render_template(
        "dashboard.html",
        summary=summary,
        customers=customers,
        monthly=monthly,
        bill_categories=bill_categories,
        monthly_bills_cents=monthly_bills_cents,
        recent=recent,
        bills=bills,
    )

