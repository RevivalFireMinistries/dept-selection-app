"""
Notification system package for event-based notifications.

This package provides an extensible architecture for sending notifications
via multiple channels (email, SMS, push) based on configurable events.
"""

from .events import EventType
from .dispatcher import dispatch_event

__all__ = ['EventType', 'dispatch_event']
