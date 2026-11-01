"""Pure helper functions for Valence Studio.

Kept free of Flask / SQLAlchemy imports so they can be tested on their own.
"""
import json
import logging
import os
import re
import smtplib
import ssl
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict, deque
from datetime import timezone, timedelta
from email.message import EmailMessage

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
SMTP_TIMEOUT = 20

IST = timezone(timedelta(hours=5, minutes=30))

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}$")
PHONE_RE = re.compile(r"^[0-9+()\-\s]{7,20}$")

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Cleaning helpers
# ---------------------------------------------------------------------------
def clean_line(value, max_len=None):
    """Single-line text: control characters (incl. newlines) become spaces."""
    value = "" if value is None else str(value)
    value = re.sub(r"[\x00-\x1f\x7f]+", " ", value)
    value = re.sub(r"\s{2,}", " ", value).strip()
    return value[:max_len] if max_len else value


def clean_text(value):
    """Multi-line text: keep newlines/tabs, drop other control characters."""
    value = "" if value is None else str(value)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", value)
    return value.strip()


def is_valid_email(email):
    return bool(email) and len(email) <= 100 and bool(EMAIL_RE.match(email))


# ---------------------------------------------------------------------------
# Validation. Each returns (clean_dict, error_message). error_message is None when OK.
# ---------------------------------------------------------------------------
def validate_registration(data):
    name = clean_line(data.get("name"))
    profession = clean_line(data.get("profession"))
    email = clean_line(data.get("email")).lower()
    phone = clean_line(data.get("phone"))
    service = clean_line(data.get("service"))
    details = clean_text(data.get("details"))

    if not 2 <= len(name) <= 100:
        return None, "Please enter your full name or company (2-100 characters)."
    if not 2 <= len(profession) <= 100:
        return None, "Please enter your profession or industry."
    if not is_valid_email(email):
        return None, "Please enter a valid email address."
    if not PHONE_RE.match(phone) or sum(c.isdigit() for c in phone) < 7:
        return None, "Please enter a valid WhatsApp / phone number."
    if not 1 <= len(service) <= 100:
        return None, "Please choose a package."
    if not 10 <= len(details) <= 3000:
        return None, "Please describe your requirements (10-3000 characters)."

    return {"name": name, "profession": profession, "email": email, "phone": phone,
            "service": service, "details": details}, None


def validate_login(data):
    email = clean_line(data.get("email")).lower()
    if not is_valid_email(email):
        return None, "Please enter a valid email address."
    return {"email": email}, None


def validate_requirement(data):
    name = clean_line(data.get("name"), 100)
    service = clean_line(data.get("service"))
    details = clean_text(data.get("details"))

    if name and len(name) < 2:
        return None, "Please enter a valid name."
    if not 1 <= len(service) <= 100:
        return None, "Please choose a package."
    if len(details) > 3000:
        return None, "Requirements are too long (max 3000 characters)."
    return {"name": name, "service": service, "details": details}, None


def validate_feedback(data):
    name = clean_line(data.get("name"))
    email = clean_line(data.get("email")).lower()
    message = clean_text(data.get("message"))

    raw_rating = data.get("rating")
    rating = None
    if isinstance(raw_rating, int) and not isinstance(raw_rating, bool):
        rating = raw_rating
    elif isinstance(raw_rating, str) and re.fullmatch(r"[1-5]", raw_rating.strip()):
        rating = int(raw_rating.strip())

    if not 2 <= len(name) <= 100:
        return None, "Please enter your name (2-100 characters)."
    if not is_valid_email(email):
        return None, "Please enter a valid email address."
    if rating is None or not 1 <= rating <= 5:
        return None, "Please select a star rating from 1 to 5."
    if not 10 <= len(message) <= 2000:
        return None, "Please write your feedback (10-2000 characters)."

    return {"name": name, "email": email, "rating": rating, "message": message}, None


# ---------------------------------------------------------------------------
# Simple in-memory rate limiter (per process)
# ---------------------------------------------------------------------------
class RateLimiter:
    def __init__(self):
        self._hits = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key, limit, window_seconds, now=None):
        now = time.time() if now is None else now
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= now - window_seconds:
                hits.popleft()
            if len(hits) >= limit:
                return False
            hits.append(now)
            if len(self._hits) > 5000:  # keep memory bounded
                for k in [k for k, v in self._hits.items() if not v]:
                    del self._hits[k]
            return True


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------
def format_time(dt_utc):
    """dt_utc is a naive datetime in UTC. Returns e.g. '01 Nov 2026, 11:45 AM IST (2026-11-01 06:15 UTC)'."""
    aware = dt_utc.replace(tzinfo=timezone.utc)
    return f"{aware.astimezone(IST):%d %b %Y, %I:%M %p} IST ({aware:%Y-%m-%d %H:%M} UTC)"


