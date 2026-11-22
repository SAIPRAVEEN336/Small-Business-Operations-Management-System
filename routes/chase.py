import re
from datetime import datetime
from urllib.parse import quote

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

import db
from services import draft_reminder, outstanding_by_customer, pick_tone, today, unpaid_invoices

chase_bp = Blueprint("chase", __name__, url_prefix="/customers")

CHANNELS = ("whatsapp", "copy", "email", "other")


def _first_name(full_name):
    parts = (full_name or "").split()
    return parts[0] if parts else "there"


def _customer_or_404(conn, customer_id):
    customer = conn.execute(
        "SELECT id, name, phone FROM customers WHERE id = ?", (customer_id,)
    ).fetchone()
    if customer is None:
        abort(404)
    return customer


def _open_invoices_for(conn, customer_id):
    return [inv for inv in unpaid_invoices(conn) if inv["customer_id"] == customer_id]


@chase_bp.route("/<int:customer_id>/chase")
def chase_page(customer_id):
    today_iso = today()
    conn = db.get_db()
    try:
        customer = _customer_or_404(conn, customer_id)
        summary = next(
            (r for r in outstanding_by_customer(conn, today_iso) if r["customer_id"] == customer_id),
            None,
        )
        invoices = _open_invoices_for(conn, customer_id)
    finally:
        conn.close()

    oldest_days = summary["days_overdue"] if summary else 0
    total_owed = summary["total_owed_cents"] if summary else 0
    tone = pick_tone(oldest_days)
    message = draft_reminder(
        _first_name(customer["name"]),
        [inv["invoice_number"] for inv in invoices],
        total_owed,
        tone,
    )
    digits = re.sub(r"\D", "", customer["phone"] or "")
    wa_base = f"https://wa.me/{digits}" if digits else None
    wa_url = f"{wa_base}?text={quote(message, safe='')}" if wa_base else None
    return render_template(
        "chase.html",
        customer=customer,
        tone=tone,
        message=message,
        oldest_days=oldest_days,
        total_owed_cents=total_owed,
        has_open_invoices=bool(invoices),
        wa_base=wa_base,
        wa_url=wa_url,
        channels=CHANNELS,
    )


@chase_bp.route("/<int:customer_id>/followups", methods=["POST"])
def log_followup(customer_id):
    message = request.form.get("message_text", "").strip()
    channel = request.form.get("channel", "")
    if channel not in CHANNELS:
        channel = "other"
    conn = db.get_db()
    try:
        _customer_or_404(conn, customer_id)
        if not message:
            flash("Reminder message cannot be empty.", "error")
        else:
            invoices = _open_invoices_for(conn, customer_id)
            if not invoices:
                flash("This customer has no open invoices to log a reminder against.", "error")
            else:
                now = datetime.now().isoformat(timespec="seconds")
                conn.executemany(
                    """
                    INSERT INTO followups (invoice_id, channel, message_text, sent_at, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    [(inv["id"], channel, message, now, now) for inv in invoices],
                )
                conn.commit()
                flash("Reminder logged.", "message")
    finally:
        conn.close()
    return redirect(url_for("chase.chase_page", customer_id=customer_id))
