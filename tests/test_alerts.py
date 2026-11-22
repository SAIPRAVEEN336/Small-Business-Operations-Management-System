from datetime import date, timedelta

import pytest

import db
from services import count_alerts, generate_alerts, sort_alerts

TODAY = "2024-06-15"
TODAY_DATE = date(2024, 6, 15)


def days_before_today(n):
    return (TODAY_DATE - timedelta(days=n)).isoformat()


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "test.db"
    monkeypatch.setenv("BACKOFFICE_DB", str(path))
    db.init_db()
    return path


@pytest.fixture
def conn(db_path):
    connection = db.get_db()
    yield connection
    connection.close()


@pytest.fixture
def client(db_path):
    from app import create_app

    flask_app = create_app()
    flask_app.testing = True
    return flask_app.test_client()


def add_customer(conn, name):
    cur = conn.execute(
        "INSERT INTO customers (name, created_at) VALUES (?, '2024-01-01')", (name,)
    )
    conn.commit()
    return cur.lastrowid


def add_invoice(conn, customer_id, number, due_date, amount_cents):
    conn.execute(
        """
        INSERT INTO invoices (customer_id, invoice_number, issue_date, due_date,
                              amount_cents, status, created_at)
        VALUES (?, ?, '2024-01-01', ?, ?, 'sent', '2024-01-01')
        """,
        (customer_id, number, due_date, amount_cents),
    )
    conn.commit()


def add_bill(conn, name, amount_cents, due_day):
    conn.execute(
        "INSERT INTO recurring_bills (name, amount_cents, due_day_of_month, active) VALUES (?, ?, ?, 1)",
        (name, amount_cents, due_day),
    )
    conn.commit()


def set_opening(conn, cents):
    conn.execute(
        "INSERT OR REPLACE INTO cash_settings (id, opening_balance_cents) VALUES (1, ?)", (cents,)
    )
    conn.commit()


def titles(alerts):
    return [a["title"] for a in alerts]


def find(alerts, text):
    return [a for a in alerts if text in a["title"]]


# Rule a: overdue more than 7 days


def test_rule_a_seven_days_overdue_not_flagged(conn):
    cid = add_customer(conn, "Acme")
    add_invoice(conn, cid, "INV-0001", days_before_today(7), 10000)
    assert find(generate_alerts(conn, TODAY), "Chase Acme") == []


def test_rule_a_eight_days_overdue_flagged_medium(conn):
    cid = add_customer(conn, "Acme")
    add_invoice(conn, cid, "INV-0001", days_before_today(8), 10000)
    hits = find(generate_alerts(conn, TODAY), "Chase Acme today")
    assert len(hits) == 1
    assert hits[0]["severity"] == "medium"
    assert hits[0]["action_url"] == f"/customers/{cid}/chase"
    assert hits[0]["action_label"] == "Chase now"
    assert "INV-0001" in hits[0]["explanation"]
    assert "R 100.00" in hits[0]["explanation"]
    assert "8 days overdue" in hits[0]["explanation"]


def test_rule_a_twenty_one_days_overdue_still_medium(conn):
    cid = add_customer(conn, "Acme")
    add_invoice(conn, cid, "INV-0001", days_before_today(21), 10000)
    hits = find(generate_alerts(conn, TODAY), "Chase Acme today")
    assert hits[0]["severity"] == "medium"


def test_rule_a_twenty_two_days_overdue_high(conn):
    cid = add_customer(conn, "Acme")
    add_invoice(conn, cid, "INV-0001", days_before_today(22), 10000)
    hits = find(generate_alerts(conn, TODAY), "Chase Acme today")
    assert hits[0]["severity"] == "high"


def test_paid_invoice_not_flagged(conn):
    cid = add_customer(conn, "Acme")
    add_invoice(conn, cid, "INV-0001", days_before_today(30), 10000)
    inv_id = conn.execute("SELECT id FROM invoices").fetchone()["id"]
    conn.execute(
        "INSERT INTO payments (invoice_id, amount_cents, paid_date, created_at) VALUES (?, 10000, '2024-06-01', '2024-06-01')",
        (inv_id,),
    )
    conn.commit()
    assert find(generate_alerts(conn, TODAY), "Chase Acme") == []


# Rule b: 3 or more overdue invoices for one customer


def test_rule_b_three_overdue_invoices_flagged(conn):
    cid = add_customer(conn, "Bongi")
    for n in range(3):
        add_invoice(conn, cid, f"INV-000{n}", days_before_today(1), 5000)
    hits = find(generate_alerts(conn, TODAY), "pattern of late payment")
    assert len(hits) == 1
    assert hits[0]["title"] == "Call Bongi — pattern of late payment"
    assert hits[0]["severity"] == "medium"
    assert hits[0]["action_url"] == f"/customers/{cid}/edit"
    assert hits[0]["action_label"] == "Open customer"
    assert hits[0]["explanation"].startswith("3 ")


def test_rule_b_two_overdue_invoices_not_flagged(conn):
    cid = add_customer(conn, "Bongi")
    for n in range(2):
        add_invoice(conn, cid, f"INV-000{n}", days_before_today(1), 5000)
    assert find(generate_alerts(conn, TODAY), "pattern of late payment") == []


