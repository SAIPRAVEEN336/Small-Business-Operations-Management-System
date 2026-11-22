import csv
import io
from datetime import datetime

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, url_for

import db

customers_bp = Blueprint("customers", __name__, url_prefix="/customers")

DEFAULT_TERMS_DAYS = 14


def _blank_form():
    return {
        "name": "",
        "phone": "",
        "email": "",
        "payment_terms_days": str(DEFAULT_TERMS_DAYS),
        "notes": "",
    }


def _read_form():
    return {
        "name": request.form.get("name", "").strip(),
        "phone": request.form.get("phone", "").strip(),
        "email": request.form.get("email", "").strip(),
        "payment_terms_days": request.form.get("payment_terms_days", "").strip(),
        "notes": request.form.get("notes", "").strip(),
    }


def _validate(form):
    """Return (error, terms_days). error is None when the form is valid."""
    if not form["name"]:
        return "Name is required.", None
    raw = form["payment_terms_days"] or str(DEFAULT_TERMS_DAYS)
    try:
        days = int(raw)
    except ValueError:
        days = -1
    if days < 0:
        return "Payment terms must be a whole number of days (0 or more).", None
    return None, days


@customers_bp.route("")
def list_customers():
    conn = db.get_db()
    try:
        customers = conn.execute(
            """
            SELECT c.id, c.name, c.phone,
                (SELECT COUNT(*) FROM invoices i
                  WHERE i.customer_id = c.id
                    AND i.status = 'sent'
                    AND i.amount_cents
                        - (SELECT COALESCE(SUM(p.amount_cents), 0)
                             FROM payments p WHERE p.invoice_id = i.id) > 0
                ) AS open_count,
                (SELECT COALESCE(SUM(i.amount_cents
                        - (SELECT COALESCE(SUM(p.amount_cents), 0)
                             FROM payments p WHERE p.invoice_id = i.id)), 0)
                   FROM invoices i
                  WHERE i.customer_id = c.id
                    AND i.status != 'void'
                ) AS outstanding_cents
            FROM customers c
            ORDER BY c.name
            """
        ).fetchall()
    finally:
        conn.close()
    return render_template("customers_list.html", customers=customers)


@customers_bp.route("/new", methods=["GET", "POST"])
def new_customer():
    form = _blank_form()
    error = None
    if request.method == "POST":
        form = _read_form()
        error, days = _validate(form)
        if error is None:
            conn = db.get_db()
            try:
                conn.execute(
                    """
                    INSERT INTO customers (name, phone, email, payment_terms_days, notes, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        form["name"],
                        form["phone"] or None,
                        form["email"] or None,
                        days,
                        form["notes"] or None,
                        datetime.now().isoformat(timespec="seconds"),
                    ),
                )
                conn.commit()
            finally:
                conn.close()
            return redirect(url_for("customers.list_customers"))
    return render_template(
        "customer_form.html",
        form=form,
        error=error,
        title="New customer",
        action=url_for("customers.new_customer"),
    )


@customers_bp.route("/<int:customer_id>/edit", methods=["GET", "POST"])
def edit_customer(customer_id):
    conn = db.get_db()
    try:
        row = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
        if row is None:
            abort(404)
        form = {
            "name": row["name"],
            "phone": row["phone"] or "",
            "email": row["email"] or "",
            "payment_terms_days": str(row["payment_terms_days"]),
            "notes": row["notes"] or "",
        }
        error = None
        if request.method == "POST":
            form = _read_form()
            error, days = _validate(form)
            if error is None:
                conn.execute(
                    """
                    UPDATE customers
                    SET name = ?, phone = ?, email = ?, payment_terms_days = ?, notes = ?
                    WHERE id = ?
                    """,
                    (
                        form["name"],
                        form["phone"] or None,
                        form["email"] or None,
                        days,
                        form["notes"] or None,
                        customer_id,
                    ),
                )
                conn.commit()
                return redirect(url_for("customers.list_customers"))
    finally:
        conn.close()
    return render_template(
        "customer_form.html",
        form=form,
        error=error,
        title="Edit customer",
        action=url_for("customers.edit_customer", customer_id=customer_id),
    )


TEMPLATE_CSV = "name,phone,email,payment_terms_days,notes\nSipho Dlamini,082 555 0101,sipho@example.com,30,Pays by EFT\n"


@customers_bp.route("/import/template")
def import_template():
    return Response(
        TEMPLATE_CSV,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=customers_template.csv"},
    )


@customers_bp.route("/import", methods=["GET", "POST"])
def import_customers():
    if request.method == "GET":
        return render_template("customer_import.html")
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        flash("Choose a CSV file to import.", "error")
        return redirect(url_for("customers.import_customers"))
    text = upload.read().decode("utf-8-sig")
    imported = 0
    skipped = 0
    conn = db.get_db()
    try:
        for row in csv.DictReader(io.StringIO(text)):
            name = (row.get("name") or "").strip()
            if not name:
                skipped += 1
                continue
            try:
                days = int((row.get("payment_terms_days") or "").strip())
            except ValueError:
                days = DEFAULT_TERMS_DAYS
            if days < 0:
                days = DEFAULT_TERMS_DAYS
            conn.execute(
                """
                INSERT INTO customers (name, phone, email, payment_terms_days, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    (row.get("phone") or "").strip() or None,
                    (row.get("email") or "").strip() or None,
                    days,
                    (row.get("notes") or "").strip() or None,
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            imported += 1
        conn.commit()
    finally:
        conn.close()
    flash(f"Imported {imported} customers, skipped {skipped}.", "message")
    return redirect(url_for("customers.list_customers"))
