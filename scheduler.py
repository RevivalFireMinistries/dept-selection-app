"""
Background scheduler for automated tasks like meeting reminders.
Uses APScheduler to run jobs at specified times.
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session, joinedload

from database import SessionLocal
from models import Meeting, MemberDepartment, Member, Department, ServiceProgram, ServiceSchedule, ProgramTemplate, HomeChurch, HomeChurchRoster, HomeChurchProgramType, HomeChurchAttendance, Survey


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

    # Display submission cleanup — runs daily at 1:30 AM. Used to live
    # inline with /api/display/fetch (delete-on-fetch), but that broke
    # FirePresenter: by the time FP requested the file, the server had
    # already removed it. Now we hold the file for at least 14 days
    # after the submission has been fetched, then sweep.
    scheduler.add_job(
        cleanup_fetched_display_submissions,
        CronTrigger(hour=1, minute=30),
        id="cleanup_fetched_display_submissions",
        name="Cleanup Fetched Display Submissions",
        replace_existing=True
    )

    # Add service schedule notifications - runs daily at 7:00 AM
    scheduler.add_job(
        check_service_schedules,
        CronTrigger(hour=7, minute=0),
        id="service_schedule_notifications",
        name="Service Schedule Notifications",
        replace_existing=True
    )

    # Home church day-before reminders - runs daily at 18:00 (6pm)
    # The job figures out which day is "tomorrow" and only acts if there are
    # published home church entries for tomorrow.
    scheduler.add_job(
        send_home_church_reminders,
        CronTrigger(hour=18, minute=0),
        id="home_church_reminders",
        name="Home Church Day-Before Reminders",
        replace_existing=True
    )

    # Home church attendance reminders - Tuesday 12:00 (noon SAST)
    # For each home church that met on the previous Monday but whose
    # committee hasn't captured attendance yet, email the leader so
    # they can send their stats via WhatsApp.
    scheduler.add_job(
        send_home_church_attendance_reminders,
        CronTrigger(day_of_week="tue", hour=12, minute=0),
        id="home_church_attendance_reminders",
        name="Home Church Attendance Reminders",
        replace_existing=True
    )

    # Survey auto-expiry — runs daily at 02:00. Surveys auto-delete (with all
    # their questions and responses) once they're older than SURVEY_RETENTION_DAYS.
    scheduler.add_job(
        purge_expired_surveys,
        CronTrigger(hour=2, minute=0),
        id="purge_expired_surveys",
        name="Purge Expired Surveys",
        replace_existing=True,
    )

    # Prayer-request nudges — runs daily at 09:00. Reminds prayer coordinators
    # of any request still unacknowledged (status "new") after 3 days.
    scheduler.add_job(
        send_prayer_request_reminders,
        CronTrigger(hour=9, minute=0),
        id="prayer_request_reminders",
        name="Prayer Request Nudges",
        replace_existing=True,
    )

    scheduler.start()
    print(f"Scheduler started. Meeting reminders scheduled for {reminder_hour:02d}:{reminder_minute:02d} daily")


def purge_expired_surveys() -> Dict[str, Any]:
    """Delete surveys older than SURVEY_RETENTION_DAYS (default 90).
    Cascades to questions and responses via the FK relationships."""
    retention_days = int(os.getenv("SURVEY_RETENTION_DAYS", "90"))
    cutoff = datetime.utcnow() - timedelta(days=retention_days)
    db: Session = SessionLocal()
    deleted = 0
    try:
        expired = db.query(Survey).filter(Survey.created_at < cutoff).all()
        for s in expired:
            db.delete(s)
            deleted += 1
        if deleted:
            db.commit()
        print(f"[Survey purge] retention={retention_days}d cutoff={cutoff.isoformat()} deleted={deleted}")
    except Exception as e:
        db.rollback()
        print(f"[Survey purge] error: {e}")
        return {"success": False, "error": str(e)}
    finally:
        db.close()
    return {"success": True, "deleted": deleted, "retention_days": retention_days}


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


def cleanup_fetched_display_submissions():
    """Delete display submissions (and their uploaded files) older than 14 days.

    Previously this filtered by ``fetched == True``, but now that the
    /api/display/fetch endpoint supports multiple FirePresenter instances
    (each one tracks "already seen" client-side via localStorage) the
    server no longer writes ``fetched = True`` — there's no single point
    at which a submission is considered "consumed by everyone". So we
    just sweep on age: 14 days is well past any reasonable retention
    window for a one-off service announcement, while giving every
    display client plenty of time to grab the asset.

    The job name is kept as ``cleanup_fetched_display_submissions`` for
    operational continuity (existing dashboards, scheduler entries).
    """
    import os
    from models import DisplaySubmission
    from datetime import timedelta as td

    db: Session = SessionLocal()
    try:
        cutoff = datetime.now() - td(days=14)
        old = db.query(DisplaySubmission).filter(
            DisplaySubmission.created_at < cutoff,
        ).all()
        for s in old:
            if s.file_path and os.path.exists(s.file_path):
                try:
                    os.remove(s.file_path)
                except Exception as fe:
                    print(f"[Cleanup] Couldn't remove {s.file_path}: {fe}")
            db.delete(s)
        if old:
            db.commit()
            print(f"[Cleanup] Removed {len(old)} display submission(s) older than 14 days")
    except Exception as e:
        print(f"[Cleanup] Failed to clean old display submissions: {e}")
    finally:
        db.close()


def check_service_schedules():
    """
    Check upcoming service schedules and send notifications:
    1. Week-start notification: 5-7 days before, notify manager they're assigned
    2. Reminder: 2 days before, if no program created yet
    """
    db: Session = SessionLocal()
    try:
        today = datetime.now().date()
        from datetime import timedelta as td

        # --- 1. Week-start notifications (5-7 days before service) ---
        notify_start = today + td(days=5)
        notify_end = today + td(days=7)
        schedules_to_notify = db.query(ServiceSchedule).options(
            joinedload(ServiceSchedule.template),
            joinedload(ServiceSchedule.service_manager)
        ).filter(
            ServiceSchedule.service_date >= notify_start,
            ServiceSchedule.service_date <= notify_end,
            ServiceSchedule.notified_at.is_(None),
            ServiceSchedule.service_manager_id.isnot(None)
        ).all()

        for schedule in schedules_to_notify:
            manager = schedule.service_manager
            if not manager or not manager.email:
                continue
            try:
                _send_schedule_notification(db, schedule, manager, "assigned")
                schedule.notified_at = datetime.now()
                db.commit()
                print(f"[Schedule] Notified {manager.full_name} for {schedule.service_date}")
            except Exception as e:
                print(f"[Schedule] Failed to notify {manager.full_name}: {e}")

        # --- 2. Reminder notifications (2 days before, no program yet) ---
        reminder_date = today + td(days=2)
        schedules_to_remind = db.query(ServiceSchedule).options(
            joinedload(ServiceSchedule.template),
            joinedload(ServiceSchedule.service_manager)
        ).filter(
            ServiceSchedule.service_date == reminder_date,
            ServiceSchedule.reminded_at.is_(None),
            ServiceSchedule.program_id.is_(None),
            ServiceSchedule.service_manager_id.isnot(None)
        ).all()

        for schedule in schedules_to_remind:
            manager = schedule.service_manager
            if not manager or not manager.email:
                continue
            try:
                _send_schedule_notification(db, schedule, manager, "reminder")
                schedule.reminded_at = datetime.now()
                db.commit()
                print(f"[Schedule] Reminded {manager.full_name} for {schedule.service_date}")
            except Exception as e:
                print(f"[Schedule] Failed to remind {manager.full_name}: {e}")

    except Exception as e:
        print(f"[Schedule] Check failed: {e}")
    finally:
        db.close()


def _send_schedule_notification(db: Session, schedule: "ServiceSchedule", manager: "Member", notif_type: str):
    """Send a schedule assignment or reminder email to the service manager."""
    from notifications.dispatcher import get_email_settings

    svc_date = schedule.service_date
    day_name = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][svc_date.weekday()]
    date_str = svc_date.strftime('%A, %d %B %Y')
    template_name = schedule.template.title if schedule.template else "No template"

    # Get titled name
    from routers.api import _get_titled_name
    manager_name = _get_titled_name(manager)

    FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
    TEXT = "#111827"
    MUTED = "#9ca3af"
    BORDER = "#f3f4f6"

    if notif_type == "assigned":
        subject = f"You're the Service Manager for {day_name}'s Service"
        heading_text = "You've Been Assigned"
        message = f"You are the service manager for the upcoming <strong>{day_name}</strong> service. Please prepare the program using the assigned template."
        accent = "#4f46e5"
    else:
        subject = f"Reminder: No program created for {day_name}'s service"
        heading_text = "Program Reminder"
        message = f"The <strong>{day_name}</strong> service is in <strong>2 days</strong> and no program has been created yet. Please create and publish the program as soon as possible."
        accent = "#f59e0b"

    app_url = os.getenv('APP_URL', '')
    programs_link = f"{app_url}/portal?phone={manager.phone}" if app_url and manager.phone else ""
    button_html = ""
    if programs_link:
        button_html = f'''<a href="{programs_link}" style="display:inline-block;background:{accent};color:#ffffff;font-family:{FONT};font-size:14px;font-weight:600;text-decoration:none;padding:10px 24px;border-radius:10px;margin-top:16px;">Open Portal</a>'''

    html = f'''<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#ffffff;font-family:{FONT};">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr><td align="center" style="padding:32px 16px;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:520px;">
  <tr><td>
    <h2 style="margin:0 0 4px 0;color:{TEXT};font-size:18px;font-weight:700;">{heading_text}</h2>
    <p style="margin:0 0 16px 0;color:{MUTED};font-size:14px;">{date_str}</p>

    <p style="margin:0 0 16px 0;color:{TEXT};font-size:15px;line-height:1.6;">Hi <strong>{manager_name}</strong>,</p>
    <p style="margin:0 0 14px 0;color:#6b7280;font-size:14px;line-height:1.6;">{message}</p>

    <p style="margin:16px 0 6px 0;font-size:11px;font-weight:600;color:{MUTED};text-transform:uppercase;letter-spacing:0.5px;">Details</p>
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0">
        <tr><td style="padding:6px 0;width:110px;"><span style="color:{MUTED};font-size:12px;text-transform:uppercase;">Date</span></td><td style="padding:6px 0;"><span style="color:{TEXT};font-size:14px;font-weight:500;">{date_str}</span></td></tr>
        <tr><td style="padding:6px 0;"><span style="color:{MUTED};font-size:12px;text-transform:uppercase;">Template</span></td><td style="padding:6px 0;"><span style="color:{TEXT};font-size:14px;font-weight:500;">{template_name}</span></td></tr>
        {f'<tr><td style="padding:6px 0;"><span style="color:{MUTED};font-size:12px;text-transform:uppercase;">Notes</span></td><td style="padding:6px 0;"><span style="color:{TEXT};font-size:14px;">{schedule.notes}</span></td></tr>' if schedule.notes else ''}
    </table>

    {button_html}

    <hr style="border:none;border-top:1px solid {BORDER};margin:24px 0 16px 0;">
    <p style="margin:0;color:{MUTED};font-size:12px;">Revival Fire Ministries</p>
  </td></tr>
</table>
</td></tr></table>
</body></html>'''

    from notifications.channels.rfm_notify import RfmNotifyChannel
    channel = RfmNotifyChannel()
    if channel.is_configured():
        success, error = channel.send(
            manager.email, subject, html,
            event_code="program.manager_reminder",
            recipient_name=getattr(manager, "full_name", None),
            idempotency_key=f"program_manager_reminder:{getattr(meeting, 'id', '')}:{manager.email}",
        )
        if not success:
            raise Exception(error)


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


def send_home_church_reminders():
    """Send day-before reminders to home church leaders and assigned preachers.

    Runs daily in the evening; only sends for *tomorrow's* published roster entries.
    Each leader gets a reminder about tomorrow's program. Each assigned preacher
    gets a reminder about where they're preaching."""
    db: Session = SessionLocal()
    try:
        from datetime import timedelta as td
        tomorrow = (datetime.now() + td(days=1)).date()

        entries = db.query(HomeChurchRoster).options(
            joinedload(HomeChurchRoster.home_church).joinedload(HomeChurch.leader),
            joinedload(HomeChurchRoster.program_type),
            joinedload(HomeChurchRoster.preacher),
        ).filter(
            HomeChurchRoster.roster_date == tomorrow,
            HomeChurchRoster.status == "published",
        ).all()

        if not entries:
            print(f"[HomeChurchReminder] No published entries for {tomorrow.isoformat()}")
            return

        from notifications.dispatcher import dispatch_event
        from notifications.events import EventType
        from routers.api import _member_email_with_central_fallback

        leader_count = 0
        preacher_count = 0

        for e in entries:
            hc = e.home_church
            if not hc:
                continue

            # Leader reminder
            leader_email = _member_email_with_central_fallback(hc.leader, db) if hc.leader else ""
            if hc.leader and leader_email:
                try:
                    dispatch_event(db, EventType.HOME_CHURCH_REMINDER_LEADER, {
                        "leader_name": hc.leader.full_name,
                        "home_church_name": hc.name,
                        "roster_date": tomorrow.isoformat(),
                        "meeting_time": hc.meeting_time,
                        "program_type_name": e.program_type.name if e.program_type else "Not set",
                        "program_type_icon": e.program_type.icon if e.program_type else "📌",
                        "requires_preacher": e.program_type.requires_preacher if e.program_type else False,
                        "preacher_name": e.preacher.full_name if e.preacher else None,
                        "preacher_phone": e.preacher.phone if e.preacher else None,
                        "recipients": [{"id": hc.leader.id, "name": hc.leader.full_name, "email": leader_email, "phone": hc.leader.phone}],
                    })
                    leader_count += 1
                except Exception as exc:
                    print(f"[HomeChurchReminder] Failed leader reminder for {hc.name}: {exc}")
            elif hc.leader:
                print(f"[HomeChurchReminder] leader {hc.leader.full_name} ({hc.name}) skipped — no email locally or in rfm-database")

            # Preacher reminder (only if assigned)
            preacher_email = _member_email_with_central_fallback(e.preacher, db) if e.preacher else ""
            if e.preacher and preacher_email:
                try:
                    dispatch_event(db, EventType.HOME_CHURCH_REMINDER_PREACHER, {
                        "preacher_name": e.preacher.full_name,
                        "home_church_name": hc.name,
                        "home_church_address": hc.address or "",
                        "leader_name": hc.leader.full_name if hc.leader else "",
                        "leader_phone": hc.leader.phone if hc.leader else "",
                        "roster_date": tomorrow.isoformat(),
                        "meeting_time": hc.meeting_time,
                        "recipients": [{"id": e.preacher.id, "name": e.preacher.full_name, "email": preacher_email, "phone": e.preacher.phone}],
                    })
                    preacher_count += 1
                except Exception as exc:
                    print(f"[HomeChurchReminder] Failed preacher reminder for {hc.name}: {exc}")
            elif e.preacher:
                print(f"[HomeChurchReminder] preacher {e.preacher.full_name} ({hc.name}) skipped — no email locally or in rfm-database")

        print(f"[HomeChurchReminder] Sent {leader_count} leader + {preacher_count} preacher reminders for {tomorrow.isoformat()}")
    except Exception as exc:
        print(f"[HomeChurchReminder] Job failed: {exc}")
    finally:
        db.close()


