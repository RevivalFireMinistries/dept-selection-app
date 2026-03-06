"""
Background scheduler for automated tasks like meeting reminders.
Uses APScheduler to run jobs at specified times.
"""

import os
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session, joinedload

from database import SessionLocal
from models import Meeting, MemberDepartment, Member, Department, ServiceProgram


# Global scheduler instance
scheduler: Optional[BackgroundScheduler] = None

# Track last run info for status reporting
last_run_info: Dict[str, Any] = {
    "meeting_reminders": {
        "last_run": None,
        "last_status": None,
        "meetings_processed": 0,
        "reminders_sent": 0,
        "errors": []
    }
}


def get_scheduler() -> BackgroundScheduler:
    """Get or create the scheduler instance"""
    global scheduler
    if scheduler is None:
        scheduler = BackgroundScheduler()
    return scheduler


def start_scheduler():
    """Start the background scheduler with all jobs"""
    global scheduler
    scheduler = get_scheduler()

    if scheduler.running:
        print("Scheduler already running")
        return

    # Get reminder time from environment or default to 8:00 AM
    reminder_hour = int(os.getenv("REMINDER_HOUR", "8"))
    reminder_minute = int(os.getenv("REMINDER_MINUTE", "0"))

    # Add meeting reminder job - runs daily at configured time
    scheduler.add_job(
        send_meeting_reminders,
        CronTrigger(hour=reminder_hour, minute=reminder_minute),
        id="meeting_reminders",
        name="Send Meeting Reminders",
        replace_existing=True
    )

    # Add past program cleanup job - runs daily at 1:00 AM
    scheduler.add_job(
        cleanup_past_programs,
        CronTrigger(hour=1, minute=0),
        id="cleanup_past_programs",
        name="Cleanup Past Service Programs",
        replace_existing=True
    )

    scheduler.start()
    print(f"Scheduler started. Meeting reminders scheduled for {reminder_hour:02d}:{reminder_minute:02d} daily")


def shutdown_scheduler():
    """Shutdown the scheduler gracefully"""
    global scheduler
    if scheduler and scheduler.running:
        scheduler.shutdown(wait=False)
        print("Scheduler shut down")


def send_meeting_reminders(meeting_ids: Optional[List[int]] = None) -> Dict[str, Any]:
    """
    Send reminder emails for today's meetings.

    Can be called by scheduler or manually triggered.

    Args:
        meeting_ids: Optional list of specific meeting IDs to send reminders for.
                    If None, auto-detects based on today's date.

    Returns a summary of the operation.
    """
    global last_run_info

    result = {
        "success": True,
        "timestamp": datetime.now().isoformat(),
        "mode": "selected" if meeting_ids else "auto",
        "meeting_ids_requested": meeting_ids,
        "meetings_found": 0,
        "reminders_sent": 0,
        "errors": []
    }

    db: Session = SessionLocal()

    try:
        now = datetime.now()
        today = now.date()
        today_str = today.isoformat()

        if meeting_ids:
            # Manual selection: only send for specified meeting IDs
            print(f"[Reminder Job] Sending reminders for selected meetings: {meeting_ids}")
            meetings = db.query(Meeting).options(
                joinedload(Meeting.department)
            ).filter(
                Meeting.id.in_(meeting_ids)
            ).order_by(Meeting.meeting_date, Meeting.start_slot).all()
        else:
            # Auto mode: find today's meetings
            print(f"[Reminder Job] Checking for meetings on {today_str}")
            meetings = db.query(Meeting).options(
                joinedload(Meeting.department)
            ).filter(
                Meeting.meeting_date == today_str
            ).order_by(Meeting.start_slot).all()

        result["meetings_found"] = len(meetings)

        if not meetings:
            print("[Reminder Job] No meetings found for today")
            last_run_info["meeting_reminders"].update({
                "last_run": result["timestamp"],
                "last_status": "success",
                "meetings_processed": 0,
                "reminders_sent": 0,
                "errors": []
            })
            return result

        # Import notification dispatcher
        from notifications.dispatcher import dispatch_event
        from notifications.events import EventType

        for meeting in meetings:
            try:
                # Get recipients based on meeting type
                recipients = get_meeting_recipients(db, meeting)

                if not recipients:
                    print(f"[Reminder Job] No recipients for meeting: {meeting.title}")
                    continue

                # Prepare meeting data
                meeting_data = {
                    "title": meeting.title,
                    "meeting_date": meeting.meeting_date,
                    "start_time": slot_to_time(meeting.start_slot),
                    "end_time": slot_to_time(meeting.end_slot),
                    "location": meeting.location,
                    "department_name": meeting.department.name if meeting.department else "All Leaders",
                    "meeting_link": meeting.meeting_link,
                    "description": meeting.description
                }

                # Dispatch reminder to all recipients
                dispatch_event(
                    db=db,
                    event_type=EventType.MEETING_REMINDER,
                    data=meeting_data,
                    recipients=recipients
                )

                result["reminders_sent"] += len(recipients)
                print(f"[Reminder Job] Sent {len(recipients)} reminders for: {meeting.title}")

            except Exception as e:
                error_msg = f"Failed to send reminders for meeting {meeting.id}: {str(e)}"
                result["errors"].append(error_msg)
                print(f"[Reminder Job] {error_msg}")

        if result["errors"]:
            result["success"] = False

        # Update last run info
        last_run_info["meeting_reminders"].update({
            "last_run": result["timestamp"],
            "last_status": "success" if result["success"] else "partial",
            "meetings_processed": result["meetings_found"],
            "reminders_sent": result["reminders_sent"],
            "errors": result["errors"]
        })

        return result

    except Exception as e:
        error_msg = f"Reminder job failed: {str(e)}"
        result["success"] = False
        result["errors"].append(error_msg)
        print(f"[Reminder Job] {error_msg}")

        last_run_info["meeting_reminders"].update({
            "last_run": result["timestamp"],
            "last_status": "failed",
            "meetings_processed": 0,
            "reminders_sent": 0,
            "errors": [error_msg]
        })

        return result

    finally:
        db.close()


