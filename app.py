import os
import secrets
import time
import logging
from datetime import datetime, timedelta, timezone
from functools import wraps
from logging.handlers import RotatingFileHandler

from dotenv import load_dotenv
from flask import (
    Flask, render_template, request, jsonify,
    session, redirect, url_for, g
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect, text
from werkzeug.middleware.proxy_fix import ProxyFix

import helpers

load_dotenv()

app = Flask(__name__, template_folder="templates", static_folder="static")

# --------------------------------------------------
# CONFIGURATION
# --------------------------------------------------

SECRET_KEY = os.getenv("SECRET_KEY")

if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY is missing from environment variables.")

app.secret_key = SECRET_KEY

database_url = os.getenv("DATABASE_URL", "sqlite:///valence_leads.db")

# Support Render PostgreSQL URLs
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config.update(
    SQLALCHEMY_DATABASE_URI=database_url,
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "1") == "1",
    PERMANENT_SESSION_LIFETIME=timedelta(days=7),
    MAX_CONTENT_LENGTH=64 * 1024,
)

if os.getenv("TRUST_PROXY") == "1":
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

db = SQLAlchemy(app)

# --------------------------------------------------
# LOGGING
# --------------------------------------------------

os.makedirs(app.instance_path, exist_ok=True)

file_handler = RotatingFileHandler(
    os.path.join(app.instance_path, "app.log"),
    maxBytes=512 * 1024,
    backupCount=3,
    encoding="utf-8"
)

file_handler.setFormatter(
    logging.Formatter("%(asctime)s %(levelname)s %(message)s")
)
file_handler.setLevel(logging.INFO)

app.logger.addHandler(file_handler)
app.logger.setLevel(logging.INFO)

logging.getLogger("helpers").addHandler(file_handler)
logging.getLogger("helpers").setLevel(logging.INFO)

# --------------------------------------------------
# EMAIL CONFIGURATION
# --------------------------------------------------

GMAIL_USER = (
    os.getenv("MAIL_USERNAME")
    or os.getenv("GMAIL_USER")
)

GMAIL_APP_PASSWORD = (
    os.getenv("MAIL_PASSWORD")
    or os.getenv("GMAIL_APP_PASSWORD")
)

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL") or GMAIL_USER

OTP_VALID_MINUTES = 10
OTP_MAX_ATTEMPTS = 5

limiter = helpers.RateLimiter()


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# --------------------------------------------------
# DATABASE MODELS
# --------------------------------------------------

class Lead(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    profession = db.Column(db.String(100))
    email = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(20))
    service = db.Column(db.String(100), nullable=False)
    details = db.Column(db.Text)
    otp_code = db.Column(db.String(6))
    is_verified = db.Column(db.Boolean, default=False)
    otp_created_at = db.Column(db.DateTime)
    otp_attempts = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=utcnow)
    admin_notified = db.Column(db.Boolean, default=False)


class Feedback(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), nullable=False)
    rating = db.Column(db.Integer, nullable=False)
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    email_sent = db.Column(db.Boolean, default=False)


# --------------------------------------------------
# DATABASE INITIALIZATION AND MIGRATION
# --------------------------------------------------

def ensure_schema():
    db.create_all()

    inspector = inspect(db.engine)
    existing = {
        column["name"]
        for column in inspector.get_columns("lead")
    }

    wanted = {
        "profession": "VARCHAR(100)",
        "otp_created_at": "DATETIME",
        "otp_attempts": "INTEGER DEFAULT 0",
        "created_at": "DATETIME",
        "admin_notified": "BOOLEAN DEFAULT 0",
    }

    with db.engine.begin() as conn:
        for column, definition in wanted.items():
            if column not in existing:
                conn.execute(
                    text(
                        f"ALTER TABLE lead ADD COLUMN {column} {definition}"
                    )
                )
                app.logger.info("Added database column: %s", column)


with app.app_context():
    ensure_schema()


# --------------------------------------------------
# COMMON FUNCTIONS
# --------------------------------------------------

def send_mail(to, subject, body, reply_to=None):
    return helpers.send_mail(
        GMAIL_USER,
        GMAIL_APP_PASSWORD,
        to,
        subject,
        body,
        reply_to=reply_to,
        logger=app.logger
    )


def client_ip():
    return request.remote_addr or "unknown"


def too_many(bucket, limit, window_seconds, who=None):
    key = f"{bucket}:{who or client_ip()}"
    return not limiter.allow(key, limit, window_seconds)


def json_body():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else None


def fail(message, status=400, **extra):
    return jsonify({
        "success": False,
        "message": message,
        **extra
    }), status


def current_account():
    lead_id = session.get("lead_id")

    lead = db.session.get(Lead, lead_id) if lead_id else None

    return lead if lead and lead.is_verified else None


