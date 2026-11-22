from flask import Blueprint, flash, redirect, request, session

import db
from services import add_payment, format_rands, parse_cents, parse_quick_entry, today

quick_bp = Blueprint("quick", __name__)


def find_customer_id(conn, name):
    """Return the id of the customer whose name matches exactly (case-insensitive), or None."""
    row = conn.execute(
        "SELECT id FROM customers WHERE LOWER(TRIM(name)) = LOWER(?) ORDER BY id LIMIT 1",
        ((name or "").strip(),),
    ).fetchone()
    return row["id"] if row else None


def _oldest_unpaid_invoice(conn, customer_id):
    """Return the customer's oldest non-void invoice with a positive balance, or None."""
    return conn.execute(
        """
        SELECT i.id, i.amount_cents - COALESCE(SUM(p.amount_cents), 0) AS balance
        FROM invoices i
        LEFT JOIN payments p ON p.invoice_id = i.id
        WHERE i.customer_id = ? AND i.status != 'void'
        GROUP BY i.id
        HAVING balance > 0
        ORDER BY i.issue_date, i.id
        LIMIT 1
        """,
        (customer_id,),
    ).fetchone()


@quick_bp.route("/quick-add", methods=["POST"])
def quick_add():
    parsed = parse_quick_entry(request.form.get("text", ""))
    conn = db.get_db()
    try:
        customer_id = None
        if parsed["recognised"]:
            customer_id = find_customer_id(conn, parsed["customer_name"])
        invoice = _oldest_unpaid_invoice(conn, customer_id) if customer_id is not None else None
        if invoice is not None:
            applied = min(parsed["amount_cents"], invoice["balance"])
            ok, message = add_payment(conn, invoice["id"], applied, today(), parsed["method"])
            if ok:
                flash(f"Recorded {format_rands(applied)} from {parsed['customer_name']}.", "message")
            else:
                flash(message, "error")
            return redirect("/")
    finally:
        conn.close()

    flash("Could not match that. Confirm the details below.", "error")
    session["quick_draft"] = {
        "customer_name": parsed["customer_name"],
        "amount_cents": parsed["amount_cents"],
        "method": parsed["method"],
    }
    return redirect("/")


@quick_bp.route("/record-payment", methods=["POST"])
def record_payment():
    customer_id = request.form.get("customer_id", "").strip()
    invoice_id = request.form.get("invoice_id", "").strip()
    amount_cents = parse_cents(request.form.get("amount", ""))
    method = request.form.get("method", "")

    if amount_cents is None or amount_cents <= 0:
        flash("Enter a valid amount greater than zero.", "error")
        return redirect("/")
    if not (customer_id.isdigit() and invoice_id.isdigit()):
        flash("Choose one of this customer's open invoices.", "error")
        return redirect("/")

    conn = db.get_db()
    try:
        invoice = conn.execute(
            "SELECT id FROM invoices WHERE id = ? AND customer_id = ?",
            (int(invoice_id), int(customer_id)),
        ).fetchone()
        if invoice is None:
            flash("Choose one of this customer's open invoices.", "error")
        else:
            ok, message = add_payment(conn, invoice["id"], amount_cents, today(), method)
            flash(message, "message" if ok else "error")
    finally:
        conn.close()
    return redirect("/")
