"""
Resend email channel for sending notifications.
Uses Resend's HTTP API - works on Railway and other cloud platforms.
"""

import os
import json
import urllib.request
import urllib.error
from typing import Tuple, Optional, Dict

from .base import NotificationChannel


class ResendChannel(NotificationChannel):
    """Resend email notification channel (HTTP API)"""

    API_URL = "https://api.resend.com/emails"

    def __init__(self, settings: Dict[str, str]):
        """
        Initialize the Resend channel.

        Args:
            settings: Dictionary containing:
                - resend_api_key: Resend API key
                - resend_from_name: Display name for From field
                - resend_from_email: Email address for From field (must be verified domain)
        """
        # Environment variables take precedence
        self.api_key = os.getenv('RESEND_API_KEY') or settings.get('resend_api_key', '')
        self.from_name = os.getenv('RESEND_FROM_NAME') or settings.get('resend_from_name', 'RFM Stellenbosch')
        self.from_email = os.getenv('RESEND_FROM_EMAIL') or settings.get('resend_from_email', '')

        # Check enabled status
        env_enabled = os.getenv('RESEND_ENABLED', '').lower()
        if env_enabled:
            self.enabled = env_enabled == 'true'
        else:
            self.enabled = settings.get('resend_enabled', 'false').lower() == 'true'

    def is_configured(self) -> bool:
        """Check if Resend is properly configured"""
        return bool(self.enabled and self.api_key and self.from_email)

    def send(
        self,
        recipient: str,
        subject: str,
        body: str,
        **kwargs
    ) -> Tuple[bool, Optional[str]]:
        """
        Send an email via Resend API.

        Args:
            recipient: Email address to send to
            subject: Email subject
            body: HTML email body

        Returns:
            Tuple of (success, error_message)
        """
        if not self.is_configured():
            return False, "Resend is not configured"

        if not recipient:
            return False, "No recipient email address"

        try:
            # Build request payload
            payload = {
                "from": f"{self.from_name} <{self.from_email}>",
                "to": [recipient],
                "subject": subject,
                "html": body
            }

            # Create request
            data = json.dumps(payload).encode('utf-8')
            req = urllib.request.Request(
                self.API_URL,
                data=data,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "User-Agent": "RFM-DeptSelection/1.0"
                },
                method="POST"
            )

            # Send request
            with urllib.request.urlopen(req, timeout=30) as response:
                if response.status in (200, 201):
                    return True, None
                else:
                    return False, f"Resend API returned status {response.status}"

        except urllib.error.HTTPError as e:
            error_body = e.read().decode('utf-8', errors='ignore')
            try:
                error_data = json.loads(error_body)
                error_msg = error_data.get('message', str(e))
            except:
                error_msg = error_body or str(e)
            return False, f"Resend API error: {error_msg}"
        except urllib.error.URLError as e:
            return False, f"Network error: {str(e.reason)}"
        except Exception as e:
            return False, f"Failed to send email: {str(e)}"

    def test_connection(self) -> Tuple[bool, Optional[str]]:
        """
        Test the Resend API connection by checking API key validity.

        Returns:
            Tuple of (success, error_message)
        """
        if not self.api_key:
            return False, "Resend API key is not configured"
        if not self.from_email:
            return False, "From email is not configured"
        if not self.enabled:
            return False, "Resend is not enabled"

        # Validate API key format
        if not self.api_key.startswith('re_'):
            return False, "Invalid API key format (should start with 're_')"

        # Try to get API keys list to validate the key works
        try:
            req = urllib.request.Request(
                "https://api.resend.com/api-keys",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "User-Agent": "RFM-DeptSelection/1.0"
                },
                method="GET"
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                if response.status == 200:
                    return True, None
                return False, f"API returned status {response.status}"
        except urllib.error.HTTPError as e:
            if e.code == 401:
                return False, "Invalid API key"
            elif e.code == 403:
                # 403 on api-keys might mean restricted key, but it could still send emails
                # Just return success and let the actual send be the real test
                return True, None
            return False, f"API error: {e.code}"
        except urllib.error.URLError as e:
            return False, f"Network error: {str(e.reason)}"
        except Exception as e:
            return False, f"Connection test failed: {str(e)}"

    def send_test_email(self, to_email: str) -> Tuple[bool, Optional[str]]:
        """Send a test email to verify the configuration."""
        subject = "Test Email from RFM Stellenbosch Portal"
        body = """
        <html>
        <body style="font-family: Arial, sans-serif; padding: 20px;">
            <h2 style="color: #4F46E5;">Test Email</h2>
            <p>This is a test email from the RFM Stellenbosch Portal.</p>
            <p>If you're seeing this, your Resend configuration is working correctly!</p>
            <hr style="border: none; border-top: 1px solid #E5E7EB; margin: 20px 0;">
            <p style="color: #6B7280; font-size: 12px;">
                Sent via Resend API
            </p>
        </body>
        </html>
        """
        return self.send(to_email, subject, body)
