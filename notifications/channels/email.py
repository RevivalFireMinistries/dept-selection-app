"""
SMTP Email channel for sending notifications.
"""

import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Tuple, Optional, Dict
import ssl

from .base import NotificationChannel


class EmailChannel(NotificationChannel):
    """SMTP email notification channel"""

    def __init__(self, settings: Dict[str, str]):
        """
        Initialize the email channel with SMTP settings.
        Environment variables take precedence over database settings.

        Args:
            settings: Dictionary containing:
                - smtp_host: SMTP server hostname
                - smtp_port: SMTP server port
                - smtp_username: SMTP authentication username
                - smtp_password: SMTP authentication password
                - smtp_from_name: Display name for From field
                - smtp_from_email: Email address for From field
        """
        # Environment variables take precedence (for Railway deployment)
        self.host = os.getenv('SMTP_HOST') or settings.get('smtp_host', 'smtp.gmail.com')
        self.port = int(os.getenv('SMTP_PORT') or settings.get('smtp_port', '587'))
        self.username = os.getenv('SMTP_USERNAME') or settings.get('smtp_username', '')
        self.password = os.getenv('SMTP_PASSWORD') or settings.get('smtp_password', '')
        self.from_name = os.getenv('SMTP_FROM_NAME') or settings.get('smtp_from_name', 'RFM Stellenbosch')
        self.from_email = os.getenv('SMTP_FROM_EMAIL') or settings.get('smtp_from_email', '')
        self.reply_to = os.getenv('SMTP_REPLY_TO') or settings.get('smtp_reply_to', '') or settings.get('reply_to', '')

        # Check enabled status from env or settings
        env_enabled = os.getenv('SMTP_ENABLED', '').lower()
        if env_enabled:
            self.enabled = env_enabled == 'true'
        else:
            self.enabled = settings.get('smtp_enabled', 'false').lower() == 'true'

    def is_configured(self) -> bool:
        """Check if SMTP is properly configured"""
        return bool(
            self.enabled and
            self.host and
            self.port and
            self.username and
            self.password and
            self.from_email
        )

    def send(
        self,
        recipient: str,
        subject: str,
        body: str,
        **kwargs
    ) -> Tuple[bool, Optional[str]]:
        """
        Send an email via SMTP.

        Args:
            recipient: Email address to send to
            subject: Email subject
            body: HTML email body
            **kwargs: Additional options (ignored for email)

        Returns:
            Tuple of (success, error_message)
        """
        if not self.is_configured():
            return False, "SMTP is not configured"

        if not recipient:
            return False, "No recipient email address"

        try:
            # Create message
            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From'] = f"{self.from_name} <{self.from_email}>"
            msg['To'] = recipient
            if self.reply_to:
                msg['Reply-To'] = self.reply_to

            # Headers Gmail uses to decide spam-vs-inbox (Feb 2024 sender
            # requirements); positive signal even at low volume.
            unsub_email = (
                os.getenv('UNSUBSCRIBE_EMAIL')
                or self.reply_to
                or self.from_email
            )
            if unsub_email:
                msg['List-Unsubscribe'] = f"<mailto:{unsub_email}?subject=unsubscribe>"
                msg['List-Unsubscribe-Post'] = 'List-Unsubscribe=One-Click'
            msg['X-Mailer'] = 'RFM-Stellenbosch-Portal'

            # Create plain text version (strip HTML for basic compatibility)
            plain_text = body.replace('<br>', '\n').replace('</p>', '\n\n')
            # Remove other HTML tags for plain text
            import re
            plain_text = re.sub('<[^<]+?>', '', plain_text)

            # Attach both versions
            msg.attach(MIMEText(plain_text, 'plain'))
            msg.attach(MIMEText(body, 'html'))

            # Connect and send
            if self.port == 465:
                # SSL connection
                context = ssl.create_default_context()
                with smtplib.SMTP_SSL(self.host, self.port, context=context) as server:
                    server.login(self.username, self.password)
                    server.sendmail(self.from_email, recipient, msg.as_string())
            else:
                # TLS connection (port 587)
                with smtplib.SMTP(self.host, self.port) as server:
                    server.starttls()
                    server.login(self.username, self.password)
                    server.sendmail(self.from_email, recipient, msg.as_string())

            return True, None

        except smtplib.SMTPAuthenticationError as e:
            return False, f"SMTP authentication failed: {str(e)}"
        except smtplib.SMTPRecipientsRefused as e:
            return False, f"Recipient refused: {str(e)}"
        except smtplib.SMTPException as e:
            return False, f"SMTP error: {str(e)}"
        except Exception as e:
            return False, f"Failed to send email: {str(e)}"

    def test_connection(self) -> Tuple[bool, Optional[str]]:
        """
        Test SMTP connection without sending an email.

        Returns:
            Tuple of (success, error_message)
        """
        if not self.is_configured():
            return False, "SMTP is not configured. Please fill in all settings."

        try:
            if self.port == 465:
                context = ssl.create_default_context()
                with smtplib.SMTP_SSL(self.host, self.port, context=context) as server:
                    server.login(self.username, self.password)
            else:
                with smtplib.SMTP(self.host, self.port, timeout=10) as server:
                    server.starttls()
                    server.login(self.username, self.password)

            return True, None

        except smtplib.SMTPAuthenticationError:
            return False, "Authentication failed. Check username and password."
        except smtplib.SMTPConnectError:
            return False, f"Could not connect to {self.host}:{self.port}"
        except TimeoutError:
            return False, f"Connection to {self.host}:{self.port} timed out"
        except Exception as e:
            return False, f"Connection test failed: {str(e)}"

    def send_test_email(self, to_email: str) -> Tuple[bool, Optional[str]]:
        """
        Send a test email to verify the configuration.

        Args:
            to_email: Email address to send test to

        Returns:
            Tuple of (success, error_message)
        """
        subject = "Test Email from RFM Stellenbosch Portal"
        body = """
        <html>
        <body style="font-family: Arial, sans-serif; padding: 20px;">
            <h2 style="color: #4F46E5;">Test Email</h2>
            <p>This is a test email from the RFM Stellenbosch Portal.</p>
            <p>If you're seeing this, your email configuration is working correctly!</p>
            <hr style="border: none; border-top: 1px solid #E5E7EB; margin: 20px 0;">
            <p style="color: #6B7280; font-size: 12px;">
                This is an automated message. Please do not reply.
            </p>
        </body>
        </html>
        """
        return self.send(to_email, subject, body)
