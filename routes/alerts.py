from flask import Blueprint, render_template

import db
from services import generate_alerts, sort_alerts, today

alerts_bp = Blueprint("alerts", __name__)


@alerts_bp.route("/alerts")
def show_alerts():
    conn = db.get_db()
    try:
        alerts = sort_alerts(generate_alerts(conn, today()))
    finally:
        conn.close()
    return render_template("alerts.html", alerts=alerts)
