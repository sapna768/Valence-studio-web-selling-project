import os
import re
import ssl
import smtplib
import logging
import threading
import time

from collections import defaultdict, deque
from datetime import timezone, timedelta
from email.message import EmailMessage

# --------------------------------------------------
# SMTP CONFIGURATION
# --------------------------------------------------

SMTP_HOST = os.getenv("MAIL_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("MAIL_PORT", "465"))
SMTP_TIMEOUT = 20

IST = timezone(timedelta(hours=5, minutes=30))

EMAIL_RE = re.compile(
    r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}$"
)

PHONE_RE = re.compile(r"^[0-9+()\-\s]{7,20}$")

_log = logging.getLogger("helpers")


# --------------------------------------------------
# CLEANING
# --------------------------------------------------

def clean_line(value, max_len=None):
    value = "" if value is None else str(value)
    value = re.sub(r"[\x00-\x1f\x7f]+", " ", value)
    value = re.sub(r"\s{2,}", " ", value).strip()
    return value[:max_len] if max_len else value


def clean_text(value):
    value = "" if value is None else str(value)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", value)
    return value.strip()


def is_valid_email(email):
    return (
        bool(email)
        and len(email) <= 100
        and bool(EMAIL_RE.match(email))
    )


# --------------------------------------------------
# REGISTRATION VALIDATION
# --------------------------------------------------

def validate_registration(data):
    name = clean_line(data.get("name"))
    profession = clean_line(data.get("profession"))
    email = clean_line(data.get("email")).lower()
    phone = clean_line(data.get("phone"))
    service = clean_line(data.get("service"))
    details = clean_text(data.get("details"))

    if not 2 <= len(name) <= 100:
        return None, "Please enter your full name or company."

    if not 2 <= len(profession) <= 100:
        return None, "Please enter your profession or industry."

    if not is_valid_email(email):
        return None, "Please enter a valid email address."

    if not PHONE_RE.match(phone) or sum(c.isdigit() for c in phone) < 7:
        return None, "Please enter a valid phone number."

    if not 1 <= len(service) <= 100:
        return None, "Please choose a package."

    if not 10 <= len(details) <= 3000:
        return None, "Please describe your requirements."

    return {
        "name": name,
        "profession": profession,
        "email": email,
        "phone": phone,
        "service": service,
        "details": details
    }, None


# --------------------------------------------------
# LOGIN VALIDATION
# --------------------------------------------------

def validate_login(data):
    email = clean_line(data.get("email")).lower()

    if not is_valid_email(email):
        return None, "Please enter a valid email address."

    return {"email": email}, None


# --------------------------------------------------
# REQUIREMENT VALIDATION
# --------------------------------------------------

def validate_requirement(data):
    name = clean_line(data.get("name"), 100)
    service = clean_line(data.get("service"))
    details = clean_text(data.get("details"))

    if name and len(name) < 2:
        return None, "Please enter a valid name."

    if not 1 <= len(service) <= 100:
        return None, "Please choose a package."

    if len(details) > 3000:
        return None, "Requirements are too long."

    return {
        "name": name,
        "service": service,
        "details": details
    }, None


# --------------------------------------------------
# FEEDBACK VALIDATION
# --------------------------------------------------

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
        return None, "Please enter your name."

    if not is_valid_email(email):
        return None, "Please enter a valid email address."

    if rating is None or not 1 <= rating <= 5:
        return None, "Please select a rating."

    if not 10 <= len(message) <= 2000:
        return None, "Please write your feedback."

    return {
        "name": name,
        "email": email,
        "rating": rating,
        "message": message
    }, None


# --------------------------------------------------
# RATE LIMITER
# --------------------------------------------------

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

            if len(self._hits) > 5000:
                for k in [k for k, v in self._hits.items() if not v]:
                    del self._hits[k]

            return True


# --------------------------------------------------
# TIME FORMAT
# --------------------------------------------------

def format_time(dt_utc):
    aware = dt_utc.replace(tzinfo=timezone.utc)

    return (
        f"{aware.astimezone(IST):%d %b %Y, %I:%M %p} IST "
        f"({aware:%Y-%m-%d %H:%M} UTC)"
    )


# --------------------------------------------------
# EMAIL TEMPLATES
# --------------------------------------------------

def build_otp_email(otp_code):
    subject = "Valence Studio - Project Verification Code"

    body = (
        "Hello,\n\n"
        f"Your 6-digit OTP code is: {otp_code}\n\n"
        "This code is valid for 10 minutes.\n"
        "Do not share this code with anyone."
    )

    return subject, body


def build_requirement_email(
    name, profession, email, phone,
    service, details, created_at, new_account
):
    kind = (
        "New client account + project requirements"
        if new_account else "New project requirements"
    )

    subject = f"{kind}: {name} ({service})"

    body = (
        f"{kind} received on Valence Studio website.\n\n"
        f"Name: {name}\n"
        f"Profession: {profession or '-'}\n"
        f"Email: {email}\n"
        f"Phone: {phone or '-'}\n"
        f"Package: {service}\n"
        f"Submitted: {format_time(created_at)}\n\n"
        "Project Requirements:\n"
        f"{details or '(none provided)'}\n"
    )

    return subject, body


def build_feedback_email(name, email, rating, message, created_at):
    stars = "★" * rating + "☆" * (5 - rating)

    subject = f"New Website Feedback: {rating}/5 stars from {name}"

    body = (
        "New feedback received on Valence Studio.\n\n"
        f"Customer Name: {name}\n"
        f"Customer Email: {email}\n"
        f"Rating: {stars} ({rating}/5)\n"
        f"Submitted: {format_time(created_at)}\n\n"
        "Feedback Message:\n"
        f"{message}\n"
    )

    return subject, body


# --------------------------------------------------
# SMTP EMAIL SENDING
# --------------------------------------------------

def send_mail(
    user,
    password,
    to,
    subject,
    body,
    reply_to=None,
    logger=None
):
    logger = logger or _log

    if not user or not password:
        logger.error(
            "Email credentials missing. Configure MAIL_USERNAME and MAIL_PASSWORD."
        )
        return False

    if not to:
        logger.error("Email recipient is missing.")
        return False

    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = user
        msg["To"] = to

        if reply_to:
            msg["Reply-To"] = reply_to

        msg.set_content(body)

        context = ssl.create_default_context()

        if SMTP_PORT == 465:
            with smtplib.SMTP_SSL(
                SMTP_HOST,
                SMTP_PORT,
                timeout=SMTP_TIMEOUT,
                context=context
            ) as server:
                server.login(user, password.replace(" ", ""))
                server.send_message(msg)

        else:
            with smtplib.SMTP(
                SMTP_HOST,
                SMTP_PORT,
                timeout=SMTP_TIMEOUT
            ) as server:
                server.ehlo()
                server.starttls(context=context)
                server.ehlo()
                server.login(user, password.replace(" ", ""))
                server.send_message(msg)

        logger.info("Email successfully sent to %s", to)
        return True

    except Exception:
        logger.exception(
            "Email delivery failed. Recipient=%s Subject=%s",
            to,
            subject
        )
        return False