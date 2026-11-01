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

user = os.getenv("GMAIL_USER")
password = os.getenv("GMAIL_APP_PASSWORD")
admin = os.getenv("ADMIN_EMAIL") or user

if not user or not password:
    raise SystemExit("GMAIL_USER and GMAIL_APP_PASSWORD must be set in .env")

now = datetime.now(timezone.utc).replace(tzinfo=None)
subject, body = helpers.build_feedback_email(
    "Test Customer", "test@example.com", 5,
    "This is a test notification sent by test_email.py. If you can read this, feedback emails work.", now)

ok = helpers.send_mail(user, password, admin, "[TEST] " + subject, body, reply_to="test@example.com")
print("\nSENT - check the inbox (and spam folder) of:", admin if ok else "")
if not ok:
    print("FAILED - see the error above. Most common cause: wrong/old App Password, or 2-Step Verification is off.")