def send_home_church_attendance_reminders():
    """Tuesday-noon digest: one email per Home Church committee member listing
    every home church whose Monday attendance still hasn't been captured on
    the portal. Leaders send their numbers via WhatsApp — it's the committee
    that captures. If we send to each leader we're nagging the wrong people."""
    db: Session = SessionLocal()
    try:
        from datetime import timedelta as td
        today = datetime.now().date()
        # The "previous Monday" = most recent Monday that has already passed
        last_monday = today - td(days=today.weekday() or 7)

        churches = db.query(HomeChurch).filter(HomeChurch.is_active == True).all()
        if not churches:
            print("[AttendanceReminder] No active home churches")
            return

        # Existing reports for that date
        existing = {
            r.home_church_id: r
            for r in db.query(HomeChurchAttendance).filter(
                HomeChurchAttendance.roster_date == last_monday
            ).all()
        }

        # Only count churches with a published roster entry — no nagging for
        # weeks the committee never said "yes we're meeting".
        published_ids = {
            e.home_church_id for e in db.query(HomeChurchRoster).filter(
                HomeChurchRoster.roster_date == last_monday,
                HomeChurchRoster.status == "published",
            ).all()
        }

        # Build the pending list. "Captured" = report row exists AND either
        # did_not_meet is true OR a real attendance number was entered.
        # Placeholder rows created by a previous reminder (attendance_count=0,
        # reminder_sent_at set) are treated as still pending.
        pending = []
        for c in churches:
            if c.id not in published_ids:
                continue
            report = existing.get(c.id)
            captured = bool(report and (report.did_not_meet or (report.attendance_count or 0) > 0))
            if captured:
                continue
            pending.append(c)

        if not pending:
            print(f"[AttendanceReminder] All reports captured for {last_monday.isoformat()} — nothing to do")
            return

        # Find committee members (case-insensitive match, same logic as the API)
        committee_dept_ids = [
            d.id for d in db.query(Department).all()
            if "home church" in (d.name or "").lower() and "committee" in (d.name or "").lower()
        ]
        if not committee_dept_ids:
            print("[AttendanceReminder] No Home Church committee department found")
            return

        committee_member_ids = [
            m[0] for m in db.query(MemberDepartment.member_id).filter(
                MemberDepartment.department_id.in_(committee_dept_ids),
                MemberDepartment.status == "approved",
            ).distinct().all()
        ]
        if not committee_member_ids:
            print("[AttendanceReminder] No active committee members")
            return

        committee = db.query(Member).filter(
            Member.id.in_(committee_member_ids),
            Member.is_active == True,
        ).all()
        recipients = [
            {"id": m.id, "name": m.full_name, "email": m.email, "phone": m.phone}
            for m in committee if m.email
        ]
        if not recipients:
            print("[AttendanceReminder] No committee members have email addresses")
            return

        pending_list = [
            {
                "name": c.name,
                "leader_name": c.leader.full_name if c.leader else None,
                "leader_phone": c.leader.phone if c.leader else None,
            }
            for c in pending
        ]

        from notifications.dispatcher import dispatch_event
        from notifications.events import EventType

        try:
            dispatch_event(db, EventType.HOME_CHURCH_ATTENDANCE_REMINDER, {
                "roster_date": last_monday.isoformat(),
                "pending_count": len(pending),
                "pending_list": pending_list,
                "recipients": recipients,
            })
        except Exception as exc:
            print(f"[AttendanceReminder] Dispatch failed: {exc}")

        # Record a tracking placeholder so we can inspect which weeks got reminded
        # (one placeholder per pending church, bearing the timestamp; doesn't
        # mark the report as captured because attendance_count stays 0).
        for c in pending:
            report = existing.get(c.id)
            if report is None:
                placeholder = HomeChurchAttendance(
                    home_church_id=c.id,
                    roster_date=last_monday,
                    attendance_count=0,
                    offering_amount="0",
                    did_not_meet=False,
                    reminder_sent_at=datetime.utcnow(),
                )
                db.add(placeholder)
            elif report.reminder_sent_at is None:
                report.reminder_sent_at = datetime.utcnow()
        db.commit()

        print(f"[AttendanceReminder] Sent digest to {len(recipients)} committee member(s) — {len(pending)} pending church(es) for {last_monday.isoformat()}")
    except Exception as exc:
        print(f"[AttendanceReminder] Job failed: {exc}")
    finally:
        db.close()