def cleanup_past_programs():
    """Delete service programs with dates before today"""
    db: Session = SessionLocal()
    try:
        today = datetime.now().date()
        deleted = db.query(ServiceProgram).filter(ServiceProgram.service_date < today).delete()
        if deleted:
            db.commit()
            print(f"[Cleanup] Deleted {deleted} past service program(s)")
    except Exception as e:
        print(f"[Cleanup] Failed to clean past programs: {e}")
    finally:
        db.close()


def get_meeting_recipients(db: Session, meeting: Meeting) -> List[Dict[str, Any]]:
    """
    Get list of recipients for a meeting based on its type.
    Always includes HOD(s) of the relevant department(s).

    - Single department meeting: members with approved status in that department + HOD
    - Multi-department meeting: members with approved status in any target department + HODs
    - General meeting (all leaders): all members with at least one approved department + all HODs
    """
    member_ids = set()
    department_ids = []

    if meeting.is_general:
        # All leaders meeting - get all members with at least one approved department
        approved_members = db.query(MemberDepartment.member_id).filter(
            MemberDepartment.status == "approved"
        ).distinct().all()
        member_ids = set(m[0] for m in approved_members)
        # Get all department IDs for HOD lookup
        all_depts = db.query(Department.id).all()
        department_ids = [d[0] for d in all_depts]

    elif meeting.target_department_ids:
        # Multi-department meeting
        import json
        try:
            target_ids = json.loads(meeting.target_department_ids) if isinstance(meeting.target_department_ids, str) else meeting.target_department_ids
        except:
            target_ids = []

        if target_ids:
            approved_members = db.query(MemberDepartment.member_id).filter(
                MemberDepartment.department_id.in_(target_ids),
                MemberDepartment.status == "approved"
            ).distinct().all()
            member_ids = set(m[0] for m in approved_members)
            department_ids = target_ids

    elif meeting.department_id:
        # Single department meeting
        approved_members = db.query(MemberDepartment.member_id).filter(
            MemberDepartment.department_id == meeting.department_id,
            MemberDepartment.status == "approved"
        ).distinct().all()
        member_ids = set(m[0] for m in approved_members)
        department_ids = [meeting.department_id]

    else:
        return []

    # Add HODs of relevant departments
    if department_ids:
        hods = db.query(Department.hod_member_id).filter(
            Department.id.in_(department_ids),
            Department.hod_member_id.isnot(None)
        ).all()
        for hod in hods:
            member_ids.add(hod[0])

    # Get member details
    recipients = []
    if member_ids:
        members = db.query(Member).filter(Member.id.in_(member_ids)).all()
        for member in members:
            if member.email:  # Only include members with email
                recipients.append({
                    "id": member.id,
                    "email": member.email,
                    "phone": member.phone,
                    "name": member.full_name
                })

    return recipients


def slot_to_time(slot: int) -> str:
    """Convert a time slot number to a formatted time string"""
    if slot is None:
        return ""
    # Slots are 30-minute increments starting at midnight
    # Slot 0 = 00:00, Slot 1 = 00:30, etc.
    hours = slot // 2
    minutes = (slot % 2) * 30

    # Format as 12-hour time
    period = "AM" if hours < 12 else "PM"
    display_hours = hours % 12
    if display_hours == 0:
        display_hours = 12

    return f"{display_hours}:{minutes:02d} {period}"


def get_scheduler_status() -> Dict[str, Any]:
    """Get the current status of the scheduler and its jobs"""
    global scheduler, last_run_info

    status = {
        "running": scheduler.running if scheduler else False,
        "jobs": [],
        "last_runs": last_run_info
    }

    if scheduler and scheduler.running:
        for job in scheduler.get_jobs():
            next_run = job.next_run_time
            status["jobs"].append({
                "id": job.id,
                "name": job.name,
                "next_run": next_run.isoformat() if next_run else None
            })

    return status
