import os
import sqlite3

from flask import Flask, render_template

import db
from routes import main_bp
from routes.alerts import alerts_bp
from routes.bills import bills_bp
from routes.chase import chase_bp
from routes.customers import customers_bp
from routes.dashboard import dashboard_bp
from routes.forecast import forecast_bp
from routes.invoices import invoices_bp
from routes.quick import quick_bp
from routes.settings import settings_bp
from services import count_alerts, format_rands, today


def create_app():
    app = Flask(__name__)
    app.secret_key = os.environ.get("BACKOFFICE_SECRET", "dev-only-change-me")
    db.init_db()
    app.register_blueprint(main_bp)
    app.register_blueprint(customers_bp)
    app.register_blueprint(invoices_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(chase_bp)
    app.register_blueprint(bills_bp)
    app.register_blueprint(forecast_bp)
    app.register_blueprint(alerts_bp)
    app.register_blueprint(quick_bp)
    app.register_blueprint(settings_bp)
    app.add_template_filter(format_rands, "rands")

    @app.context_processor
    def inject_alert_count():
        """Expose the open-alert count to every template (nav badge). 0 if the database is not ready."""
        conn = None
        try:
            conn = db.get_db()
            return {"alert_count": count_alerts(conn, today())}
        except sqlite3.Error:
            return {"alert_count": 0}
        finally:
            if conn is not None:
                conn.close()

    @app.errorhandler(404)
    def page_not_found(error):
        return render_template("404.html"), 404

    @app.cli.command("seed")
    def seed():
        """Insert demo data into the database."""
        conn = db.get_db()
        try:
            db.seed_demo(conn, today())
        finally:
            conn.close()
        print("Demo data seeded.")

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
