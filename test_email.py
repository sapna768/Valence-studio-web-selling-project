"""Sends ONE real test email to the admin address using the settings in .env.

Run it from the project folder with the venv active:
    python test_email.py
"""
import logging
import os
from datetime import datetime, timezone

from dotenv import load_dotenv

import helpers

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
load_dotenv()

admin = os.getenv("ADMIN_EMAIL") or os.getenv("BREVO_SENDER") or os.getenv("GMAIL_USER")
if not admin:
    raise SystemExit("Set ADMIN_EMAIL (or GMAIL_USER) in .env")
print("Sending via:", "Brevo HTTPS API" if os.getenv("BREVO_API_KEY") else "Gmail SMTP")

now = datetime.now(timezone.utc).replace(tzinfo=None)
subject, body = helpers.build_feedback_email(
    "Test Customer", "test@example.com", 5,
    "This is a test notification sent by test_email.py. If you can read this, feedback emails work.", now)

ok = helpers.deliver(admin, "[TEST] " + subject, body, reply_to="test@example.com")
print("\nSENT - check the inbox (and spam folder) of:", admin if ok else "")
if not ok:
    print("FAILED - see the error above. Most common cause: wrong/old App Password, or 2-Step Verification is off.")
