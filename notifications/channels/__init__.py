"""
Notification channels package.
Channels are responsible for actually sending notifications via different mediums.
"""

from .email import EmailChannel

__all__ = ['EmailChannel']
