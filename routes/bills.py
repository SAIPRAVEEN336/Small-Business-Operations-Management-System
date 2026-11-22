from datetime import datetime
from decimal import Decimal

from flask import Blueprint, abort, redirect, render_template, request, url_for

import db
from services import get_opening_balance_cents, parse_cents

bills_bp = Blueprint("bills", __name__, url_prefix="/bills")

BILL_CATEGORIES = ("rent", "salary", "supplier", "tax", "other")
BILL_SELECT = """
    SELECT id, name, amount_cents, due_day_of_month, category, active
    FROM recurring_bills
    ORDER BY due_day_of_month, id
"""


def _blank_form():
    return {"name": "", "amount": "", "due_day_of_month": "", "category": "rent", "active": True}


def _cents_to_text(cents):
    """Format cents as a plain decimal string for a form field, e.g. -1250 -> '-12.50'."""
    return f"{Decimal(int(cents or 0)) / 100:.2f}"


def _render_page(form, error=None, opening_error=None, opening_text=None):
    conn = db.get_db()
    try:
        bills = conn.execute(BILL_SELECT).fetchall()
        if opening_text is None:
            opening_text = _cents_to_text(get_opening_balance_cents(conn))
    finally:
        conn.close()
    return render_template(
        "bills.html",
        bills=bills,
        form=form,
        error=error,
        categories=BILL_CATEGORIES,
        opening_text=opening_text,
        opening_error=opening_error,
    )


@bills_bp.route("", methods=["GET", "POST"])
def bills_page():
    if request.method == "GET":
        return _render_page(_blank_form())

    form = {
        "name": request.form.get("name", "").strip(),
        "amount": request.form.get("amount", "").strip(),
        "due_day_of_month": request.form.get("due_day_of_month", "").strip(),
        "category": request.form.get("category", ""),
        "active": "active" in request.form,
    }
    error = None
    amount_cents = None
    due_day = None
    if not form["name"]:
        error = "Enter a name for the bill."
    if error is None:
        amount_cents = parse_cents(form["amount"])
        if amount_cents is None or amount_cents <= 0:
            error = "Enter an amount greater than zero."
    if error is None:
        try:
            due_day = int(form["due_day_of_month"])
        except ValueError:
            due_day = None
        if due_day is None or not 1 <= due_day <= 28:
            error = "Due day must be a whole number from 1 to 28."
    if error is None and form["category"] not in BILL_CATEGORIES:
        error = "Choose a category."

    if error is not None:
        return _render_page(form, error=error)

    conn = db.get_db()
    try:
        conn.execute(
            """
            INSERT INTO recurring_bills (name, amount_cents, due_day_of_month, category, active)
            VALUES (?, ?, ?, ?, ?)
            """,
            (form["name"], amount_cents, due_day, form["category"], 1 if form["active"] else 0),
        )
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for("bills.bills_page"))


@bills_bp.route("/<int:bill_id>/toggle", methods=["POST"])
def toggle_bill(bill_id):
    conn = db.get_db()
    try:
        cur = conn.execute("UPDATE recurring_bills SET active = 1 - active WHERE id = ?", (bill_id,))
        if cur.rowcount == 0:
            abort(404)
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for("bills.bills_page"))


@bills_bp.route("/opening-balance", methods=["POST"])
def save_opening_balance():
    text = request.form.get("amount", "").strip()
    amount_cents = parse_cents(text)
    if amount_cents is None:
        return _render_page(
            _blank_form(),
            opening_error="Enter a valid amount (negative amounts are allowed).",
            opening_text=text,
        )
    conn = db.get_db()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO cash_settings (id, opening_balance_cents, updated_at)
            VALUES (1, ?, ?)
            """,
            (amount_cents, datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for("bills.bills_page"))
