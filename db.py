import os
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "backoffice.db"


def get_db():
    """Return a sqlite3 connection to backoffice.db, or to the path in BACKOFFICE_DB if set."""
    conn = sqlite3.connect(os.environ.get("BACKOFFICE_DB", DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """Create the base tables if they do not already exist."""
    conn = get_db()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS customers (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                phone TEXT,
                email TEXT,
                payment_terms_days INTEGER NOT NULL DEFAULT 14,
                notes TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS invoices (
                id INTEGER PRIMARY KEY,
                customer_id INTEGER NOT NULL REFERENCES customers(id),
                invoice_number TEXT NOT NULL UNIQUE,
                issue_date TEXT NOT NULL,
                due_date TEXT NOT NULL,
                amount_cents INTEGER NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('draft','sent','paid','void')),
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY,
                invoice_id INTEGER NOT NULL REFERENCES invoices(id),
                amount_cents INTEGER NOT NULL,
                paid_date TEXT NOT NULL,
                method TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS followups (
                id INTEGER PRIMARY KEY,
                invoice_id INTEGER REFERENCES invoices(id),
                channel TEXT,
                message_text TEXT,
                sent_at TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS recurring_bills (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                amount_cents INTEGER NOT NULL,
                due_day_of_month INTEGER NOT NULL,
                category TEXT,
                active INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS cash_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                opening_balance_cents INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


# (name, payment_terms_days, behaviour, days_late). Behaviour: on_time, late or stopped.
DEMO_CUSTOMERS = [
    ("Northwind Traders", 30, "on_time", 0),
    ("Blue Gum Catering", 14, "on_time", 0),
    ("Karoo Print Works", 30, "on_time", 0),
    ("Protea Motors", 14, "on_time", 0),
    ("Stellar Dental", 30, "on_time", 0),
    ("Harbour Logistics", 30, "late", 10),
    ("Mosaic Interiors", 14, "late", 17),
    ("Cape Fibre Co", 30, "late", 25),
    ("Umlazi Hardware", 14, "late", 12),
    ("Sunrise Events", 30, "late", 21),
    ("Greenfield Farms", 14, "stopped", 0),
    ("Old Mill Bakery", 30, "stopped", 0),
]

# Extra open invoices: (customer index, days overdue, amount_cents). Negative = not yet due.
EXTRA_OPEN_INVOICES = [
    (0, 0, 65000),
    (1, 14, 88000),
    (2, 38, 54000),
    (3, 67, 112000),
]

# (name, amount_cents, due_day_of_month, category)
DEMO_BILLS = [
    ("Rent", 1500000, 1, "rent"),
    ("Tutor assistant salary", 800000, 25, "salary"),
    ("Software subscription", 25000, 5, "other"),
    ("Supplies", 40000, 15, "supplier"),
    ("Tax provision", 120000, 28, "tax"),
]

DEMO_OPENING_BALANCE_CENTS = 200000


def _first_of_month_back(d, months_back):
    """Return the 1st of the month that is `months_back` months before date d."""
    index = d.year * 12 + (d.month - 1) - months_back
    return date(index // 12, index % 12 + 1, 1)


def seed_demo(conn, today_iso):
    """Insert demo data relative to today_iso (YYYY-MM-DD). Does nothing if any customer exists."""
    if conn.execute("SELECT 1 FROM customers LIMIT 1").fetchone():
        return

    today_d = date.fromisoformat(today_iso)
    now = datetime.now().isoformat(timespec="seconds")
    invoice_seq = 0

    def add_invoice(customer_id, issue, due, amount, paid_on):
        """Insert an invoice, plus a payment when paid_on is given. Paid_on None = open."""
        nonlocal invoice_seq
        invoice_seq += 1
        cur = conn.execute(
            "INSERT INTO invoices (customer_id, invoice_number, issue_date, due_date, amount_cents, status, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                customer_id,
                f"INV-{invoice_seq:04d}",
                issue.isoformat(),
                due.isoformat(),
                amount,
                "paid" if paid_on else "sent",
                f"{issue.isoformat()}T09:00:00",
            ),
        )
        if paid_on:
            conn.execute(
                "INSERT INTO payments (invoice_id, amount_cents, paid_date, method, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (cur.lastrowid, amount, paid_on.isoformat(), "EFT", now),
            )
        return cur.lastrowid

    customer_ids = []
    for ci, (name, terms, behaviour, days_late) in enumerate(DEMO_CUSTOMERS):
        email = f"accounts@{name.lower().replace(' ', '')}.example"
        cur = conn.execute(
            "INSERT INTO customers (name, phone, email, payment_terms_days, notes, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (name, None, email, terms, None, now),
        )
        customer_ids.append(cur.lastrowid)

        # Six monthly invoices, oldest first.
        for months_back in range(6, 0, -1):
            issue = _first_of_month_back(today_d, months_back)
            due = issue + timedelta(days=terms)
            amount = 40000 + ((ci * 37 + months_back * 101) % 801) * 100
            if behaviour == "stopped" and months_back <= 2:
                add_invoice(cur.lastrowid, issue, due, amount, None)
                continue
            if behaviour == "late":
                paid_on = due + timedelta(days=days_late)
            else:
                paid_on = due
            if paid_on > today_d:
                paid_on = None  # not paid yet, so it is still open
            add_invoice(cur.lastrowid, issue, due, amount, paid_on)

    for ci, days_overdue, amount in EXTRA_OPEN_INVOICES:
        due = today_d - timedelta(days=days_overdue)
        issue = due - timedelta(days=DEMO_CUSTOMERS[ci][1])
        add_invoice(customer_ids[ci], issue, due, amount, None)

    for name, amount, day, category in DEMO_BILLS:
        conn.execute(
            "INSERT INTO recurring_bills (name, amount_cents, due_day_of_month, category, active)"
            " VALUES (?, ?, ?, ?, 1)",
            (name, amount, day, category),
        )

    conn.execute(
        "INSERT OR REPLACE INTO cash_settings (id, opening_balance_cents, updated_at) VALUES (1, ?, ?)",
        (DEMO_OPENING_BALANCE_CENTS, now),
    )
    conn.commit()
