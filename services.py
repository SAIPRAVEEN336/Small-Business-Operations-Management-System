import calendar
import copy
import re
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation


def today():
    """Return today's date as an ISO string (YYYY-MM-DD)."""
    return date.today().isoformat()


def compute_status(invoice_row, balance_cents, today_iso):
    """Work out an invoice's status from its stored status, balance and due date.

    Overdue is never stored; it is derived here.
    """
    if invoice_row["status"] == "void":
        return "void"
    if balance_cents == 0:
        return "paid"
    if balance_cents > 0 and today_iso > invoice_row["due_date"]:
        return "overdue"
    return "open"


def balance_for_invoice(conn, invoice_id):
    """Return amount minus payments in cents, or None if the invoice does not exist."""
    row = conn.execute(
        """
        SELECT i.amount_cents - COALESCE(SUM(p.amount_cents), 0) AS balance
        FROM invoices i
        LEFT JOIN payments p ON p.invoice_id = i.id
        WHERE i.id = ?
        GROUP BY i.id
        """,
        (invoice_id,),
    ).fetchone()
    return row["balance"] if row else None


def next_invoice_number(conn):
    """Return the next invoice number, e.g. INV-0007, based on the max id + 1."""
    row = conn.execute("SELECT COALESCE(MAX(id), 0) + 1 AS n FROM invoices").fetchone()
    return "INV-%04d" % row["n"]


def days_overdue(due_date_iso, today_iso):
    """Return the whole number of days past the due date, or 0 if not overdue."""
    if today_iso <= due_date_iso:
        return 0
    return (date.fromisoformat(today_iso) - date.fromisoformat(due_date_iso)).days


def format_rands(cents):
    """Format cents as rands, e.g. 123456 -> 'R 1 234.56'. Registered as the 'rands' Jinja filter."""
    cents = int(cents or 0)
    sign = "-" if cents < 0 else ""
    rands, remainder = divmod(abs(cents), 100)
    grouped = f"{rands:,}".replace(",", " ")
    return f"{sign}R {grouped}.{remainder:02d}"


def parse_cents(text):
    """Parse a rand amount typed by the user (e.g. '1 234.5' or '1,234.50') into integer cents.

    Spaces and commas are treated as thousands separators. Returns None if the
    text is not a valid finite number.
    """
    cleaned = (text or "").strip().replace(" ", "").replace(",", "")
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    if not value.is_finite():
        return None
    return int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


PAYMENT_METHODS = ("cash", "bank transfer", "card", "other")

# A number with optional space or comma thousands groups (exactly 3 digits each) and optional decimals.
_QUICK_AMOUNT = re.compile(r"\d+(?:[ ,]\d{3}(?!\d))*(?:\.\d+)?")


def parse_quick_entry(text):
    """Read a free-text payment note such as 'Sipho paid 500 by EFT'. Pure: no database access.

    Returns a dict with customer_name, amount_cents, method and recognised. The amount is the
    first number in the text; the customer is the text before the word 'paid'.
    """
    text = (text or "").strip()
    amount_cents = None
    match = _QUICK_AMOUNT.search(text)
    if match:
        amount_cents = parse_cents(match.group(0))

    lowered = text.lower()
    if re.search(r"\b(eft|transfer|bank)\b", lowered):
        method = "bank transfer"
    elif re.search(r"\bcash\b", lowered):
        method = "cash"
    elif re.search(r"\bcard\b", lowered):
        method = "card"
    else:
        method = "other"

    customer_name = None
    paid = re.search(r"\bpaid\b", text, re.IGNORECASE)
    if paid:
        customer_name = text[:paid.start()].strip() or None

    return {
        "customer_name": customer_name,
        "amount_cents": amount_cents,
        "method": method,
        "recognised": customer_name is not None and amount_cents is not None,
    }


