import db
from services import parse_quick_entry


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("BACKOFFICE_DB", str(tmp_path / "quick.db"))
    db.init_db()
    from app import create_app

    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def test_parse_name_amount_and_eft():
    result = parse_quick_entry("Sipho paid 500 by EFT")
    assert result["customer_name"] == "Sipho"
    assert result["amount_cents"] == 50000
    assert result["method"] == "bank transfer"
    assert result["recognised"] is True


def test_parse_unrecognised_text():
    assert parse_quick_entry("hello")["recognised"] is False


def test_parse_spaced_thousands_and_cash():
    result = parse_quick_entry("Thandi paid R 1 500.50 cash")
    assert result["amount_cents"] == 150050
    assert result["method"] == "cash"
    assert result["customer_name"] == "Thandi"


def test_post_quick_add_records_payment(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    conn = db.get_db()
    try:
        cur = conn.execute(
            "INSERT INTO customers (name, phone, payment_terms_days, created_at) "
            "VALUES ('Sipho', '', 14, '2024-01-01')"
        )
        conn.execute(
            """
            INSERT INTO invoices (customer_id, invoice_number, issue_date, due_date,
                                  amount_cents, status, created_at)
            VALUES (?, 'INV-0001', '2024-01-01', '2024-01-15', 100000, 'sent', '2024-01-01')
            """,
            (cur.lastrowid,),
        )
        conn.commit()
    finally:
        conn.close()

    response = client.post("/quick-add", data={"text": "Sipho paid 500 by EFT"})
    assert response.status_code == 302

    conn = db.get_db()
    try:
        rows = conn.execute("SELECT amount_cents, method FROM payments").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0]["amount_cents"] == 50000
    assert rows[0]["method"] == "bank transfer"