def login_required_api(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        account = current_account()

        if not account:
            session.pop("lead_id", None)
            return fail(
                "Please log in to submit your requirements.",
                401,
                login_required=True
            )

        g.account = account
        return view(*args, **kwargs)

    return wrapper


# --------------------------------------------------
# OTP FUNCTIONS
# --------------------------------------------------

def issue_otp(lead):
    lead.otp_code = str(secrets.randbelow(900000) + 100000)
    lead.otp_created_at = utcnow()
    lead.otp_attempts = 0

    db.session.commit()

    subject, body = helpers.build_otp_email(lead.otp_code)

    return send_mail(lead.email, subject, body)


def notify_admin_requirement(lead, new_account):
    subject, body = helpers.build_requirement_email(
        lead.name,
        lead.profession,
        lead.email,
        lead.phone,
        lead.service,
        lead.details,
        lead.created_at or utcnow(),
        new_account
    )

    try:
        sent = send_mail(
            ADMIN_EMAIL,
            subject,
            body,
            reply_to=lead.email
        )

        lead.admin_notified = sent
        db.session.commit()

        if not sent:
            app.logger.error(
                "Admin notification failed for lead %s",
                lead.id
            )

        return sent

    except Exception:
        db.session.rollback()
        app.logger.exception("Admin notification error")
        return False


# --------------------------------------------------
# SECURITY HEADERS
# --------------------------------------------------

@app.after_request
def security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault(
        "Referrer-Policy",
        "strict-origin-when-cross-origin"
    )
    return response


@app.errorhandler(500)
def server_error(error):
    app.logger.exception("Server error: %s", error)

    if request.path.startswith("/api/"):
        return fail("Something went wrong on our side. Please try again.", 500)

    return "Internal Server Error", 500


@app.errorhandler(413)
def too_large(error):
    return fail("Request is too large.", 413)


# --------------------------------------------------
# WEBSITE ROUTES
# --------------------------------------------------

@app.route("/")
def home():
    return render_template("index.html")


@app.route("/services")
def services():
    return render_template("services.html")


@app.route("/work")
def work():
    return render_template("work.html")


@app.route("/pricing")
def pricing():
    return render_template("pricing.html")


@app.route("/about")
def about():
    return render_template("about.html")


@app.route("/process")
def process():
    return redirect(url_for("home") + "#process")


@app.route("/capabilities")
def capabilities():
    return redirect(url_for("services"))


@app.route("/insights")
def insights():
    return redirect(url_for("home"))


@app.route("/faq")
def faq():
    return redirect(url_for("pricing") + "#faq")


@app.route("/register")
def register():
    if current_account():
        return redirect(url_for("pricing") + "#contact")

    return render_template("register.html")


@app.route("/login")
def login():
    if current_account():
        return redirect(url_for("pricing") + "#contact")

    return render_template("login.html")


@app.route("/feedback")
def feedback_page():
    session["feedback_opened"] = time.time()
    return render_template("feedback.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


# --------------------------------------------------
# API: REGISTER
# --------------------------------------------------

@app.route("/api/register", methods=["POST"])
def api_register():
    data = json_body()

    if data is None:
        return fail("Invalid request.")

    clean, error = helpers.validate_registration(data)

    if error:
        return fail(error)

    if too_many("otp-ip", 8, 3600):
        return fail("Too many attempts. Try again later.", 429)

    if too_many("otp-email", 3, 3600, who=clean["email"]):
        return fail("Too many attempts. Try again later.", 429)

    existing = Lead.query.filter(
        db.func.lower(Lead.email) == clean["email"],
        Lead.is_verified.is_(True)
    ).first()

    if existing:
        return fail(
            "An account with this email already exists. Please log in.",
            409
        )

    lead = Lead(
        name=clean["name"],
        profession=clean["profession"],
        email=clean["email"],
        phone=clean["phone"],
        service=clean["service"],
        details=clean["details"],
        is_verified=False,
        created_at=utcnow()
    )

    try:
        db.session.add(lead)
        db.session.commit()

        if not issue_otp(lead):
            db.session.delete(lead)
            db.session.commit()

            return fail(
                "Verification email could not be sent. Please check email configuration and try again.",
                502
            )

        session.clear()
        session["pending_lead_id"] = lead.id
        session["pending_purpose"] = "register"

        return jsonify({
            "success": True,
            "message": "A 6-digit verification code has been sent to your email."
        })

    except Exception:
        db.session.rollback()
        app.logger.exception("Registration failed")
        return fail("Registration failed. Please try again.", 500)


# --------------------------------------------------
# API: LOGIN
# --------------------------------------------------

@app.route("/api/login", methods=["POST"])
def api_login():
    data = json_body()

    if data is None:
        return fail("Invalid request.")

    clean, error = helpers.validate_login(data)

    if error:
        return fail(error)

    if too_many("otp-ip", 8, 3600):
        return fail("Too many attempts. Try again later.", 429)

    if too_many("otp-email", 3, 3600, who=clean["email"]):
        return fail("Too many attempts. Try again later.", 429)

    lead = (
        Lead.query.filter(
            db.func.lower(Lead.email) == clean["email"],
            Lead.is_verified.is_(True)
        )
        .order_by(Lead.id.desc())
        .first()
    )

    session.clear()

    if lead:
        if not issue_otp(lead):
            return fail(
                "Verification email could not be sent. Please try again later.",
                502
            )

        session["pending_lead_id"] = lead.id
        session["pending_purpose"] = "login"

    return jsonify({
        "success": True,
        "message": "If an account exists, a verification code has been sent."
    })


# --------------------------------------------------
# API: VERIFY OTP
# --------------------------------------------------

@app.route("/api/verify-otp", methods=["POST"])
def api_verify_otp():
    data = json_body()

    if data is None:
        return fail("Invalid request.")

    user_otp = str(data.get("otp", "")).strip()

    if not user_otp.isdigit() or len(user_otp) != 6:
        return fail("Please enter a valid 6-digit OTP.")

    lead_id = session.get("pending_lead_id")

    lead = db.session.get(Lead, lead_id) if lead_id else None

    if not lead or not lead.otp_code:
        return fail("Your verification session has expired.", 400)

    if (lead.otp_attempts or 0) >= OTP_MAX_ATTEMPTS:
        return fail("Too many wrong attempts. Request a new code.", 429)

    if (
        not lead.otp_created_at
        or utcnow() - lead.otp_created_at > timedelta(minutes=OTP_VALID_MINUTES)
    ):
        return fail("OTP expired. Request a new code.", 400)

    if not secrets.compare_digest(lead.otp_code, user_otp):
        lead.otp_attempts = (lead.otp_attempts or 0) + 1
        db.session.commit()

        return fail("Invalid OTP. Please try again.", 400)

    purpose = session.get("pending_purpose")

    lead.is_verified = True
    lead.otp_code = None
    lead.otp_attempts = 0

    db.session.commit()

    session.clear()
    session.permanent = True
    session["lead_id"] = lead.id
    session["user_name"] = lead.name
    session["user_email"] = lead.email

    if purpose == "register":
        notify_admin_requirement(lead, new_account=True)

    return jsonify({
        "success": True,
        "message": "OTP verified successfully. You are now logged in."
    })


# --------------------------------------------------
# API: CONTACT / REQUIREMENT
# --------------------------------------------------

@app.route("/api/contact", methods=["POST"])
@login_required_api
def api_contact():
    data = json_body()

    if data is None:
        return fail("Invalid request.")

    clean, error = helpers.validate_requirement(data)

    if error:
        return fail(error)

    account = g.account

    if too_many("requirement", 10, 3600, who=str(account.id)):
        return fail("Too many submissions. Try again later.", 429)

    lead = Lead(
        name=clean["name"] or account.name,
        profession=account.profession,
        email=account.email,
        phone=account.phone,
        service=clean["service"],
        details=clean["details"],
        is_verified=True,
        created_at=utcnow()
    )

    try:
        db.session.add(lead)
        db.session.commit()

        notify_admin_requirement(lead, new_account=False)

        return jsonify({
            "success": True,
            "message": "Thank you! Your requirements have been received."
        })

    except Exception:
        db.session.rollback()
        app.logger.exception("Contact submission failed")
        return fail("Could not submit your requirements.", 500)


# --------------------------------------------------
# API: FEEDBACK
# --------------------------------------------------

@app.route("/api/feedback", methods=["POST"])
def api_feedback():
    data = json_body()

    if data is None:
        return fail("Invalid request.")

    if str(data.get("website", "")).strip():
        return jsonify({
            "success": True,
            "message": "Thank you for your feedback!"
        })

    opened = session.get("feedback_opened")

    if not opened:
        return fail("Session expired. Reload the page.", 400)

    if time.time() - opened < 3:
        return fail("Please wait a moment before submitting.", 400)

    if too_many("feedback", 5, 3600):
        return fail("Too many submissions. Try again later.", 429)

    clean, error = helpers.validate_feedback(data)

    if error:
        return fail(error)

    entry = Feedback(
        name=clean["name"],
        email=clean["email"],
        rating=clean["rating"],
        message=clean["message"],
        created_at=utcnow(),
        email_sent=False
    )

    try:
        db.session.add(entry)
        db.session.commit()

        subject, body = helpers.build_feedback_email(
            entry.name,
            entry.email,
            entry.rating,
            entry.message,
            entry.created_at
        )

        entry.email_sent = send_mail(
            ADMIN_EMAIL,
            subject,
            body,
            reply_to=entry.email
        )

        db.session.commit()

        session["feedback_opened"] = time.time()

        return jsonify({
            "success": True,
            "message": "Thank you! Your feedback has been submitted."
        })

    except Exception:
        db.session.rollback()
        app.logger.exception("Feedback submission failed")
        return fail("Could not submit feedback.", 500)


# --------------------------------------------------
# HEALTH CHECK
# --------------------------------------------------

@app.route("/health")
def health():
    return jsonify({"status": "ok"}), 200


# --------------------------------------------------
# LOCAL DEVELOPMENT
# --------------------------------------------------

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", 5000)),
        debug=False
    )