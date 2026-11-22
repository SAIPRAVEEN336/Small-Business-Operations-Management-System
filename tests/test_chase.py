import db


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("BACKOFFICE_DB", str(tmp_path / "chase.db"))
    db.init_db()
    from app import create_app

    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def _seed(customer_name="Thandi Nkosi"):
    conn = db.get_db()
    try:
        cur = conn.execute(
            "INSERT INTO customers (name, phone, payment_terms_days, created_at) "
            "VALUES (?, '072 123 4567', 14, '2024-01-01')",
            (customer_name,),
        )
        customer_id = cur.lastrowid
        open_ids = []
        for number in ("INV-0001", "INV-0002"):
            cur = conn.execute(
                """
                INSERT INTO invoices (customer_id, invoice_number, issue_date, due_date,
                                      amount_cents, status, created_at)
                VALUES (?, ?, '2024-01-01', '2024-01-15', 50000, 'sent', '2024-01-01')
                """,
                (customer_id, number),
            )
            open_ids.append(cur.lastrowid)
        # A paid invoice must not receive a follow-up row.
        cur = conn.execute(
            """
            INSERT INTO invoices (customer_id, invoice_number, issue_date, due_date,
                                  amount_cents, status, created_at)
            VALUES (?, 'INV-0003', '2024-01-01', '2024-01-15', 10000, 'sent', '2024-01-01')
            """,
            (customer_id,),
        )
        conn.execute(
            "INSERT INTO payments (invoice_id, amount_cents, paid_date, created_at) "
            "VALUES (?, 10000, '2024-01-10', '2024-01-10')",
            (cur.lastrowid,),
        )
        conn.commit()
    finally:
        conn.close()
    return customer_id


def test_chase_page_shows_first_name(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    customer_id = _seed()
    response = client.get(f"/customers/{customer_id}/chase")
    assert response.status_code == 200
    assert b"Thandi" in response.data
    assert b"wa.me/0721234567" in response.data


def test_chase_page_404_for_missing_customer(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    assert client.get("/customers/999/chase").status_code == 404


def test_post_followup_creates_one_row_per_open_invoice(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    customer_id = _seed()
    response = client.post(
        f"/customers/{customer_id}/followups",
        data={"message_text": "Hi Thandi, please pay.", "channel": "whatsapp"},
    )
    assert response.status_code == 302
    conn = db.get_db()
    try:
        rows = conn.execute("SELECT invoice_id, channel, message_text, sent_at FROM followups").fetchall()
    finally:
        conn.close()
    assert len(rows) == 2
    assert {r["channel"] for r in rows} == {"whatsapp"}
    assert all(r["message_text"] == "Hi Thandi, please pay." for r in rows)
    assert all(r["sent_at"] for r in rows)


def test_post_followup_rejects_empty_message(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    customer_id = _seed()
    response = client.post(
        f"/customers/{customer_id}/followups",
        data={"message_text": "   ", "channel": "copy"},
    )
    assert response.status_code == 302
    conn = db.get_db()
    try:
        count = conn.execute("SELECT COUNT(*) AS n FROM followups").fetchone()["n"]
    finally:
        conn.close()
    assert count == 0
