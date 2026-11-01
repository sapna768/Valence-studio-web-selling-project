import os
import secrets
import time
import logging
from datetime import datetime, timedelta, timezone
from functools import wraps
from logging.handlers import RotatingFileHandler

from dotenv import load_dotenv
from flask import (Flask, render_template, request, jsonify, session,
                   redirect, url_for, g)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect, text
from werkzeug.middleware.proxy_fix import ProxyFix

import helpers

# Load .env variables
load_dotenv()

app = Flask(__name__, template_folder='templates', static_folder='static')

# ---------------------------------------------------------
# CONFIG
# ---------------------------------------------------------
_secret_key = os.getenv("SECRET_KEY")
if not _secret_key:
    # Never ship a hard-coded secret. Without SECRET_KEY in .env a temporary key is used,
    # which means customers are logged out every time the server restarts.
    _secret_key = secrets.token_hex(32)
    print("[Config] SECRET_KEY is not set in .env - using a temporary key (set it for production).")
app.secret_key = _secret_key

app.config.update(
    SQLALCHEMY_DATABASE_URI='sqlite:///valence_leads.db',   # stored in ./instance/valence_leads.db
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE") == "1",   # set COOKIE_SECURE=1 when the site is on https
    PERMANENT_SESSION_LIFETIME=timedelta(days=7),
    MAX_CONTENT_LENGTH=64 * 1024,
)

# When deployed behind a proxy (Render / Heroku / nginx) set TRUST_PROXY=1 so rate limits use the real client IP.
if os.getenv("TRUST_PROXY") == "1":
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

db = SQLAlchemy(app)

# Logging (errors, including email failures, go to instance/app.log as well as the console)
os.makedirs(app.instance_path, exist_ok=True)
_file_handler = RotatingFileHandler(os.path.join(app.instance_path, 'app.log'),
                                    maxBytes=512 * 1024, backupCount=3, encoding='utf-8')
_file_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
_file_handler.setLevel(logging.INFO)
app.logger.addHandler(_file_handler)
app.logger.setLevel(logging.INFO)
logging.getLogger('helpers').addHandler(_file_handler)
logging.getLogger('helpers').setLevel(logging.INFO)

GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
# Admin notifications go here. Defaults to the Gmail account that sends the mail.
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL") or GMAIL_USER

OTP_VALID_MINUTES = 10
OTP_MAX_ATTEMPTS = 5

limiter = helpers.RateLimiter()


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------
# MODELS
# ---------------------------------------------------------
class Lead(db.Model):
    """A customer account / requirement submission."""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    profession = db.Column(db.String(100), nullable=True)
    email = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(20), nullable=True)
    service = db.Column(db.String(100), nullable=False)
    details = db.Column(db.Text, nullable=True)
    otp_code = db.Column(db.String(6), nullable=True)
    is_verified = db.Column(db.Boolean, default=False)
    otp_created_at = db.Column(db.DateTime, nullable=True)
    otp_attempts = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=utcnow)
    admin_notified = db.Column(db.Boolean, default=False)


class Feedback(db.Model):
    """Website feedback. Kept separate from customer requirements."""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), nullable=False)
    rating = db.Column(db.Integer, nullable=False)
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    email_sent = db.Column(db.Boolean, default=False)


def ensure_schema():
    """Create missing tables AND add missing columns to the existing `lead` table.

    db.create_all() never alters an existing table. The old database had no `profession`
    column, so every INSERT failed with "table lead has no column named profession"
    (which the browser showed as "Network error").
    """
    db.create_all()
    existing = {c['name'] for c in inspect(db.engine).get_columns('lead')}
    wanted = {
        'profession': 'VARCHAR(100)',
        'otp_created_at': 'DATETIME',
        'otp_attempts': 'INTEGER DEFAULT 0',
        'created_at': 'DATETIME',
        'admin_notified': 'BOOLEAN DEFAULT 0',
    }
    with db.engine.begin() as conn:
        for column, ddl in wanted.items():
            if column not in existing:
                conn.execute(text(f'ALTER TABLE lead ADD COLUMN {column} {ddl}'))
                app.logger.info("Database migration: added column lead.%s", column)


with app.app_context():
    ensure_schema()


# ---------------------------------------------------------
# HELPERS
# ---------------------------------------------------------
def send_mail(to, subject, body, reply_to=None):
    return helpers.send_mail(GMAIL_USER, GMAIL_APP_PASSWORD, to, subject, body,
                             reply_to=reply_to, logger=app.logger)


def client_ip():
    return request.remote_addr or 'unknown'


def too_many(bucket, limit, window_seconds, who=None):
    """True when the caller exceeded `limit` requests in `window_seconds`."""
    return not limiter.allow(f"{bucket}:{who or client_ip()}", limit, window_seconds)


def json_body():
    """JSON object from the request, or None. Requiring JSON also blocks cross-site form posts."""
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else None


def fail(message, status=400, **extra):
    return jsonify({"success": False, "message": message, **extra}), status


