from flask import Blueprint, flash, redirect, render_template, request, url_for

import db
from services import today

settings_bp = Blueprint("settings", __name__)

DEMO_TABLES = ("payments", "followups", "invoices", "customers", "recurring_bills", "cash_settings")


@settings_bp.route("/settings")
def show_settings():
    return render_template("settings.html")


@settings_bp.route("/settings/reset-demo", methods=["POST"])
def reset_demo():
    if request.form.get("confirm") != "yes":
        flash("Tick the box to confirm.", "error")
        return redirect(url_for("settings.show_settings"))

    conn = db.get_db()
    try:
        # Child tables first so foreign keys are not violated.
        for table in DEMO_TABLES:
            conn.execute(f"DELETE FROM {table}")
        db.seed_demo(conn, today())  # commits the whole reset
    finally:
        conn.close()

    flash("Demo data reset.", "message")
    return redirect(url_for("settings.show_settings"))