def send_prayer_request_reminders() -> Dict[str, Any]:
    """Nudge prayer coordinators about requests still unacknowledged (status
    "new") after 3 days. One nudge per request (tracked by reminder_sent_at),
    grouped into a per-assembly digest to the coordinators."""
    from models import PrayerRequest
    from notifications.dispatcher import dispatch_event
    from notifications.events import EventType
    from routers.prayer_requests import _recipient_ids, _default_assembly

    days = int(os.getenv("PRAYER_REMINDER_DAYS", "3"))
    db = SessionLocal()
    sent_batches = 0
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        stale = (
            db.query(PrayerRequest)
            .filter(
                PrayerRequest.status == "new",
                PrayerRequest.created_at < cutoff,
                PrayerRequest.reminder_sent_at.is_(None),
            )
            .order_by(PrayerRequest.created_at.asc())
            .all()
        )
        if not stale:
            return {"reminded": 0, "batches": 0}

        # Group by assembly so each branch's coordinators get their own digest.
        by_asm: Dict[Any, List] = {}
        for r in stale:
            by_asm.setdefault(r.assembly_id, []).append(r)

        default_asm = _default_assembly(db)
        now = datetime.now(timezone.utc)

        for asm, reqs in by_asm.items():
            ids = _recipient_ids(db, asm)
            if not ids and default_asm and str(default_asm) != str(asm or ""):
                ids = _recipient_ids(db, default_asm)
            if not ids:
                continue
            members = db.query(Member).filter(Member.id.in_(ids)).all()
            recipients = [
                {"id": m.id, "email": (getattr(m, "email", None) or "").strip(),
                 "name": m.full_name, "phone": m.phone}
                for m in members if (getattr(m, "email", None) or "").strip()
            ]
            if not recipients:
                continue

            def _short(t: str) -> str:
                t = (t or "").strip().replace("\n", " ")
                return t if len(t) <= 120 else t[:117] + "…"

            data = {
                "count": len(reqs),
                "days": days,
                "requests": [_short(r.request_text) for r in reqs],
            }
            try:
                dispatch_event(db, EventType.PRAYER_REQUEST_REMINDER, data, recipients)
                for r in reqs:
                    r.reminder_sent_at = now
                sent_batches += 1
            except Exception as exc:
                print(f"[PrayerReminder] dispatch failed for assembly {asm}: {exc}")

        db.commit()
        print(f"[PrayerReminder] nudged coordinators for {len(stale)} request(s) across {sent_batches} assembly digest(s)")
        return {"reminded": len(stale), "batches": sent_batches}
    except Exception as exc:
        print(f"[PrayerReminder] Job failed: {exc}")
        return {"error": str(exc)}
    finally:
        db.close()


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
