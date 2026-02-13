"""
Event type definitions for the notification system.
"""

from enum import Enum
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from datetime import datetime


class EventType(str, Enum):
    """All triggerable notification events"""
    MEMBER_APPROVED = "member_approved"
    MEMBER_REJECTED = "member_rejected"
    DEPARTMENT_ASSIGNED = "department_assigned"
    RESULTS_PUBLISHED = "results_published"
    APPEAL_SUBMITTED = "appeal_submitted"
    APPEAL_RESOLVED = "appeal_resolved"
    MEETING_CREATED = "meeting_created"
    MEETING_REMINDER = "meeting_reminder"
    MEETING_UPDATED = "meeting_updated"
    MEETING_CANCELLED = "meeting_cancelled"


# Event display names for admin UI
EVENT_LABELS = {
    EventType.MEMBER_APPROVED: "Member Selection Approved",
    EventType.MEMBER_REJECTED: "Member Selection Rejected",
    EventType.DEPARTMENT_ASSIGNED: "Department Assigned by Admin",
    EventType.RESULTS_PUBLISHED: "Results Published",
    EventType.APPEAL_SUBMITTED: "Appeal Submitted",
    EventType.APPEAL_RESOLVED: "Appeal Resolved",
    EventType.MEETING_CREATED: "Meeting Created",
    EventType.MEETING_REMINDER: "Meeting Reminder",
    EventType.MEETING_UPDATED: "Meeting Updated",
    EventType.MEETING_CANCELLED: "Meeting Cancelled",
}


# Event descriptions for admin UI
EVENT_DESCRIPTIONS = {
    EventType.MEMBER_APPROVED: "Sent to member when their department selection is approved",
    EventType.MEMBER_REJECTED: "Sent to member when their department selection is rejected",
    EventType.DEPARTMENT_ASSIGNED: "Sent to member when admin assigns them to a department",
    EventType.RESULTS_PUBLISHED: "Sent to all members when results are published",
    EventType.APPEAL_SUBMITTED: "Sent to admin when a member submits an appeal",
    EventType.APPEAL_RESOLVED: "Sent to member when their appeal is resolved",
    EventType.MEETING_CREATED: "Sent to department members when a new meeting is scheduled",
    EventType.MEETING_REMINDER: "Sent to attendees on the day of a meeting",
    EventType.MEETING_UPDATED: "Sent to department members when meeting details change",
    EventType.MEETING_CANCELLED: "Sent to department members when a meeting is cancelled",
}


# Email subjects for each event type
EMAIL_SUBJECTS = {
    EventType.MEMBER_APPROVED: "Your Department Selection Has Been Approved",
    EventType.MEMBER_REJECTED: "Update on Your Department Selection",
    EventType.DEPARTMENT_ASSIGNED: "You Have Been Assigned to a Department",
    EventType.RESULTS_PUBLISHED: "Department Selection Results Are Now Available",
    EventType.APPEAL_SUBMITTED: "New Appeal Submitted",
    EventType.APPEAL_RESOLVED: "Your Appeal Has Been Resolved",
    EventType.MEETING_CREATED: "Meeting Invite: {title} - Please RSVP",
    EventType.MEETING_REMINDER: "Reminder: {title} Today",
    EventType.MEETING_UPDATED: "Meeting Updated: {title}",
    EventType.MEETING_CANCELLED: "Meeting Cancelled: {title}",
}


@dataclass
class EventData:
    """Base event data container"""
    event_type: EventType
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()


@dataclass
class MemberApprovedEvent(EventData):
    """Data for member approval events"""
    member_id: int = None
    member_name: str = None
    member_email: str = None
    department_name: str = None
    category_name: Optional[str] = None


@dataclass
class MemberRejectedEvent(EventData):
    """Data for member rejection events"""
    member_id: int = None
    member_name: str = None
    member_email: str = None
    department_name: str = None
    category_name: Optional[str] = None
    admin_note: Optional[str] = None


@dataclass
class DepartmentAssignedEvent(EventData):
    """Data for admin assignment events"""
    member_id: int = None
    member_name: str = None
    member_email: str = None
    department_name: str = None
    category_name: Optional[str] = None
    admin_note: Optional[str] = None


@dataclass
class ResultsPublishedEvent(EventData):
    """Data for results published events"""
    year: str = None
    members: List[Dict[str, Any]] = None  # List of members with approved departments


@dataclass
class AppealSubmittedEvent(EventData):
    """Data for appeal submission events (sent to admin)"""
    appeal_id: int = None
    member_name: str = None
    member_phone: str = None
    unwanted_department: Optional[str] = None
    wanted_department: Optional[str] = None
    reason: Optional[str] = None


@dataclass
class AppealResolvedEvent(EventData):
    """Data for appeal resolution events"""
    member_id: int = None
    member_name: str = None
    member_email: str = None
    status: str = None  # approved or rejected
    unwanted_department: Optional[str] = None
    wanted_department: Optional[str] = None
    admin_response: Optional[str] = None


@dataclass
class MeetingEvent(EventData):
    """Data for meeting events"""
    meeting_id: int = None
    title: str = None
    description: Optional[str] = None
    meeting_date: str = None
    start_time: str = None
    end_time: str = None
    location: Optional[str] = None
    meeting_link: Optional[str] = None
    department_name: Optional[str] = None
    is_general: bool = False
    recipients: List[Dict[str, Any]] = None  # List of members to notify
