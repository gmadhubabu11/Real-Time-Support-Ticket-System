import logging
import smtplib
from email.message import EmailMessage

from app.config import settings

logger = logging.getLogger(__name__)


def send_email(to: str, subject: str, body: str) -> None:
    """Sync SMTP send (use with BackgroundTasks). No-op when SMTP_HOST is empty."""
    if not settings.SMTP_HOST or not to:
        return
    try:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = settings.SMTP_FROM, to, subject
        msg.set_content(body)
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as s:
            s.starttls()
            if settings.SMTP_USER:
                s.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            s.send_message(msg)
    except Exception as exc:  # never break the request because of email
        logger.warning("Email to %s failed: %s", to, exc)
