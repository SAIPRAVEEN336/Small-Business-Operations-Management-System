import copy
from datetime import date, datetime, timedelta

import pytest

import db
from services import (
    apply_scenario,
    build_cash_forecast,
    format_rands,
    lowest_balance,
    summary_sentence,
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
    """One customer, one open invoice of 1000.00 due in 10 days, one recurring bill of
    300.00 due on day 5 of the month."""
    today = date.today()
    now = datetime.now().isoformat(timespec="seconds")
    cid = conn.execute(
        "INSERT INTO customers (name, payment_terms_days, created_at) VALUES ('Acme', 14, ?)",
        (now,),
    ).lastrowid
    conn.execute(
        """
        INSERT INTO invoices (customer_id, invoice_number, issue_date, due_date,
                              amount_cents, status, created_at)
        VALUES (?, 'INV-0001', ?, ?, 100000, 'sent', ?)
        """,
        (cid, (today - timedelta(days=4)).isoformat(), (today + timedelta(days=10)).isoformat(), now),
    )
    conn.execute(
        "INSERT INTO recurring_bills (name, amount_cents, due_day_of_month, category, active) "
        "VALUES ('Bill', 30000, 5, 'other', 1)"
    )
    conn.commit()
    return {"customer_id": cid, "today": today.isoformat()}


def test_late_customer_moves_inflow_15_days(conn, dataset):
    today = dataset["today"]
    base = build_cash_forecast(conn, today, 30)
    before = copy.deepcopy(base)

    scenario = apply_scenario(
        conn, base, {"type": "late_customer", "customer_id": dataset["customer_id"], "days": 15}, today
    )

    assert base == before  # base list untouched
    assert base[10]["expected_inflows_cents"] == 100000
    assert scenario[10]["expected_inflows_cents"] == 0
    assert scenario[25]["date"] == (date.fromisoformat(today) + timedelta(days=25)).isoformat()
    assert scenario[25]["expected_inflows_cents"] == 100000
    assert sum(e["expected_inflows_cents"] for e in scenario) == sum(
        e["expected_inflows_cents"] for e in base
    )
    assert scenario[-1]["running_balance_cents"] == base[-1]["running_balance_cents"]


def test_lose_customer_removes_inflow(conn, dataset):
    today = dataset["today"]
    base = build_cash_forecast(conn, today, 30)

    scenario = apply_scenario(
        conn, base, {"type": "lose_customer", "customer_id": dataset["customer_id"]}, today
    )

    assert sum(e["expected_inflows_cents"] for e in base) == 100000
    assert sum(e["expected_inflows_cents"] for e in scenario) == 0
    assert sum(e["uncertain_inflows_cents"] for e in scenario) == 0
    assert scenario[-1]["running_balance_cents"] == base[-1]["running_balance_cents"] - 100000


def test_delay_all_shifts_inflow_by_7_days(conn, dataset):
    today = dataset["today"]
    base = build_cash_forecast(conn, today, 30)

    scenario = apply_scenario(conn, base, {"type": "delay_all", "days": 7}, today)

    assert base[10]["expected_inflows_cents"] == 100000
    assert scenario[10]["expected_inflows_cents"] == 0
    assert scenario[17]["expected_inflows_cents"] == 100000


def test_extra_expense_lowers_balance_from_that_day(conn, dataset):
    today = dataset["today"]
    base = build_cash_forecast(conn, today, 30)
    expense_date = (date.fromisoformat(today) + timedelta(days=3)).isoformat()

    scenario = apply_scenario(
        conn,
        base,
        {"type": "extra_expense", "amount_cents": 50000, "date_iso": expense_date},
        today,
    )

    assert scenario[3]["expected_outflows_cents"] == base[3]["expected_outflows_cents"] + 50000
    for i, (b, s) in enumerate(zip(base, scenario)):
        expected_diff = -50000 if i >= 3 else 0
        assert s["running_balance_cents"] - b["running_balance_cents"] == expected_diff


def test_summary_sentence_names_lowest_balance_and_amounts(conn, dataset):
    today = dataset["today"]
    base = build_cash_forecast(conn, today, 30)
    scenario = apply_scenario(
        conn, base, {"type": "extra_expense", "amount_cents": 50000, "date_iso": today}, today
    )

    base_low = lowest_balance(base)
    scenario_low = lowest_balance(scenario)
    sentence = summary_sentence(base, scenario)

    assert "lowest balance" in sentence
    assert format_rands(base_low[0]) in sentence
    assert format_rands(scenario_low[0]) in sentence
    assert sentence == (
        f"With this scenario, your lowest balance drops from {format_rands(base_low[0])} "
        f"to {format_rands(scenario_low[0])} on {scenario_low[1]}."
    )


def test_summary_sentence_when_expense_is_outside_window(conn, dataset):
    today = dataset["today"]
    base = build_cash_forecast(conn, today, 30)
    scenario = apply_scenario(
        conn, base, {"type": "extra_expense", "amount_cents": 50000, "date_iso": "2999-01-01"}, today
    )

    sentence = summary_sentence(base, scenario)

    assert "lowest balance stays at" in sentence
    assert format_rands(lowest_balance(base)[0]) in sentence


def test_forecast_page_with_late_customer_scenario(dataset):
    from app import create_app

    client = create_app().test_client()
    response = client.get(
        f"/forecast?scenario=late_customer&customer_id={dataset['customer_id']}&days=15"
    )
    assert response.status_code == 200
    assert b"Scenario" in response.data


def test_forecast_page_rejects_bad_amount(dataset):
    from app import create_app

    client = create_app().test_client()
    response = client.get("/forecast?scenario=extra_expense&amount=abc")
    assert response.status_code == 200
    assert b"Enter a positive amount" in response.data
