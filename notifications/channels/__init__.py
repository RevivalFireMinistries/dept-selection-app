"""Notification channel implementations.

Email goes through rfm-notify since v1.2 — see RfmNotifyChannel below.
The Resend / SMTP direct-send channels were retired at the same time
(provider config now lives in the rfm-notify admin console).
"""
from .base import NotificationChannel
from .rfm_notify import RfmNotifyChannel

__all__ = ['NotificationChannel', 'RfmNotifyChannel']
