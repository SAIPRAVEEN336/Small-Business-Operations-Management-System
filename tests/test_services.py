import db
from services import (
    dashboard_summary,
    draft_reminder,
    format_rands,
    outstanding_by_customer,
    pick_tone,
)

TODAY = "2024-06-15"


def _use_temp_db(monkeypatch, tmp_path):
    monkeypatch.setenv("BACKOFFICE_DB", str(tmp_path / "test.db"))
    db.init_db()
    return db.get_db()


def _customer(conn, name):
    cur = conn.execute(
        "INSERT INTO customers (name, payment_terms_days, created_at) VALUES (?, 14, '2024-01-01')",
        (name,),
    )
    return cur.lastrowid


def _invoice(conn, customer_id, number, amount_cents, due_date, status="sent"):
    cur = conn.execute(
        """
        INSERT INTO invoices (customer_id, invoice_number, issue_date, due_date,
                              amount_cents, status, created_at)
        VALUES (?, ?, '2024-01-01', ?, ?, ?, '2024-01-01')
        """,
        (customer_id, number, due_date, amount_cents, status),
    )
    return cur.lastrowid


def _payment(conn, invoice_id, amount_cents, paid_date):
    conn.execute(
        """
        INSERT INTO payments (invoice_id, amount_cents, paid_date, method, created_at)
        VALUES (?, ?, ?, 'cash', ?)
        """,
        (invoice_id, amount_cents, paid_date, paid_date),
    )


def test_pick_tone_boundaries():
    assert pick_tone(0) == "friendly"
    assert pick_tone(7) == "friendly"
    assert pick_tone(8) == "firm"
    assert pick_tone(20) == "firm"
    assert pick_tone(21) == "final"


def test_outstanding_by_customer_sort_and_totals(monkeypatch, tmp_path):
    conn = _use_temp_db(monkeypatch, tmp_path)
    try:
        ann = _customer(conn, "Ann")
        bob = _customer(conn, "Bob")
        cy = _customer(conn, "Cy")
        dee = _customer(conn, "Dee")

        # Ann: 60000 overdue (due 2024-06-01) + 50000 not yet due (due 2024-06-20).
        a1 = _invoice(conn, ann, "INV-A1", 100000, "2024-06-01")
        _payment(conn, a1, 40000, "2024-06-03")
        _invoice(conn, ann, "INV-A2", 50000, "2024-06-20")
        # Bob: fully paid, so excluded.
        b1 = _invoice(conn, bob, "INV-B1", 30000, "2024-05-01")
        _payment(conn, b1, 30000, "2024-05-20")
        # Cy: void, so excluded.
        _invoice(conn, cy, "INV-C1", 80000, "2024-06-10", status="void")
        # Dee: 20000 overdue (due 2024-06-05).
        _invoice(conn, dee, "INV-D1", 20000, "2024-06-05")
        conn.commit()

        rows = outstanding_by_customer(conn, TODAY)
    finally:
        conn.close()

    assert [r["name"] for r in rows] == ["Ann", "Dee"]
    ann_row, dee_row = rows
    assert ann_row["total_owed_cents"] == 110000
    assert ann_row["oldest_due_date"] == "2024-06-01"
    assert ann_row["days_overdue"] == 14
    assert ann_row["invoice_count"] == 2
    assert ann_row["last_chased"] is None
    assert dee_row["total_owed_cents"] == 20000
    assert dee_row["days_overdue"] == 10
    assert dee_row["invoice_count"] == 1


def test_dashboard_summary_overdue_cents(monkeypatch, tmp_path):
    conn = _use_temp_db(monkeypatch, tmp_path)
    try:
        ann = _customer(conn, "Ann")
        dee = _customer(conn, "Dee")
        a1 = _invoice(conn, ann, "INV-A1", 100000, "2024-06-01")
        _payment(conn, a1, 40000, "2024-06-03")
        _invoice(conn, ann, "INV-A2", 50000, "2024-06-20")  # not yet due
        _invoice(conn, dee, "INV-D1", 20000, "2024-06-05")  # overdue
        conn.commit()

        summary = dashboard_summary(conn, TODAY)
    finally:
        conn.close()

    assert summary["total_outstanding_cents"] == 130000
    assert summary["overdue_cents"] == 80000  # 60000 on Ann's A1 + 20000 on Dee's D1
    assert summary["customers_owing"] == 2
    assert summary["received_this_month_cents"] == 40000


def test_draft_reminder_contains_number_and_amount():
    message = draft_reminder("Ann", ["INV-0001"], 110000, "friendly")
    assert "Ann" in message
    assert "INV-0001" in message
    assert format_rands(110000) in message
    assert "Thank you" in message
