from datetime import date, timedelta

import pytest


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Point the app at a throwaway database before anything imports or initialises it.
    monkeypatch.setenv("BACKOFFICE_DB", str(tmp_path / "e2e.db"))
    import db
    from app import create_app

    db.init_db()
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def test_invoice_to_payment_flow(client):
    today_iso = date.today().isoformat()
    issue_iso = (date.today() - timedelta(days=20)).isoformat()

    # 1. Create a customer (routes/customers.py: POST /customers/new).
    resp = client.post(
        "/customers/new",
        data={"name": "Amara Test", "payment_terms_days": "14"},
    )
    assert resp.status_code == 302

    # 2. Create an invoice for that customer (routes/invoices.py: POST /invoices/new).
    resp = client.post(
        "/invoices/new",
        data={"customer_id": "1", "amount": "1000.00", "issue_date": issue_iso},
    )
    assert resp.status_code == 302

    # 3. The dashboard lists the customer as owing money.
    resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert "Amara Test" in resp.get_data(as_text=True)

    # 4. Chase page for the customer (routes/chase.py: GET /customers/<id>/chase).
    resp = client.get("/customers/1/chase")
    assert resp.status_code == 200
    assert "Amara" in resp.get_data(as_text=True)

    # 5. Log a follow-up (routes/chase.py: POST /customers/<id>/followups).
    resp = client.post(
        "/customers/1/followups",
        data={"message_text": "Hello", "channel": "whatsapp"},
    )
    assert resp.status_code == 302

    # 6. Part payment (routes/invoices.py: POST /invoices/<id>/payments).
    resp = client.post(
        "/invoices/1/payments",
        data={"amount": "400.00", "paid_date": today_iso, "method": "cash"},
    )
    assert resp.status_code == 302

    resp = client.get("/invoices/1")
    assert resp.status_code == 200
    assert "600.00" in resp.get_data(as_text=True)

    # 7. Settle the balance.
    resp = client.post(
        "/invoices/1/payments",
        data={"amount": "600.00", "paid_date": today_iso, "method": "cash"},
    )
    assert resp.status_code == 302

    resp = client.get("/invoices/1")
    assert resp.status_code == 200
    assert "paid" in resp.get_data(as_text=True)

    # 8. Once fully paid, the customer no longer appears as owing on the dashboard.
    resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert "Amara Test" not in resp.get_data(as_text=True)
