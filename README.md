# Back Office

A small Flask app for tracking customers, invoices, payments, recurring bills and cash forecasts.

## Setup (Windows PowerShell)

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Then open http://127.0.0.1:5000 in your browser.

## Tests

Install the development dependencies first, then run pytest:

```powershell
pip install -r requirements-dev.txt
python -m pytest
```

## Features

Mapped to the hackathon requirements:

- **Invoice to payment, with drafted follow-ups:** create customers and invoices, record part or full payments, and draft a reminder for each customer on the chase page.
- **Who-owes-me dashboard:** see every customer with an open balance, how overdue they are, and what to do next.
- **30 to 90 day cash forecast:** a rule-based projection of cash in and out from open invoices, recurring bills and the opening balance.
- **What-if scenarios:** test how a late payment, a lost customer or a new bill would change the forecast.
- **Actionable alerts:** flags for overdue invoices, cash shortfalls and upcoming bills.
- **Quick add:** enter a payment, invoice or bill from a single short form.

## Run tests

```powershell
python -m pytest
```

## Known limitations

- No real sending of reminders: drafted messages are copied or opened in WhatsApp, and the app only logs that a follow-up was made.
- No bank sync: payments and balances are entered by hand.
- Rule-based forecast: projections follow simple timing rules from due dates and payment history, not a statistical model.

