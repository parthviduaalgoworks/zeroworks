import smtplib
import unittest
from unittest.mock import patch

from email_sender import send_reminder_email, smtp_settings_from_environment


class EmailSenderTests(unittest.TestCase):
    @patch("email_sender.smtplib.SMTP")
    def test_sends_only_after_called_with_recipient_and_message(self, smtp_class: object) -> None:
        server = smtp_class.return_value.__enter__.return_value

        send_reminder_email(
            recipient="teammate@example.com",
            subject="Timesheet reminder",
            body="Please submit your timesheet.",
            sender="manager@example.com",
            smtp_host="smtp.office365.com",
            smtp_port=587,
            username="manager@example.com",
            password="secret",
        )

        smtp_class.assert_called_once_with("smtp.office365.com", 587, timeout=30)
        server.starttls.assert_called_once()
        server.login.assert_called_once_with("manager@example.com", "secret")
        sent = server.send_message.call_args.args[0]
        self.assertEqual(sent["To"], "teammate@example.com")
        self.assertEqual(sent["Subject"], "Timesheet reminder")
        self.assertEqual(sent.get_content().strip(), "Please submit your timesheet.")

    @patch("email_sender.smtplib.SMTP")
    def test_rejects_invalid_recipient_without_opening_smtp(self, smtp_class: object) -> None:
        with self.assertRaisesRegex(ValueError, "recipient email"):
            send_reminder_email(
                recipient="not-an-email",
                subject="Reminder",
                body="Please submit your timesheet.",
                sender="manager@example.com",
                smtp_host="smtp.office365.com",
            )
        smtp_class.assert_not_called()

    @patch("email_sender.smtplib.SMTP")
    def test_requires_username_and_password_together(self, smtp_class: object) -> None:
        with self.assertRaisesRegex(ValueError, "both SMTP username"):
            send_reminder_email(
                recipient="teammate@example.com",
                subject="Reminder",
                body="Please submit your timesheet.",
                sender="manager@example.com",
                smtp_host="smtp.office365.com",
                username="manager@example.com",
            )
        smtp_class.assert_not_called()

    @patch("email_sender.smtplib.SMTP")
    def test_uses_smtp_username_when_sender_is_blank(self, smtp_class: object) -> None:
        server = smtp_class.return_value.__enter__.return_value

        send_reminder_email(
            recipient="teammate@example.com",
            subject="Timesheet reminder",
            body="Please submit your timesheet.",
            sender=" ",
            smtp_host="smtp.office365.com",
            username="manager@example.com",
            password="secret",
        )

        sent = server.send_message.call_args.args[0]
        self.assertEqual(sent["From"], "manager@example.com")

    @patch("email_sender.smtplib.SMTP")
    def test_explains_microsoft_365_smtp_auth_rejection(
        self, smtp_class: object
    ) -> None:
        server = smtp_class.return_value.__enter__.return_value
        server.send_message.side_effect = smtplib.SMTPResponseException(
            530, b"5.7.57 Client not authenticated to send mail"
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "Verify that both SMTP username and password are entered",
        ):
            send_reminder_email(
                recipient="teammate@example.com",
                subject="Reminder",
                body="Please submit your timesheet.",
                sender="manager@example.com",
                smtp_host="smtp.office365.com",
                username="manager@example.com",
                password="secret",
            )

    @patch.dict(
        "os.environ",
        {
            "SMTP_HOST": "smtp.example.com",
            "SMTP_PORT": "2525",
            "SMTP_FROM": "manager@example.com",
            "SMTP_USERNAME": "manager",
            "SMTP_PASSWORD": "secret",
        },
        clear=True,
    )
    def test_reads_smtp_settings_from_environment(self) -> None:
        settings = smtp_settings_from_environment()
        self.assertEqual(settings["host"], "smtp.example.com")
        self.assertEqual(settings["port"], 2525)
        self.assertEqual(settings["sender"], "manager@example.com")
        self.assertEqual(settings["username"], "manager")
        self.assertEqual(settings["password"], "secret")


if __name__ == "__main__":
    unittest.main()