def build_feedback_email(name, email, rating, message, created_at):
    stars = "★" * rating + "☆" * (5 - rating)
    subject = f"New Website Feedback: {rating}/5 stars from {name}"
    body = (
        "You received new feedback on the Valence Studio website.\n\n"
        f"Customer Name : {name}\n"
        f"Customer Email: {email}\n"
        f"Rating        : {stars} ({rating}/5)\n"
        f"Submitted     : {format_time(created_at)}\n\n"
        "Feedback Message:\n"
        "-----------------\n"
        f"{message}\n"
    )
    return subject, body


def build_requirement_email(name, profession, email, phone, service, details, created_at, new_account):
    kind = "New client account + project requirements" if new_account else "New project requirements"
    subject = f"{kind}: {name} ({service})"
    body = (
        f"{kind} received on the Valence Studio website.\n\n"
        f"Name / Company: {name}\n"
        f"Profession    : {profession or '-'}\n"
        f"Email         : {email}\n"
        f"Phone/WhatsApp: {phone or '-'}\n"
        f"Package       : {service}\n"
        f"Submitted     : {format_time(created_at)}\n\n"
        "Project Requirements:\n"
        "---------------------\n"
        f"{details or '(none provided)'}\n"
    )
    return subject, body


def build_otp_email(otp_code):
    subject = "Valence Studio - Project Verification Code"
    body = (
        "Hello,\n\n"
        f"Your 6-digit OTP code for Valence Studio verification is: {otp_code}\n\n"
        "This code is valid for 10 minutes. Do not share this code with anyone."
    )
    return subject, body


def send_mail(user, password, to, subject, body, reply_to=None, logger=None):
    """Send a plain-text email through Gmail SMTP. Returns True on success, False on any failure.

    Credentials are only ever used here, on the server.
    """
    logger = logger or _log
    if not user or not password:
        logger.error("Email not sent: GMAIL_USER / GMAIL_APP_PASSWORD are not configured.")
        return False
    if not to:
        logger.error("Email not sent: no recipient address.")
        return False
    try:
        msg = EmailMessage()  # raises on header injection (newlines)
        msg["Subject"] = subject
        msg["From"] = user
        msg["To"] = to
        if reply_to:
            msg["Reply-To"] = reply_to
        msg.set_content(body)

        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT, context=context) as server:
            server.login(user, password.replace(" ", ""))  # Google shows app passwords with spaces
            server.send_message(msg)
        return True
    except Exception:
        logger.exception("Email delivery failed (to=%s, subject=%r)", to, subject)
        return False


BREVO_URL = "https://api.brevo.com/v3/smtp/email"


def send_mail_brevo(api_key, sender_email, to, subject, body, reply_to=None, logger=None, sender_name="Valence Studio"):
    """Send through Brevo's HTTPS API (port 443). Works on hosts that block SMTP ports (e.g. Render free)."""
    logger = logger or _log
    if not api_key or not sender_email or not to:
        logger.error("Email not sent: BREVO_API_KEY / sender / recipient missing.")
        return False
    payload = {
        "sender": {"name": sender_name, "email": sender_email},
        "to": [{"email": to}],
        "subject": subject,
        "textContent": body,
    }
    if reply_to:
        payload["replyTo"] = {"email": reply_to}
    try:
        req = urllib.request.Request(
            BREVO_URL, data=json.dumps(payload).encode("utf-8"), method="POST",
            headers={"api-key": api_key, "content-type": "application/json", "accept": "application/json"})
        with urllib.request.urlopen(req, timeout=SMTP_TIMEOUT) as resp:
            return 200 <= resp.status < 300
    except urllib.error.HTTPError as exc:
        logger.error("Brevo rejected the email (HTTP %s): %s", exc.code, exc.read().decode("utf-8", "replace")[:300])
        return False
    except Exception:
        logger.exception("Email delivery via Brevo failed (to=%s, subject=%r)", to, subject)
        return False


def deliver(to, subject, body, reply_to=None, logger=None):
    """Send one email using whatever is configured in the environment.

    BREVO_API_KEY (+ BREVO_SENDER)  -> HTTPS API (use this on Render free)
    otherwise GMAIL_USER + GMAIL_APP_PASSWORD -> Gmail SMTP (fine on your own computer)
    """
    api_key = os.getenv("BREVO_API_KEY")
    if api_key:
        sender = os.getenv("BREVO_SENDER") or os.getenv("GMAIL_USER")
        return send_mail_brevo(api_key, sender, to, subject, body, reply_to, logger)
    return send_mail(os.getenv("GMAIL_USER"), os.getenv("GMAIL_APP_PASSWORD"), to, subject, body, reply_to, logger)
