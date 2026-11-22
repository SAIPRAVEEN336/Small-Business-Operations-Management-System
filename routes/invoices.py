from datetime import date, datetime, timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

import db
from services import (
    PAYMENT_METHODS,
    add_payment,
    compute_status,
    days_overdue,
    next_invoice_number,
    parse_cents,
    today,
)

invoices_bp = Blueprint("invoices", __name__, url_prefix="/invoices")

FILTER_TABS = (("all", "All"), ("open", "Open"), ("overdue", "Overdue"), ("paid", "Paid"))

INVOICE_SELECT = """
    SELECT i.id, i.customer_id, i.invoice_number, i.issue_date, i.due_date,
           i.amount_cents, i.status, c.name AS customer_name,
           COALESCE(SUM(p.amount_cents), 0) AS paid_cents
    FROM invoices i
    JOIN customers c ON c.id = i.customer_id
    LEFT JOIN payments p ON p.invoice_id = i.id
"""


def _parse_iso_date(text):
    try:
        return date.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def _enrich(row, today_iso):
    """Turn a joined invoice row into a dict with balance, derived status and days overdue."""
    invoice = dict(row)
    invoice["balance_cents"] = invoice["amount_cents"] - invoice["paid_cents"]
    invoice["status_derived"] = compute_status(row, invoice["balance_cents"], today_iso)
    invoice["overdue_days"] = days_overdue(invoice["due_date"], today_iso)
    return invoice


def _in_filter(invoice, active_filter):
    status = invoice["status_derived"]
    if active_filter == "open":
        # Unpaid and not void, which includes overdue invoices.
        return status in ("open", "overdue")
    if active_filter == "overdue":
        return status == "overdue"
    if active_filter == "paid":
        return status == "paid"
    return True


@invoices_bp.route("")
def list_invoices():
    active_filter = request.args.get("filter", "all")
    if active_filter not in dict(FILTER_TABS):
        active_filter = "all"
    today_iso = today()
    conn = db.get_db()
    try:
        rows = conn.execute(INVOICE_SELECT + " GROUP BY i.id ORDER BY i.id DESC").fetchall()
    finally:
        conn.close()
    all_invoices = [_enrich(r, today_iso) for r in rows]
    invoices = [inv for inv in all_invoices if _in_filter(inv, active_filter)]
    return render_template(
        "invoices_list.html",
        invoices=invoices,
        has_any=bool(all_invoices),
        active_filter=active_filter,
        filter_tabs=FILTER_TABS,
    )


@invoices_bp.route("/new", methods=["GET", "POST"])
def new_invoice():
    conn = db.get_db()
    try:
        customers = conn.execute(
            "SELECT id, name, payment_terms_days FROM customers ORDER BY name"
        ).fetchall()
        form = {"customer_id": "", "amount": "", "issue_date": today(), "description": ""}
        error = None
        if request.method == "POST":
            form = {
                "customer_id": request.form.get("customer_id", "").strip(),
                "amount": request.form.get("amount", "").strip(),
                "issue_date": request.form.get("issue_date", "").strip() or today(),
                "description": request.form.get("description", "").strip(),
            }
            customer = None
            if not form["customer_id"]:
                error = "Select a customer."
            else:
                customer = conn.execute(
                    "SELECT id, payment_terms_days FROM customers WHERE id = ?",
                    (form["customer_id"],),
                ).fetchone()
                if customer is None:
                    error = "Select a customer."

            amount_cents = None
            if error is None:
                amount_cents = parse_cents(form["amount"])
                if amount_cents is None:
                    error = "Enter a valid amount."
                elif amount_cents <= 0:
                    error = "Amount must be greater than zero."

            issue = None
            if error is None:
                issue = _parse_iso_date(form["issue_date"])
                if issue is None:
                    error = "Enter a valid issue date."

            if error is None:
                # Server-side due date calculation is authoritative; the form preview is only a hint.
                due = issue + timedelta(days=customer["payment_terms_days"])
                cur = conn.execute(
                    """
                    INSERT INTO invoices
                        (customer_id, invoice_number, issue_date, due_date,
                         amount_cents, status, created_at)
                    VALUES (?, ?, ?, ?, ?, 'sent', ?)
                    """,
                    (
                        customer["id"],
                        next_invoice_number(conn),
                        issue.isoformat(),
                        due.isoformat(),
                        amount_cents,
                        datetime.now().isoformat(timespec="seconds"),
                    ),
                )
                conn.commit()
                return redirect(url_for("invoices.invoice_detail", invoice_id=cur.lastrowid))
    finally:
        conn.close()
    return render_template(
        "invoice_new.html",
        customers=customers,
        form=form,
        error=error,
    )


@invoices_bp.route("/<int:invoice_id>")
def invoice_detail(invoice_id):
    conn = db.get_db()
    try:
        row = conn.execute(INVOICE_SELECT + " WHERE i.id = ? GROUP BY i.id", (invoice_id,)).fetchone()
        if row is None:
            abort(404)
        invoice = _enrich(row, today())
        payments = conn.execute(
            """
            SELECT id, amount_cents, paid_date, method
            FROM payments WHERE invoice_id = ?
            ORDER BY paid_date, id
            """,
            (invoice_id,),
        ).fetchall()
        followups = conn.execute(
            """
            SELECT id, channel, message_text, sent_at, created_at
            FROM followups WHERE invoice_id = ?
            ORDER BY id DESC
            """,
            (invoice_id,),
        ).fetchall()
    finally:
        conn.close()
    return render_template(
        "invoice_detail.html",
        invoice=invoice,
        payments=payments,
        followups=followups,
        methods=PAYMENT_METHODS,
        today=today(),
    )


@invoices_bp.route("/<int:invoice_id>/payments", methods=["POST"])
def record_payment(invoice_id):
    conn = db.get_db()
    try:
        invoice = conn.execute("SELECT id FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
        if invoice is None:
            abort(404)
        ok, message = add_payment(
            conn,
            invoice_id,
            parse_cents(request.form.get("amount", "")),
            request.form.get("paid_date", "").strip() or today(),
            request.form.get("method", ""),
        )
    finally:
        conn.close()
    flash(message, "message" if ok else "error")
    return redirect(url_for("invoices.invoice_detail", invoice_id=invoice_id))


@invoices_bp.route("/<int:invoice_id>/void", methods=["POST"])
def void_invoice(invoice_id):
    conn = db.get_db()
    try:
        cur = conn.execute("UPDATE invoices SET status = 'void' WHERE id = ?", (invoice_id,))
        if cur.rowcount == 0:
            abort(404)
        conn.commit()
    finally:
        conn.close()
    flash("Invoice voided.", "message")
    return redirect(url_for("invoices.invoice_detail", invoice_id=invoice_id))