# Rule c: forecast goes negative within 30 days


def test_rule_c_negative_forecast_flagged_with_no_overdue_customer(conn):
    add_bill(conn, "Water", 100000, 20)  # due 2024-06-20, outside rule d's window
    hits = find(generate_alerts(conn, TODAY), "Cash short")
    assert len(hits) == 1
    assert hits[0]["severity"] == "high"
    assert hits[0]["title"] == "Cash short on 2024-06-20 — chase your largest debtor or defer a bill"
    assert hits[0]["action_url"] == "/forecast"
    assert hits[0]["action_label"] == "View forecast"


def test_rule_c_names_largest_overdue_customer(conn):
    acme = add_customer(conn, "Acme")
    add_invoice(conn, acme, "INV-0001", days_before_today(10), 50000)
    add_bill(conn, "Water", 100000, 20)
    hits = find(generate_alerts(conn, TODAY), "Cash short")
    assert hits[0]["title"] == "Cash short on 2024-06-20 — chase Acme or defer a bill"


def test_rule_c_no_alert_when_forecast_stays_positive(conn):
    set_opening(conn, 500000)
    add_bill(conn, "Water", 100000, 20)
    assert find(generate_alerts(conn, TODAY), "Cash short") == []


# Rule d: active bill due within 3 days that cash cannot cover


def test_rule_d_bill_due_soon_not_covered_by_opening_balance(conn):
    add_bill(conn, "Rent", 50000, 17)  # due 2 days from TODAY, opening balance is 0
    hits = find(generate_alerts(conn, TODAY), "Pay Rent")
    assert len(hits) == 1
    assert hits[0]["title"] == "Pay Rent before 2024-06-17"
    assert hits[0]["severity"] == "high"
    assert hits[0]["action_url"] == "/bills"
    assert hits[0]["action_label"] == "View bills"


def test_rule_d_bill_due_soon_covered_by_cash_not_flagged(conn):
    set_opening(conn, 500000)
    add_bill(conn, "Rent", 50000, 17)
    assert find(generate_alerts(conn, TODAY), "Pay Rent") == []


def test_rule_d_bill_due_more_than_3_days_away_not_flagged(conn):
    add_bill(conn, "Rent", 50000, 21)  # 6 days away, opening balance is 0
    assert find(generate_alerts(conn, TODAY), "Pay Rent") == []


# Rule e: invoice due tomorrow


def test_rule_e_invoice_due_tomorrow_flagged_low(conn):
    cid = add_customer(conn, "Thabo")
    add_invoice(conn, cid, "INV-0009", "2024-06-16", 10000)
    hits = find(generate_alerts(conn, TODAY), "Send reminder")
    assert len(hits) == 1
    assert hits[0]["title"] == "Send reminder before due date for Thabo"
    assert hits[0]["severity"] == "low"
    assert hits[0]["action_url"] == f"/customers/{cid}/chase"
    assert hits[0]["action_label"] == "Draft reminder"


def test_rule_e_invoice_due_today_not_flagged(conn):
    cid = add_customer(conn, "Thabo")
    add_invoice(conn, cid, "INV-0009", TODAY, 10000)
    assert find(generate_alerts(conn, TODAY), "Send reminder") == []


# sort_alerts and count_alerts


def test_sort_alerts_orders_high_medium_low_and_is_stable():
    alerts = [
        {"severity": "low", "title": "A"},
        {"severity": "high", "title": "B"},
        {"severity": "medium", "title": "C"},
        {"severity": "high", "title": "D"},
        {"severity": "low", "title": "E"},
    ]
    result = sort_alerts(alerts)
    assert [a["title"] for a in result] == ["B", "D", "C", "A", "E"]


def test_count_alerts_matches_list_length(conn):
    cid = add_customer(conn, "Acme")
    add_invoice(conn, cid, "INV-0001", days_before_today(30), 10000)
    add_invoice(conn, cid, "INV-0002", "2024-06-16", 5000)
    add_bill(conn, "Rent", 50000, 17)
    expected = len(generate_alerts(conn, TODAY))
    assert expected >= 3
    assert count_alerts(conn, TODAY) == expected


# Routes


def test_alerts_page_lists_alert_title(client, conn):
    cid = add_customer(conn, "Acme")
    overdue = (date.today() - timedelta(days=10)).isoformat()
    add_invoice(conn, cid, "INV-0001", overdue, 10000)
    response = client.get("/alerts")
    assert response.status_code == 200
    assert b"Chase Acme today" in response.data


def test_alerts_page_empty_state(client):
    response = client.get("/alerts")
    assert response.status_code == 200
    assert b"No alerts right now. Nothing needs attention." in response.data


def test_today_page_returns_200_and_contains_today(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Today" in response.data


def test_nav_badge_shown_when_alerts_open(client, conn):
    cid = add_customer(conn, "Acme")
    overdue = (date.today() - timedelta(days=10)).isoformat()
    add_invoice(conn, cid, "INV-0001", overdue, 10000)
    response = client.get("/")
    assert b'class="badge"' in response.data