def current_account():
    lead_id = session.get('lead_id')
    lead = db.session.get(Lead, lead_id) if lead_id else None
    return lead if lead and lead.is_verified else None


def login_required_api(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        account = current_account()
        if not account:
            session.pop('lead_id', None)
            return fail("Please log in to submit your requirements.", 401, login_required=True)
        g.account = account
        return view(*args, **kwargs)
    return wrapper


def issue_otp(lead):
    """Generate a fresh OTP for the lead and email it. Returns True if the email was sent."""
    lead.otp_code = f"{secrets.randbelow(900000) + 100000}"
    lead.otp_created_at = utcnow()
    lead.otp_attempts = 0
    db.session.commit()
    subject, body = helpers.build_otp_email(lead.otp_code)
    return send_mail(lead.email, subject, body)


def notify_admin_requirement(lead, new_account):
    """Email the admin about a requirement. Never raises; the requirement is already saved."""
    subject, body = helpers.build_requirement_email(
        lead.name, lead.profession, lead.email, lead.phone, lead.service,
        lead.details, lead.created_at or utcnow(), new_account)
    sent = send_mail(ADMIN_EMAIL, subject, body, reply_to=lead.email)
    lead.admin_notified = sent
    db.session.commit()
    if not sent:
        app.logger.error("Admin notification FAILED for requirement lead_id=%s (saved in database).", lead.id)
    return sent


@app.after_request
def security_headers(resp):
    resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
    resp.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
    resp.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    return resp


@app.errorhandler(500)
def server_error(_e):
    if request.path.startswith('/api/'):
        return fail("Something went wrong on our side. Please try again.", 500)
    return "Internal Server Error", 500


@app.errorhandler(413)
def too_large(_e):
    return fail("That request is too large.", 413)


# ---------------------------------------------------------
# MAIN ROUTES (pages)
# ---------------------------------------------------------
@app.route('/')
def home():
    return render_template('index.html')


@app.route('/services')
def services():
    return render_template('services.html')


@app.route('/work')
def work():
    return render_template('work.html')


@app.route('/pricing')
def pricing():
    return render_template('pricing.html')


@app.route('/about')
def about():
    return render_template('about.html')


# Old menu links from the previous design keep working.
@app.route('/process')
def process():
    return redirect(url_for('home') + '#process')


@app.route('/capabilities')
def capabilities():
    return redirect(url_for('services'))


@app.route('/insights')
def insights():
    return redirect(url_for('home'))


@app.route('/faq')
def faq():
    return redirect(url_for('pricing') + '#faq')


@app.route('/register')
def register():
    if current_account():
        return redirect(url_for('pricing') + '#contact')
    return render_template('register.html')


@app.route('/login')
def login():
    if current_account():
        return redirect(url_for('pricing') + '#contact')
    return render_template('login.html')


@app.route('/feedback')
def feedback_page():
    session['feedback_opened'] = time.time()   # used for basic bot protection (see /api/feedback)
    return render_template('feedback.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))


# ---------------------------------------------------------
# API: ACCOUNT (register / login with email OTP)
# ---------------------------------------------------------
@app.route('/api/register', methods=['POST'])
def api_register():
    data = json_body()
    if data is None:
        return fail("Invalid request.")
    clean, error = helpers.validate_registration(data)
    if error:
        return fail(error)

    if too_many('otp-ip', 8, 3600) or too_many('otp-email', 3, 3600, who=clean['email']):
        return fail("Too many attempts. Please try again in a little while.", 429)

    existing = Lead.query.filter(db.func.lower(Lead.email) == clean['email'],
                                 Lead.is_verified.is_(True)).first()
    if existing:
        return fail("An account with this email already exists. Please log in instead.", 409)

    lead = Lead(name=clean['name'], profession=clean['profession'], email=clean['email'],
                phone=clean['phone'], service=clean['service'], details=clean['details'],
                is_verified=False, created_at=utcnow())
    db.session.add(lead)
    db.session.commit()

    if not issue_otp(lead):
        db.session.delete(lead)   # nothing was verified or sent; let the visitor retry cleanly
        db.session.commit()
        return fail("We couldn't send the verification email right now. Please try again in a few minutes.", 502)

    session.clear()
    session['pending_lead_id'] = lead.id
    session['pending_purpose'] = 'register'
    return jsonify({
        "success": True,
        "message": "Proposal submitted! A 6-digit OTP code has been sent to your Email inbox."
    })


@app.route('/api/login', methods=['POST'])
def api_login():
    data = json_body()
    if data is None:
        return fail("Invalid request.")
    clean, error = helpers.validate_login(data)
    if error:
        return fail(error)

    if too_many('otp-ip', 8, 3600) or too_many('otp-email', 3, 3600, who=clean['email']):
        return fail("Too many attempts. Please try again in a little while.", 429)

    lead = (Lead.query.filter(db.func.lower(Lead.email) == clean['email'], Lead.is_verified.is_(True))
            .order_by(Lead.id.desc()).first())
    session.clear()
    if lead:
        if not issue_otp(lead):
            return fail("We couldn't send the verification email right now. Please try again in a few minutes.", 502)
        session['pending_lead_id'] = lead.id
        session['pending_purpose'] = 'login'

    # Same answer whether or not the account exists (does not reveal who has an account).
    return jsonify({"success": True,
                    "message": "If an account exists for this email, a 6-digit code has been sent."})


@app.route('/api/verify-otp', methods=['POST'])
def api_verify_otp():
    data = json_body()
    if data is None:
        return fail("Invalid request.")
    user_otp = str(data.get('otp', '')).strip()

    # The lead being verified comes from the server-side session, never from the browser.
    lead_id = session.get('pending_lead_id')
    lead = db.session.get(Lead, lead_id) if lead_id else None
    if not lead or not lead.otp_code:
        return fail("Your verification session has expired. Please request a new code.", 400)

    if (lead.otp_attempts or 0) >= OTP_MAX_ATTEMPTS:
        return fail("Too many wrong attempts. Please request a new code.", 429)
    if not lead.otp_created_at or utcnow() - lead.otp_created_at > timedelta(minutes=OTP_VALID_MINUTES):
        return fail("This code has expired. Please request a new one.", 400)

    if not secrets.compare_digest(lead.otp_code.encode(), user_otp.encode()):
        lead.otp_attempts = (lead.otp_attempts or 0) + 1
        db.session.commit()
        return fail("Invalid OTP code. Please try again.", 400)

    purpose = session.get('pending_purpose')
    lead.is_verified = True
    lead.otp_code = None
    lead.otp_attempts = 0
    db.session.commit()

    session.clear()
    session.permanent = True
    session['lead_id'] = lead.id
    session['user_name'] = lead.name
    session['user_email'] = lead.email

    if purpose == 'register':
        notify_admin_requirement(lead, new_account=True)

    return jsonify({"success": True, "message": "OTP Verified Successfully! You are now logged in."})


# ---------------------------------------------------------
# API: REQUIREMENT FORM (logged-in customers only)
# ---------------------------------------------------------
@app.route('/api/contact', methods=['POST'])
@login_required_api
def api_contact():
    data = json_body()
    if data is None:
        return fail("Invalid request.")
    clean, error = helpers.validate_requirement(data)
    if error:
        return fail(error)

    if too_many('requirement', 10, 3600, who=str(g.account.id)):
        return fail("You have submitted several requirements recently. Please try again later.", 429)

    account = g.account
    # Identity (email, phone, profession) always comes from the verified account, not the browser.
    lead = Lead(name=clean['name'] or account.name, profession=account.profession,
                email=account.email, phone=account.phone,
                service=clean['service'], details=clean['details'],
                is_verified=True, created_at=utcnow())
    db.session.add(lead)
    db.session.commit()

    notify_admin_requirement(lead, new_account=False)

    return jsonify({"success": True,
                    "message": "Thank you! Your requirements have been received. We will contact you shortly."})


# ---------------------------------------------------------
# API: FEEDBACK (open to everyone)
# ---------------------------------------------------------
@app.route('/api/feedback', methods=['POST'])
def api_feedback():
    data = json_body()
    if data is None:
        return fail("Invalid request.")

    # Honeypot: real visitors never see this field. Bots fill it; pretend success and store nothing.
    if str(data.get('website', '')).strip():
        return jsonify({"success": True, "message": "Thank you for your feedback!"})

    # Time check: the form must have been open for at least 3 seconds.
    opened = session.get('feedback_opened')
    if not opened:
        return fail("Your session expired. Please reload the page and try again.", 400)
    if time.time() - opened < 3:
        return fail("Please take a moment to complete the form, then submit again.", 400)

    if too_many('feedback', 5, 3600):
        return fail("Too many submissions. Please try again later.", 429)

    clean, error = helpers.validate_feedback(data)
    if error:
        return fail(error)

    # 1) Save first so feedback is never lost, even if email delivery fails.
    entry = Feedback(name=clean['name'], email=clean['email'], rating=clean['rating'],
                     message=clean['message'], created_at=utcnow(), email_sent=False)
    db.session.add(entry)
    db.session.commit()

    # 2) Notify the admin.
    subject, body = helpers.build_feedback_email(entry.name, entry.email, entry.rating,
                                                 entry.message, entry.created_at)
    entry.email_sent = send_mail(ADMIN_EMAIL, subject, body, reply_to=entry.email)
    db.session.commit()
    if not entry.email_sent:
        app.logger.error("Feedback id=%s saved but the admin notification email FAILED.", entry.id)

    session['feedback_opened'] = time.time()   # next submission needs a fresh 3 second wait
    return jsonify({"success": True, "message": "Thank you! Your feedback has been submitted."})


if __name__ == '__main__':
    # Local development only (production uses gunicorn, see Procfile).
    # 127.0.0.1 keeps the debugger reachable from this computer only.
    app.run(debug=os.getenv("FLASK_DEBUG", "1") == "1",
            host=os.getenv("HOST", "127.0.0.1"),
            port=int(os.getenv("PORT", "5000")))
