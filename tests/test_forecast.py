from datetime import date, datetime, timedelta

import pytest

import db
from services import (
    average_days_late,
    build_cash_forecast,
    expected_pay_date,
    first_negative_date,
    lowest_balance,
    unpaid_invoices,
    weekly_summary,
)


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKOFFICE_DB", str(tmp_path / "test.db"))
    db.init_db()
    connection = db.get_db()
    yield connection
    connection.close()


@pytest.fixture
def dataset(conn):
    """One customer (terms 14), one paid invoice paid 5 days late, one open invoice due in
    10 days, one open invoice 45 days overdue, two recurring bills, opening balance 0."""
    today = date.today()
    now = datetime.now().isoformat(timespec="seconds")
    cid = conn.execute(
        "INSERT INTO customers (name, payment_terms_days, created_at) VALUES ('Acme', 14, ?)",
        (now,),
    ).lastrowid

    def add_invoice(number, due, amount_cents):
        return conn.execute(
            """
            INSERT INTO invoices (customer_id, invoice_number, issue_date, due_date,
                                  amount_cents, status, created_at)
            VALUES (?, ?, ?, ?, ?, 'sent', ?)
            """,
            (cid, number, (due - timedelta(days=14)).isoformat(), due.isoformat(), amount_cents, now),
        ).lastrowid

    paid_id = add_invoice("INV-0001", today - timedelta(days=40), 50000)
    conn.execute(
        "INSERT INTO payments (invoice_id, amount_cents, paid_date, method, created_at) "
        "VALUES (?, 50000, ?, 'bank transfer', ?)",
        (paid_id, (today - timedelta(days=35)).isoformat(), now),
    )
    add_invoice("INV-0002", today + timedelta(days=10), 100000)
    add_invoice("INV-0003", today - timedelta(days=45), 20000)

    conn.execute(
        "INSERT INTO recurring_bills (name, amount_cents, due_day_of_month, category, active) "
        "VALUES ('Rent', 80000, 28, 'rent', 1)"
    )
    conn.execute(
        "INSERT INTO recurring_bills (name, amount_cents, due_day_of_month, category, active) "
        "VALUES ('Supplier', 30000, 15, 'supplier', 1)"
    )
    conn.commit()
    return {"customer_id": cid, "today": today}


def test_average_days_late_is_five(conn, dataset):
    assert average_days_late(conn, dataset["customer_id"]) == 5


def test_average_days_late_no_history_is_zero(conn, dataset):
    assert average_days_late(conn, 999) == 0


def test_expected_pay_date_overdue_invoice_is_uncertain(conn, dataset):
    today = dataset["today"]
    invoice = next(i for i in unpaid_invoices(conn) if i["invoice_number"] == "INV-0003")
    result = expected_pay_date(invoice, 5, today.isoformat())
    assert result == ((today + timedelta(days=14)).isoformat(), True)


def test_expected_pay_date_open_invoice_uses_average_late(conn, dataset):
    today = dataset["today"]
    invoice = next(i for i in unpaid_invoices(conn) if i["invoice_number"] == "INV-0002")
    result = expected_pay_date(invoice, 5, today.isoformat())
    assert result == ((today + timedelta(days=15)).isoformat(), False)


def test_build_cash_forecast_returns_30_entries(conn, dataset):
    entries = build_cash_forecast(conn, dataset["today"].isoformat(), 30)
    assert len(entries) == 30
    assert entries[0]["date"] == dataset["today"].isoformat()


def test_build_cash_forecast_rejects_other_lengths(conn, dataset):
    with pytest.raises(ValueError):
        build_cash_forecast(conn, dataset["today"].isoformat(), 45)


def test_bill_outflow_on_due_day_equals_amount(conn, dataset):
    # 60 consecutive days always contain every day of the month, so both bills occur.
    entries = build_cash_forecast(conn, dataset["today"].isoformat(), 60)
    seen_rent = seen_supplier = False
    for e in entries:
        day = date.fromisoformat(e["date"]).day
        expected = (80000 if day == 28 else 0) + (30000 if day == 15 else 0)
        assert e["expected_outflows_cents"] == expected
        seen_rent = seen_rent or day == 28
        seen_supplier = seen_supplier or day == 15
    assert seen_rent and seen_supplier


def test_inflows_and_uncertain_inflows(conn, dataset):
    entries = build_cash_forecast(conn, dataset["today"].isoformat(), 30)
    assert sum(e["expected_inflows_cents"] for e in entries) == 120000
    assert sum(e["uncertain_inflows_cents"] for e in entries) == 20000


def test_lowest_balance_and_first_negative_date(conn):
    entries = [
        {"date": "2030-01-01", "running_balance_cents": 500},
        {"date": "2030-01-02", "running_balance_cents": -200},
        {"date": "2030-01-03", "running_balance_cents": -900},
        {"date": "2030-01-04", "running_balance_cents": -900},
        {"date": "2030-01-05", "running_balance_cents": 100},
    ]
    assert lowest_balance(entries) == (-900, "2030-01-03")
    assert first_negative_date(entries) == "2030-01-02"


def test_no_negative_date_when_balance_stays_positive():
    entries = [
        {"date": "2030-01-01", "running_balance_cents": 500},
        {"date": "2030-01-02", "running_balance_cents": 0},
    ]
    assert first_negative_date(entries) is None


def test_weekly_summary_matches_daily_entries(conn, dataset):
    entries = build_cash_forecast(conn, dataset["today"].isoformat(), 30)
    weeks = weekly_summary(entries)
    assert len(weeks) == 5  # 7 + 7 + 7 + 7 + 2 days
    assert weeks[0]["week_start"] == entries[0]["date"]
    for i, week in enumerate(weeks):
        block = entries[i * 7:(i + 1) * 7]
        assert week["week_start"] == block[0]["date"]
        assert week["closing_balance_cents"] == block[-1]["running_balance_cents"]
        assert week["inflows_cents"] == sum(e["expected_inflows_cents"] for e in block)
        assert week["outflows_cents"] == sum(e["expected_outflows_cents"] for e in block)


def test_forecast_page_renders_chart(dataset):
    from app import create_app

    client = create_app().test_client()
    response = client.get("/forecast?days=60")
    assert response.status_code == 200
    assert b"Chart" in response.data
