from flask import Blueprint, render_template

import db
from services import dashboard_summary, outstanding_by_customer, today

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/dashboard")
def show_dashboard():
    today_iso = today()
    conn = db.get_db()
    try:
        summary = dashboard_summary(conn, today_iso)
        customers = outstanding_by_customer(conn, today_iso)
    finally:
        conn.close()
    return render_template("dashboard.html", summary=summary, customers=customers)
