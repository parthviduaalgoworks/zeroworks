"""SMTP email delivery for manually confirmed timesheet reminders."""

from email.message import EmailMessage
import os
import smtplib
import ssl
from typing import Optional


DEFAULT_SMTP_HOST = "smtp.office365.com"
DEFAULT_SMTP_PORT = 587


def send_reminder_email(
    *,
    recipient: str,
    subject: str,
    body: str,
    sender: str,
    smtp_host: str,
    smtp_port: int = DEFAULT_SMTP_PORT,
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> None:
    """Send one reminder through SMTP after the caller confirms the preview."""
    clean_recipient = recipient.strip()
    clean_sender = sender.strip() or (username or "").strip()
    clean_host = smtp_host.strip()
    if not clean_recipient or "@" not in clean_recipient:
        raise ValueError("Enter a valid recipient email address.")
    if not clean_sender or "@" not in clean_sender:
        raise ValueError("Enter a valid sender email address.")
    if not clean_host:
        raise ValueError("Enter an SMTP server host.")
    if not 1 <= smtp_port <= 65535:
        raise ValueError("SMTP port must be between 1 and 65535.")
    if bool(username) != bool(password):
        raise ValueError("Provide both SMTP username and password, or neither.")

    message = EmailMessage()
    message["To"] = clean_recipient
    message["From"] = clean_sender
    message["Subject"] = subject.strip()
    message.set_content(body.strip())

    context = ssl.create_default_context()
    try:
        with smtplib.SMTP(clean_host, smtp_port, timeout=30) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            if username and password:
                server.login(username, password)
            server.send_message(message)
    except smtplib.SMTPResponseException as exc:
        if exc.smtp_code in (530, 535):
            raise RuntimeError(
                "Microsoft 365 rejected SMTP authentication. Verify that both "
                "SMTP username and password are entered, and that SMTP AUTH is "
                "enabled for the mailbox and tenant. If your organization blocks "
                "password-based SMTP AUTH, use an approved mail-sending method."
            ) from exc
        raise RuntimeError(
            f"Email was not sent. SMTP server returned {exc.smtp_code}: "
            f"{exc.smtp_error.decode(errors='replace') if isinstance(exc.smtp_error, bytes) else exc.smtp_error}"
        ) from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise RuntimeError(
            f"Email was not sent. Check the SMTP settings and provider policy: {exc}"
        ) from exc


def smtp_settings_from_environment() -> dict[str, str | int]:
    """Read optional SMTP defaults without persisting secrets in application files."""
    raw_port = os.getenv("SMTP_PORT", str(DEFAULT_SMTP_PORT)).strip()
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise ValueError("SMTP_PORT must be an integer.") from exc
    return {
        "host": os.getenv("SMTP_HOST", DEFAULT_SMTP_HOST).strip(),
        "port": port,
        "sender": os.getenv("SMTP_FROM", "").strip(),
        "username": os.getenv("SMTP_USERNAME", "").strip(),
        "password": os.getenv("SMTP_PASSWORD", ""),
    }
