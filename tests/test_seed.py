import pytest

import db

TODAY = "2025-06-15"


@pytest.fixture
def demo_db(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKOFFICE_DB", str(tmp_path / "test.db"))
    db.init_db()
    return tmp_path


def _scalar(sql):
    conn = db.get_db()
    try:
        return conn.execute(sql).fetchone()[0]
    finally:
        conn.close()


def _seed():
    conn = db.get_db()
    try:
        db.seed_demo(conn, TODAY)
    finally:
        conn.close()


def test_seed_creates_12_customers(demo_db):
    _seed()
    assert _scalar("SELECT COUNT(*) FROM customers") == 12


def test_seed_is_idempotent(demo_db):
    _seed()
    _seed()
    assert _scalar("SELECT COUNT(*) FROM customers") == 12


def test_seed_creates_5_active_recurring_bills(demo_db):
    _seed()
    assert _scalar("SELECT COUNT(*) FROM recurring_bills WHERE active = 1") == 5


def test_reset_without_confirm_changes_nothing(demo_db):
    from app import create_app

    _seed()
    before = _scalar("SELECT COUNT(*) FROM customers")
    client = create_app().test_client()
    client.post("/settings/reset-demo", data={})
    assert _scalar("SELECT COUNT(*) FROM customers") == before


def test_reset_with_confirm_reloads_12_customers(demo_db):
    from app import create_app

    _seed()
    client = create_app().test_client()
    response = client.post("/settings/reset-demo", data={"confirm": "yes"})
    assert response.status_code == 302
    assert _scalar("SELECT COUNT(*) FROM customers") == 12
