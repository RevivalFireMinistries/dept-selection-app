"""
Base notification channel interface.
All notification channels must implement this interface.
"""

from abc import ABC, abstractmethod
from typing import Tuple, Optional


class NotificationChannel(ABC):
    """Abstract base class for notification channels"""

    @abstractmethod
    def is_configured(self) -> bool:
        """Check if the channel is properly configured"""
        pass

    @abstractmethod
    def send(
        self,
        recipient: str,
        subject: str,
        body: str,
        **kwargs
    ) -> Tuple[bool, Optional[str]]:
        """
        Send a notification.

        Args:
            recipient: The recipient identifier (email, phone, etc.)
            subject: The notification subject/title
            body: The notification body/content
            **kwargs: Additional channel-specific options

        Returns:
            Tuple of (success: bool, error_message: Optional[str])
        """
        pass

    @abstractmethod
    def test_connection(self) -> Tuple[bool, Optional[str]]:
        """
        Test the channel connection/configuration.

        Returns:
            Tuple of (success: bool, error_message: Optional[str])
        """
        pass
