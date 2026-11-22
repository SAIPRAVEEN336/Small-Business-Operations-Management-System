import os
import tempfile

# Point the database at a throwaway file before app.py is imported, because importing
# app.py runs create_app() at module level and would otherwise create backoffice.db.
os.environ["BACKOFFICE_DB"] = os.path.join(tempfile.mkdtemp(), "import-check.db")

import pytest  # noqa: E402

import db  # noqa: E402
from app import create_app  # noqa: E402
from services import balance_for_invoice, compute_status, today  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKOFFICE_DB", str(tmp_path / "test.db"))
    flask_app = create_app()
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as test_client:
        yield test_client


def _create_customer_and_invoice(client, amount):
    client.post("/customers/new", data={"name": "Acme", "payment_terms_days": "14"})
    client.post(
        "/invoices/new",
        data={"customer_id": "1", "amount": amount, "issue_date": today()},
    )
    # Fresh database: the first customer and first invoice get id 1.
    return 1


def _count_payments():
    conn = db.get_db()
    try:
        return conn.execute("SELECT COUNT(*) AS n FROM payments").fetchone()["n"]
    finally:
        conn.close()


def test_overpayment_is_rejected_and_not_recorded(client):
    invoice_id = _create_customer_and_invoice(client, "100.00")

    resp = client.post(
        f"/invoices/{invoice_id}/payments",
        data={"amount": "150.00", "paid_date": today(), "method": "cash"},
        follow_redirects=True,
    )

    assert b"Payment is more than the outstanding balance of R 100.00." in resp.data
    assert _count_payments() == 0


def test_payment_equal_to_balance_makes_invoice_paid(client):
    invoice_id = _create_customer_and_invoice(client, "250.00")

    client.post(
        f"/invoices/{invoice_id}/payments",
        data={"amount": "250.00", "paid_date": today(), "method": "bank transfer"},
    )

    conn = db.get_db()
    try:
        invoice = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
        balance = balance_for_invoice(conn, invoice_id)
        assert balance == 0
        assert compute_status(invoice, balance, today()) == "paid"
    finally:
        conn.close()


def test_void_invoice_is_excluded_from_outstanding_totals(client):
    invoice_id = _create_customer_and_invoice(client, "300.00")

    before = client.get("/customers")
    assert b"R 300.00" in before.data

    client.post(f"/invoices/{invoice_id}/void")

    after = client.get("/customers")
    assert b"R 300.00" not in after.data
    assert b"R 0.00" in after.data