def add_payment(conn, invoice_id, amount_cents, paid_date, method):
    """Validate and record a payment against an invoice. Returns (ok, message).

    Rejects void invoices, already-paid invoices, missing, zero or negative amounts,
    amounts above the outstanding balance, invalid dates and unknown methods.
    """
    invoice = conn.execute("SELECT id, status FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    if invoice is None:
        return (False, "Invoice not found.")
    if invoice["status"] == "void":
        return (False, "This invoice is void, so no payment can be recorded.")

    balance = balance_for_invoice(conn, invoice_id)
    if balance <= 0:
        return (False, "This invoice is already paid.")
    if amount_cents is None:
        return (False, "Enter a valid amount.")
    if amount_cents <= 0:
        return (False, "Amount must be greater than zero.")
    if amount_cents > balance:
        return (False, f"Payment is more than the outstanding balance of {format_rands(balance)}.")
    try:
        date.fromisoformat(paid_date)
    except (TypeError, ValueError):
        return (False, "Enter a valid payment date.")
    if method not in PAYMENT_METHODS:
        return (False, "Choose a payment method.")

    conn.execute(
        """
        INSERT INTO payments (invoice_id, amount_cents, paid_date, method, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            invoice_id,
            amount_cents,
            paid_date,
            method,
            datetime.now().isoformat(timespec="seconds"),
        ),
    )
    conn.commit()
    return (True, "Payment recorded.")


def unpaid_invoices(conn):
    """Return non-void invoices that still have a positive balance, with customer name and phone."""
    return conn.execute(
        """
        SELECT i.id, i.customer_id, i.invoice_number, i.due_date,
               c.name AS customer_name, c.phone AS customer_phone,
               i.amount_cents - COALESCE(SUM(p.amount_cents), 0) AS balance_cents
        FROM invoices i
        JOIN customers c ON c.id = i.customer_id
        LEFT JOIN payments p ON p.invoice_id = i.id
        WHERE i.status != 'void'
        GROUP BY i.id
        HAVING balance_cents > 0
        """
    ).fetchall()


def outstanding_by_customer(conn, today_iso):
    """Return one dict per customer who owes money, sorted by days overdue (most first)."""
    last_chased = {
        row["customer_id"]: row["last_sent"]
        for row in conn.execute(
            """
            SELECT i.customer_id, MAX(f.sent_at) AS last_sent
            FROM followups f
            JOIN invoices i ON i.id = f.invoice_id
            GROUP BY i.customer_id
            """
        ).fetchall()
    }
    by_customer = {}
    for inv in unpaid_invoices(conn):
        cid = inv["customer_id"]
        entry = by_customer.get(cid)
        if entry is None:
            entry = {
                "customer_id": cid,
                "name": inv["customer_name"],
                "total_owed_cents": 0,
                "oldest_due_date": inv["due_date"],
                "days_overdue": 0,
                "invoice_count": 0,
                "last_chased": last_chased.get(cid),
            }
            by_customer[cid] = entry
        entry["total_owed_cents"] += inv["balance_cents"]
        entry["invoice_count"] += 1
        if inv["due_date"] < entry["oldest_due_date"]:
            entry["oldest_due_date"] = inv["due_date"]
    for entry in by_customer.values():
        entry["days_overdue"] = days_overdue(entry["oldest_due_date"], today_iso)
    return sorted(by_customer.values(), key=lambda e: (-e["days_overdue"], e["name"]))


def dashboard_summary(conn, today_iso):
    """Return the headline numbers for the dashboard."""
    invoices = unpaid_invoices(conn)
    overdue_cents = sum(
        inv["balance_cents"] for inv in invoices if days_overdue(inv["due_date"], today_iso) > 0
    )
    received = conn.execute(
        "SELECT COALESCE(SUM(amount_cents), 0) AS total FROM payments WHERE substr(paid_date, 1, 7) = ?",
        (today_iso[:7],),
    ).fetchone()["total"]
    return {
        "total_outstanding_cents": sum(inv["balance_cents"] for inv in invoices),
        "overdue_cents": overdue_cents,
        "customers_owing": len(outstanding_by_customer(conn, today_iso)),
        "received_this_month_cents": received,
    }


def pick_tone(oldest_days_overdue):
    """Choose the reminder tone from how many days the oldest unpaid invoice is overdue."""
    if oldest_days_overdue <= 7:
        return "friendly"
    if oldest_days_overdue <= 20:
        return "firm"
    return "final"


def draft_reminder(customer_first_name, invoice_numbers, total_owed_cents, tone):
    """Return a polite payment reminder message for a customer."""
    numbers = ", ".join(invoice_numbers)
    label = "invoice" if len(invoice_numbers) == 1 else "invoices"
    openers = {
        "friendly": "I hope you are well. This is a friendly reminder that payment is still outstanding on",
        "firm": "I am following up on payment that is still outstanding on",
        "final": "This is a final reminder that payment is still outstanding on",
    }
    opener = openers.get(tone, openers["friendly"])
    return (
        f"Hi {customer_first_name},\n\n"
        f"{opener} {label} {numbers}.\n\n"
        f"The total outstanding is {format_rands(total_owed_cents)}.\n\n"
        "Please arrange payment at your earliest convenience. "
        "If you have already paid, please let me know so I can update my records.\n\n"
        "Thank you."
    )


def get_opening_balance_cents(conn):
    """Return the opening cash balance in cents, or 0 if it has not been set."""
    row = conn.execute("SELECT opening_balance_cents FROM cash_settings WHERE id = 1").fetchone()
    return row["opening_balance_cents"] if row else 0


def expected_pay_date(invoice_row, avg_days_late, today_iso):
    """Return (expected_pay_date_iso, uncertain) for an invoice.

    Normally the due date plus the customer's average days late. An unpaid invoice
    more than 30 days past its due date is uncertain and expected in 14 days from today.
    """
    due = date.fromisoformat(invoice_row["due_date"])
    today_date = date.fromisoformat(today_iso)
    balance = dict(invoice_row).get("balance_cents")
    still_unpaid = balance is None or balance > 0
    if still_unpaid and (today_date - due).days > 30:
        return ((today_date + timedelta(days=14)).isoformat(), True)
    return ((due + timedelta(days=round(avg_days_late))).isoformat(), False)


def average_days_late(conn, customer_id):
    """Return the customer's average days late over fully paid invoices, rounded; 0 if no history.

    Days late per invoice = last payment's paid_date minus due_date (can be negative).
    """
    rows = conn.execute(
        """
        SELECT i.due_date, i.amount_cents, MAX(p.paid_date) AS last_paid
        FROM invoices i
        JOIN payments p ON p.invoice_id = i.id
        WHERE i.customer_id = ? AND i.status != 'void'
        GROUP BY i.id, i.due_date, i.amount_cents
        HAVING SUM(p.amount_cents) >= i.amount_cents
        """,
        (customer_id,),
    ).fetchall()
    if not rows:
        return 0
    total = sum(
        (date.fromisoformat(r["last_paid"]) - date.fromisoformat(r["due_date"])).days for r in rows
    )
    return round(total / len(rows))


def _effective_due_day(due_day_of_month, day):
    """Return the bill's due day in the month containing `day`, capped at that month's last day."""
    last_day = calendar.monthrange(day.year, day.month)[1]
    return min(due_day_of_month, last_day)


def build_cash_forecast(conn, start_date_iso, days):
    """Project the daily cash position for 30, 60 or 90 days from start_date_iso.

    Inflows: each unpaid invoice lands on its expected pay date (pushed to the start
    date if that date has already passed). uncertain_inflows_cents is the part of
    expected_inflows_cents coming from flagged invoices. Outflows: active recurring
    bills on their due day of month. running_balance_cents starts from the opening balance.
    """
    if days not in (30, 60, 90):
        raise ValueError("days must be 30, 60 or 90")
    start = date.fromisoformat(start_date_iso)

    avg_late = {}
    inflows = {}  # iso date -> [expected_cents, uncertain_cents]
    for inv in unpaid_invoices(conn):
        cid = inv["customer_id"]
        if cid not in avg_late:
            avg_late[cid] = average_days_late(conn, cid)
        pay_iso, uncertain = expected_pay_date(inv, avg_late[cid], start_date_iso)
        if pay_iso < start_date_iso:
            pay_iso = start_date_iso
        bucket = inflows.setdefault(pay_iso, [0, 0])
        bucket[0] += inv["balance_cents"]
        if uncertain:
            bucket[1] += inv["balance_cents"]

    bills = conn.execute(
        "SELECT amount_cents, due_day_of_month FROM recurring_bills WHERE active = 1"
    ).fetchall()

    running = get_opening_balance_cents(conn)
    entries = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        day_iso = day.isoformat()
        inflow, uncertain_in = inflows.get(day_iso, [0, 0])
        outflow = sum(
            b["amount_cents"]
            for b in bills
            if _effective_due_day(b["due_day_of_month"], day) == day.day
        )
        running += inflow - outflow
        entries.append(
            {
                "date": day_iso,
                "expected_inflows_cents": inflow,
                "uncertain_inflows_cents": uncertain_in,
                "expected_outflows_cents": outflow,
                "running_balance_cents": running,
            }
        )
    return entries


def weekly_summary(daily_entries):
    """Group daily entries into 7-day blocks starting from the first date."""
    weeks = []
    for start in range(0, len(daily_entries), 7):
        block = daily_entries[start:start + 7]
        weeks.append(
            {
                "week_start": block[0]["date"],
                "inflows_cents": sum(e["expected_inflows_cents"] for e in block),
                "outflows_cents": sum(e["expected_outflows_cents"] for e in block),
                "closing_balance_cents": block[-1]["running_balance_cents"],
            }
        )
    return weeks


def lowest_balance(daily_entries):
    """Return (balance_cents, date_iso) of the lowest running balance (first occurrence), or None."""
    if not daily_entries:
        return None
    lowest = min(daily_entries, key=lambda e: e["running_balance_cents"])
    return (lowest["running_balance_cents"], lowest["date"])


def first_negative_date(daily_entries):
    """Return the first date whose running balance is below zero, or None."""
    for entry in daily_entries:
        if entry["running_balance_cents"] < 0:
            return entry["date"]
    return None


def _open_landings(conn, today_iso):
    """Return (invoice_row, pay_iso, uncertain) for each open invoice, placed as build_cash_forecast places it."""
    avg_late = {}
    landings = []
    for inv in unpaid_invoices(conn):
        cid = inv["customer_id"]
        if cid not in avg_late:
            avg_late[cid] = average_days_late(conn, cid)
        pay_iso, uncertain = expected_pay_date(inv, avg_late[cid], today_iso)
        if pay_iso < today_iso:
            pay_iso = today_iso
        landings.append((inv, pay_iso, uncertain))
    return landings


def _rebuild_balances(daily, opening_cents):
    """Recompute running_balance_cents in date order from the opening balance. Returns daily."""
    running = opening_cents
    for entry in daily:
        running += entry["expected_inflows_cents"] - entry["expected_outflows_cents"]
        entry["running_balance_cents"] = running
    return daily


def _move_inflows(daily, moves):
    """Move inflow amounts in place. Each move is (from_iso, to_iso or None, amount_cents, uncertain).

    The amount is removed from from_iso and added to to_iso (None means removed only).
    Dates outside the forecast window are ignored.
    """
    by_date = {entry["date"]: entry for entry in daily}
    for from_iso, to_iso, amount, uncertain in moves:
        source = by_date.get(from_iso)
        if source is not None:
            source["expected_inflows_cents"] -= amount
            if uncertain:
                source["uncertain_inflows_cents"] -= amount
        target = by_date.get(to_iso) if to_iso else None
        if target is not None:
            target["expected_inflows_cents"] += amount
            if uncertain:
                target["uncertain_inflows_cents"] += amount


def apply_scenario(conn, base_forecast_daily, scenario, today_iso):
    """Return a NEW daily forecast with the what-if scenario applied. The base list is never changed.

    scenario["type"] is one of:
      - late_customer: customer_id, days (default 15). That customer's inflows move later.
      - lose_customer: customer_id. That customer's open inflows are removed.
      - delay_all: days (default 7). Every open inflow moves later.
      - extra_expense: amount_cents, date_iso. An extra outflow on that date.
    """
    daily = copy.deepcopy(base_forecast_daily)
    kind = scenario.get("type")
    if kind == "extra_expense":
        amount = int(scenario["amount_cents"])
        for entry in daily:
            if entry["date"] == scenario["date_iso"]:
                entry["expected_outflows_cents"] += amount
    elif kind in ("late_customer", "lose_customer", "delay_all"):
        shift = None
        if kind in ("late_customer", "delay_all"):
            default_days = 7 if kind == "delay_all" else 15
            shift = int(scenario.get("days", default_days))
        cid = int(scenario["customer_id"]) if kind != "delay_all" else None
        moves = []
        for inv, pay_iso, uncertain in _open_landings(conn, today_iso):
            if cid is not None and inv["customer_id"] != cid:
                continue
            to_iso = None
            if shift is not None:
                to_iso = (date.fromisoformat(pay_iso) + timedelta(days=shift)).isoformat()
            moves.append((pay_iso, to_iso, inv["balance_cents"], uncertain))
        _move_inflows(daily, moves)
    else:
        raise ValueError("unknown scenario type")
    return _rebuild_balances(daily, get_opening_balance_cents(conn))


def summary_sentence(base_daily, scenario_daily):
    """Describe in one sentence how the scenario changes the lowest balance."""
    base_low = lowest_balance(base_daily)
    scenario_low = lowest_balance(scenario_daily)
    if scenario_low[0] < base_low[0]:
        return (
            f"With this scenario, your lowest balance drops from {format_rands(base_low[0])} "
            f"to {format_rands(scenario_low[0])} on {scenario_low[1]}."
        )
    return (
        f"With this scenario, your lowest balance stays at {format_rands(scenario_low[0])} "
        f"on {scenario_low[1]}."
    )



ALERT_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _alert(severity, title, explanation, action_label, action_url):
    """Build one alert dict with the keys the alerts page and Today page expect."""
    return {
        "severity": severity,
        "title": title,
        "explanation": explanation,
        "action_label": action_label,
        "action_url": action_url,
    }


def _largest_overdue_customer_name(conn, today_iso):
    """Return the customer on the largest overdue balance, or 'your largest debtor' if none is overdue."""
    overdue = [inv for inv in unpaid_invoices(conn) if days_overdue(inv["due_date"], today_iso) > 0]
    if not overdue:
        return "your largest debtor"
    return max(overdue, key=lambda inv: inv["balance_cents"])["customer_name"]


def generate_alerts(conn, today_iso):
    """Return the list of alerts for today_iso. Rules a to e are described in the Phase 5 spec."""
    alerts = []
    invoices = unpaid_invoices(conn)
    today_date = date.fromisoformat(today_iso)
    tomorrow_iso = (today_date + timedelta(days=1)).isoformat()

    # Rule a (chase invoices more than 7 days overdue) and collect overdue counts for rule b.
    overdue_by_customer = {}
    for inv in invoices:
        late = days_overdue(inv["due_date"], today_iso)
        if late <= 0:
            continue
        info = overdue_by_customer.setdefault(
            inv["customer_id"], {"name": inv["customer_name"], "count": 0}
        )
        info["count"] += 1
        if late > 7:
            alerts.append(
                _alert(
                    "high" if late > 21 else "medium",
                    f"Chase {inv['customer_name']} today",
                    f"Invoice {inv['invoice_number']} has {format_rands(inv['balance_cents'])} "
                    f"outstanding, {late} days overdue.",
                    "Chase now",
                    f"/customers/{inv['customer_id']}/chase",
                )
            )

    # Rule b (3 or more overdue invoices for one customer).
    for cid, info in overdue_by_customer.items():
        if info["count"] >= 3:
            alerts.append(
                _alert(
                    "medium",
                    f"Call {info['name']} — pattern of late payment",
                    f"{info['count']} invoices are overdue.",
                    "Open customer",
                    f"/customers/{cid}/edit",
                )
            )

    # Rule c (forecast goes negative within 30 days).
    entries = build_cash_forecast(conn, today_iso, 30)
    negative_on = first_negative_date(entries)
    if negative_on:
        largest = _largest_overdue_customer_name(conn, today_iso)
        alerts.append(
            _alert(
                "high",
                f"Cash short on {negative_on} — chase {largest} or defer a bill",
                f"The projected balance goes below zero on {negative_on}.",
                "View forecast",
                "/forecast",
            )
        )

    # Rule d (active recurring bill due within 3 days that the cash position cannot cover).
    opening = get_opening_balance_cents(conn)
    bills = conn.execute(
        "SELECT id, name, amount_cents, due_day_of_month FROM recurring_bills WHERE active = 1 ORDER BY id"
    ).fetchall()
    for bill in bills:
        for offset in range(4):
            due_day = today_date + timedelta(days=offset)
            if _effective_due_day(bill["due_day_of_month"], due_day) != due_day.day:
                continue
            due_iso = due_day.isoformat()
            lowest_until_due = min(e["running_balance_cents"] for e in entries if e["date"] <= due_iso)
            if lowest_until_due < 0 or opening < bill["amount_cents"]:
                alerts.append(
                    _alert(
                        "high",
                        f"Pay {bill['name']} before {due_iso}",
                        f"{format_rands(bill['amount_cents'])} is due on {due_iso} and the cash "
                        "position is not enough to cover it.",
                        "View bills",
                        "/bills",
                    )
                )

    # Rule e (invoice falls due tomorrow).
    for inv in invoices:
        if inv["due_date"] == tomorrow_iso:
            alerts.append(
                _alert(
                    "low",
                    f"Send reminder before due date for {inv['customer_name']}",
                    f"Invoice {inv['invoice_number']} for {format_rands(inv['balance_cents'])} "
                    f"is due {tomorrow_iso}.",
                    "Draft reminder",
                    f"/customers/{inv['customer_id']}/chase",
                )
            )

    return alerts


def sort_alerts(alerts):
    """Return a new list ordered high, medium, low. Stable within each severity."""
    return sorted(alerts, key=lambda a: ALERT_SEVERITY_ORDER.get(a["severity"], 3))


def count_alerts(conn, today_iso):
    """Return how many alerts are open for today_iso (used for the nav badge)."""
    return len(generate_alerts(conn, today_iso))



def _month_keys(today_iso, months):
    """Return 'YYYY-MM' keys for the last `months` calendar months ending in today's month, oldest first."""
    base = date.fromisoformat(today_iso)
    year, month = base.year, base.month
    keys = []
    for _ in range(months):
        keys.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    return list(reversed(keys))


def monthly_series(conn, today_iso, months=6):
    """Return invoiced and received totals per month for the last `months` months, oldest first.

    Void invoices are excluded from both totals. Months with no rows show 0.
    """
    keys = _month_keys(today_iso, months)
    invoiced = {
        row["month"]: row["total"]
        for row in conn.execute(
            """
            SELECT substr(issue_date, 1, 7) AS month, COALESCE(SUM(amount_cents), 0) AS total
            FROM invoices
            WHERE status != 'void'
            GROUP BY substr(issue_date, 1, 7)
            """
        ).fetchall()
    }
    received = {
        row["month"]: row["total"]
        for row in conn.execute(
            """
            SELECT substr(p.paid_date, 1, 7) AS month, COALESCE(SUM(p.amount_cents), 0) AS total
            FROM payments p
            JOIN invoices i ON i.id = p.invoice_id
            WHERE i.status != 'void'
            GROUP BY substr(p.paid_date, 1, 7)
            """
        ).fetchall()
    }
    return [
        {
            "label": calendar.month_abbr[int(key[5:])],
            "invoiced_cents": int(invoiced.get(key, 0)),
            "received_cents": int(received.get(key, 0)),
        }
        for key in keys
    ]


def bills_by_category(conn):
    """Return active recurring bill totals per category, largest first. Blank categories become 'other'."""
    rows = conn.execute(
        """
        SELECT COALESCE(NULLIF(TRIM(category), ''), 'other') AS category,
               COALESCE(SUM(amount_cents), 0) AS total_cents
        FROM recurring_bills
        WHERE active = 1
        GROUP BY COALESCE(NULLIF(TRIM(category), ''), 'other')
        ORDER BY total_cents DESC
        """
    ).fetchall()
    return [{"category": row["category"], "total_cents": int(row["total_cents"])} for row in rows]


def active_bills_total_cents(conn):
    """Return the sum of amount_cents over active recurring bills, in cents."""
    row = conn.execute(
        "SELECT COALESCE(SUM(amount_cents), 0) AS total FROM recurring_bills WHERE active = 1"
    ).fetchone()
    return int(row["total"])


def recent_invoices(conn, limit=8):
    """Return the newest non-void, unpaid (open or overdue) invoices, newest first, up to limit."""
    today_iso = today()
    rows = conn.execute(
        """
        SELECT i.id, i.invoice_number, i.status, i.amount_cents, i.issue_date, i.due_date,
               c.name AS customer_name,
               i.amount_cents - COALESCE(SUM(p.amount_cents), 0) AS balance_cents
        FROM invoices i
        JOIN customers c ON c.id = i.customer_id
        LEFT JOIN payments p ON p.invoice_id = i.id
        WHERE i.status != 'void'
        GROUP BY i.id
        ORDER BY i.issue_date DESC, i.id DESC
        """
    ).fetchall()
    result = []
    for row in rows:
        status = compute_status(row, row["balance_cents"], today_iso)
        if status == "paid":
            continue
        result.append(
            {
                "id": row["id"],
                "invoice_number": row["invoice_number"],
                "customer_name": row["customer_name"],
                "status": status,
                "amount_cents": row["amount_cents"],
                "due_date": row["due_date"],
            }
        )
        if len(result) >= limit:
            break
    return result


def upcoming_bills(conn):
    """Return active recurring bills ordered by due day of month."""
    rows = conn.execute(
        """
        SELECT name, COALESCE(NULLIF(TRIM(category), ''), 'other') AS category,
               amount_cents, due_day_of_month
        FROM recurring_bills
        WHERE active = 1
        ORDER BY due_day_of_month
        """
    ).fetchall()
    return [
        {
            "name": row["name"],
            "category": row["category"],
            "amount_cents": row["amount_cents"],
            "due_day_of_month": row["due_day_of_month"],
        }
        for row in rows
    ]


def forecast_bands(base_forecast, pct=0.15):
    """Return upside and downside running balances: base balance +/- pct of cumulative base inflow to date."""
    upside = []
    downside = []
    cumulative_inflow = 0
    for entry in base_forecast:
        cumulative_inflow += entry["expected_inflows_cents"]
        balance = entry["running_balance_cents"]
        swing = cumulative_inflow * pct
        upside.append(int(round(balance + swing)))
        downside.append(int(round(balance - swing)))
    return {"upside": upside, "downside": downside}


def forecast_summary(base_forecast):
    """Return headline forecast numbers: opening balance, lowest balance and date, and average weekly net flow."""
    if not base_forecast:
        return {
            "current_balance_cents": 0,
            "lowest_cents": 0,
            "lowest_date": None,
            "avg_weekly_net_cents": 0,
        }
    first = base_forecast[0]
    current_balance = first["running_balance_cents"] - (
        first["expected_inflows_cents"] - first["expected_outflows_cents"]
    )
    lowest_cents, lowest_date = lowest_balance(base_forecast)
    total_net = sum(e["expected_inflows_cents"] - e["expected_outflows_cents"] for e in base_forecast)
    weeks = len(base_forecast) / 7
    avg_weekly_net = int(round(total_net / weeks)) if weeks > 0 else 0
    return {
        "current_balance_cents": int(current_balance),
        "lowest_cents": int(lowest_cents),
        "lowest_date": lowest_date,
        "avg_weekly_net_cents": avg_weekly_net,
    }


def ar_aging(conn, today_iso):
    """Return unpaid balances of open invoices in four buckets: Current, 1-30, 31-60 and 61+ days past due."""
    totals = {"Current": 0, "1-30 days": 0, "31-60 days": 0, "61+ days": 0}
    rows = conn.execute(
        """
        SELECT i.due_date,
               i.amount_cents - COALESCE(SUM(p.amount_cents), 0) AS balance_cents
        FROM invoices i
        LEFT JOIN payments p ON p.invoice_id = i.id
        WHERE i.status NOT IN ('paid', 'void')
        GROUP BY i.id
        HAVING balance_cents > 0
        """
    ).fetchall()
    for row in rows:
        late = days_overdue(row["due_date"], today_iso)
        if late == 0:
            label = "Current"
        elif late <= 30:
            label = "1-30 days"
        elif late <= 60:
            label = "31-60 days"
        else:
            label = "61+ days"
        totals[label] += row["balance_cents"]
    return [{"label": label, "amount_cents": int(cents)} for label, cents in totals.items()]


def _next_occurrence(due_day_of_month, today_date):
    """Return the next calendar date (today or later) on which the bill's due day falls."""
    this_month = today_date.replace(day=_effective_due_day(due_day_of_month, today_date))
    if this_month >= today_date:
        return this_month
    if today_date.month == 12:
        year, month = today_date.year + 1, 1
    else:
        year, month = today_date.year, today_date.month + 1
    first_of_next = date(year, month, 1)
    return date(year, month, _effective_due_day(due_day_of_month, first_of_next))


def ap_due(conn, today_iso):
    """Return active recurring bill totals bucketed by days until next due date: This week, Next week, 2+ weeks."""
    totals = {"This week": 0, "Next week": 0, "2+ weeks": 0}
    today_date = date.fromisoformat(today_iso)
    bills = conn.execute(
        "SELECT amount_cents, due_day_of_month FROM recurring_bills WHERE active = 1"
    ).fetchall()
    for bill in bills:
        days_away = (_next_occurrence(bill["due_day_of_month"], today_date) - today_date).days
        if days_away <= 6:
            label = "This week"
        elif days_away <= 13:
            label = "Next week"
        else:
            label = "2+ weeks"
        totals[label] += bill["amount_cents"]
    return [{"label": label, "amount_cents": int(cents)} for label, cents in totals.items()]

