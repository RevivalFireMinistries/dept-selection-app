"""
Event dispatcher for the notification system.
Routes events to appropriate channels based on configuration.
"""

import os
from typing import Dict, Any, List, Optional
from datetime import datetime
from sqlalchemy.orm import Session

from .events import EventType, EMAIL_SUBJECTS
from .channels.email import EmailChannel
from .channels.resend import ResendChannel


def get_email_settings(db: Session) -> Dict[str, str]:
    """Get all email settings (SMTP and Resend) from database"""
    from models import Settings
    settings = db.query(Settings).filter(
        Settings.key.in_([
            # SMTP settings
            'smtp_enabled', 'smtp_host', 'smtp_port',
            'smtp_username', 'smtp_password',
            'smtp_from_name', 'smtp_from_email',
            # Resend settings
            'resend_enabled', 'resend_api_key',
            'resend_from_name', 'resend_from_email'
        ])
    ).all()
    return {s.key: s.value for s in settings}


def get_email_channel(settings: Dict[str, str]):
    """
    Get the appropriate email channel based on configuration.
    Resend is preferred if configured, otherwise falls back to SMTP.
    """
    # Try Resend first (more reliable on cloud platforms)
    resend = ResendChannel(settings)
    if resend.is_configured():
        return resend, "resend"

    # Fall back to SMTP
    smtp = EmailChannel(settings)
    if smtp.is_configured():
        return smtp, "smtp"

    return None, None


def get_notification_config(db: Session, event_type: EventType):
    """Get notification config for an event type"""
    from models import NotificationConfig
    return db.query(NotificationConfig).filter(
        NotificationConfig.event_type == event_type.value
    ).first()


def log_notification(
    db: Session,
    event_type: EventType,
    channel: str,
    recipient_id: Optional[int],
    recipient_email: Optional[str],
    recipient_phone: Optional[str],
    subject: str,
    body: str,
    status: str,
    error_message: Optional[str] = None
):
    """Log a notification attempt"""
    from models import NotificationLog
    log = NotificationLog(
        event_type=event_type.value,
        channel=channel,
        recipient_id=recipient_id,
        recipient_email=recipient_email,
        recipient_phone=recipient_phone,
        subject=subject,
        body=body,
        status=status,
        error_message=error_message,
        sent_at=datetime.now() if status == "sent" else None
    )
    db.add(log)
    db.commit()
    return log


def render_email_template(event_type: EventType, data: Dict[str, Any]) -> str:
    """Render email template for an event type"""
    template_path = os.path.join(
        os.path.dirname(__file__),
        'templates',
        f'{event_type.value}.html'
    )

    # Check if custom template exists
    if os.path.exists(template_path):
        with open(template_path, 'r', encoding='utf-8') as f:
            template = f.read()
            # Simple template variable substitution
            for key, value in data.items():
                template = template.replace(f'{{{{{key}}}}}', str(value) if value else '')
            return template

    # Fallback to default templates
    return _get_default_template(event_type, data)


def _get_default_template(event_type: EventType, data: Dict[str, Any]) -> str:
    """Generate default email template"""
    base_style = """
        <style>
            body { font-family: Arial, sans-serif; line-height: 1.6; color: #333; }
            .container { max-width: 600px; margin: 0 auto; padding: 20px; }
            .header { background: #4F46E5; color: white; padding: 20px; text-align: center; border-radius: 8px 8px 0 0; }
            .content { background: #F9FAFB; padding: 20px; border: 1px solid #E5E7EB; }
            .footer { padding: 15px; text-align: center; color: #6B7280; font-size: 12px; border-top: 1px solid #E5E7EB; }
            .button { display: inline-block; background: #4F46E5; color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px; margin: 10px 0; }
            .info-box { background: white; border: 1px solid #E5E7EB; border-radius: 6px; padding: 15px; margin: 15px 0; }
            .label { font-weight: bold; color: #4B5563; }
        </style>
    """

    templates = {
        EventType.MEMBER_APPROVED: f"""
            <html><head>{base_style}</head><body>
                <div class="container">
                    <div class="header"><h2>Selection Approved!</h2></div>
                    <div class="content">
                        <p>Dear {data.get('member_name', 'Member')},</p>
                        <p>Great news! Your selection for <strong>{data.get('department_name', 'the department')}</strong> has been approved.</p>
                        <div class="info-box">
                            <p><span class="label">Department:</span> {data.get('department_name', 'N/A')}</p>
                            <p><span class="label">Category:</span> {data.get('category_name', 'N/A')}</p>
                        </div>
                        <p>You can view your approved departments in the member portal.</p>
                    </div>
                    <div class="footer">RFM Stellenbosch Department Selection</div>
                </div>
            </body></html>
        """,

        EventType.MEMBER_REJECTED: f"""
            <html><head>{base_style}</head><body>
                <div class="container">
                    <div class="header" style="background: #DC2626;"><h2>Selection Update</h2></div>
                    <div class="content">
                        <p>Dear {data.get('member_name', 'Member')},</p>
                        <p>We regret to inform you that your selection for <strong>{data.get('department_name', 'the department')}</strong> was not approved.</p>
                        {f'<div class="info-box"><p><span class="label">Reason:</span> {data.get("admin_note")}</p></div>' if data.get('admin_note') else ''}
                        <p>If you have questions, please contact the church office or speak to a leader.</p>
                    </div>
                    <div class="footer">RFM Stellenbosch Department Selection</div>
                </div>
            </body></html>
        """,

        EventType.DEPARTMENT_ASSIGNED: f"""
            <html><head>{base_style}</head><body>
                <div class="container">
                    <div class="header"><h2>Department Assignment</h2></div>
                    <div class="content">
                        <p>Dear {data.get('member_name', 'Member')},</p>
                        <p>You have been assigned to <strong>{data.get('department_name', 'a department')}</strong> by the admin team.</p>
                        <div class="info-box">
                            <p><span class="label">Department:</span> {data.get('department_name', 'N/A')}</p>
                            <p><span class="label">Category:</span> {data.get('category_name', 'N/A')}</p>
                        </div>
                        {f'<p><span class="label">Note:</span> {data.get("admin_note")}</p>' if data.get('admin_note') else ''}
                        <p>Please log in to the member portal to view your assignments.</p>
                    </div>
                    <div class="footer">RFM Stellenbosch Department Selection</div>
                </div>
            </body></html>
        """,

        EventType.RESULTS_PUBLISHED: f"""
            <html><head>{base_style}</head><body>
                <div class="container">
                    <div class="header"><h2>Results Published!</h2></div>
                    <div class="content">
                        <p>Dear Member,</p>
                        <p>The department selection results for <strong>{data.get('year', '2026')}</strong> are now available!</p>
                        <p>Please log in to the member portal to view your approved department assignments.</p>
                        <p style="text-align: center;">
                            <a href="#" class="button">View My Results</a>
                        </p>
                    </div>
                    <div class="footer">RFM Stellenbosch Department Selection</div>
                </div>
            </body></html>
        """,

        EventType.APPEAL_SUBMITTED: f"""
            <html><head>{base_style}</head><body>
                <div class="container">
                    <div class="header" style="background: #F59E0B;"><h2>New Appeal Submitted</h2></div>
                    <div class="content">
                        <p>A new appeal has been submitted and requires your attention.</p>
                        <div class="info-box">
                            <p><span class="label">Member:</span> {data.get('member_name', 'N/A')}</p>
                            <p><span class="label">Phone:</span> {data.get('member_phone', 'N/A')}</p>
                            {f'<p><span class="label">Unwanted Dept:</span> {data.get("unwanted_department")}</p>' if data.get('unwanted_department') else ''}
                            {f'<p><span class="label">Wanted Dept:</span> {data.get("wanted_department")}</p>' if data.get('wanted_department') else ''}
                            {f'<p><span class="label">Reason:</span> {data.get("reason")}</p>' if data.get('reason') else ''}
                        </div>
                        <p>Please review this appeal in the admin panel.</p>
                    </div>
                    <div class="footer">RFM Stellenbosch Department Selection</div>
                </div>
            </body></html>
        """,

        EventType.APPEAL_RESOLVED: f"""
            <html><head>{base_style}</head><body>
                <div class="container">
                    <div class="header"><h2>Appeal Resolved</h2></div>
                    <div class="content">
                        <p>Dear {data.get('member_name', 'Member')},</p>
                        <p>Your appeal has been reviewed and <strong>{data.get('status', 'processed')}</strong>.</p>
                        {f'<div class="info-box"><p><span class="label">Response:</span> {data.get("admin_response")}</p></div>' if data.get('admin_response') else ''}
                        <p>Please check the member portal for your updated department assignments.</p>
                    </div>
                    <div class="footer">RFM Stellenbosch Department Selection</div>
                </div>
            </body></html>
        """,

        EventType.MEETING_CREATED: f"""
            <html><head>{base_style}</head><body>
                <div class="container">
                    <div class="header"><h2>New Meeting Scheduled</h2></div>
                    <div class="content">
                        <p>A new meeting has been scheduled:</p>
                        <div class="info-box">
                            <h3 style="margin-top: 0;">{data.get('title', 'Meeting')}</h3>
                            <p><span class="label">Date:</span> {data.get('meeting_date', 'TBD')}</p>
                            <p><span class="label">Time:</span> {data.get('start_time', '')} - {data.get('end_time', '')}</p>
                            {f'<p><span class="label">Location:</span> {data.get("location")}</p>' if data.get('location') else ''}
                            {f'<p><span class="label">Department:</span> {data.get("department_name")}</p>' if data.get('department_name') else ''}
                            {f'<p>{data.get("description")}</p>' if data.get('description') else ''}
                        </div>
                        {f'<p style="text-align: center;"><a href="{data.get("meeting_link")}" class="button">Join Meeting</a></p>' if data.get('meeting_link') else ''}
                    </div>
                    <div class="footer">RFM Stellenbosch Department Selection</div>
                </div>
            </body></html>
        """,

        EventType.MEETING_REMINDER: f"""
            <html><head>{base_style}</head><body>
                <div class="container">
                    <div class="header" style="background: #10B981;"><h2>Meeting Reminder</h2></div>
                    <div class="content">
                        <p>This is a reminder about your upcoming meeting:</p>
                        <div class="info-box">
                            <h3 style="margin-top: 0;">{data.get('title', 'Meeting')}</h3>
                            <p><span class="label">Date:</span> {data.get('meeting_date', 'Tomorrow')}</p>
                            <p><span class="label">Time:</span> {data.get('start_time', '')} - {data.get('end_time', '')}</p>
                            {f'<p><span class="label">Location:</span> {data.get("location")}</p>' if data.get('location') else ''}
                        </div>
                        {f'<p style="text-align: center;"><a href="{data.get("meeting_link")}" class="button">Join Meeting</a></p>' if data.get('meeting_link') else ''}
                    </div>
                    <div class="footer">RFM Stellenbosch Department Selection</div>
                </div>
            </body></html>
        """,

        EventType.MEETING_UPDATED: f"""
            <html><head>{base_style}</head><body>
                <div class="container">
                    <div class="header" style="background: #F59E0B;"><h2>Meeting Updated</h2></div>
                    <div class="content">
                        <p>A meeting has been updated. Please note the new details:</p>
                        <div class="info-box">
                            <h3 style="margin-top: 0;">{data.get('title', 'Meeting')}</h3>
                            <p><span class="label">Date:</span> {data.get('meeting_date', 'TBD')}</p>
                            <p><span class="label">Time:</span> {data.get('start_time', '')} - {data.get('end_time', '')}</p>
                            {f'<p><span class="label">Location:</span> {data.get("location")}</p>' if data.get('location') else ''}
                        </div>
                    </div>
                    <div class="footer">RFM Stellenbosch Department Selection</div>
                </div>
            </body></html>
        """,

        EventType.MEETING_CANCELLED: f"""
            <html><head>{base_style}</head><body>
                <div class="container">
                    <div class="header" style="background: #DC2626;"><h2>Meeting Cancelled</h2></div>
                    <div class="content">
                        <p>The following meeting has been cancelled:</p>
                        <div class="info-box" style="border-color: #DC2626;">
                            <h3 style="margin-top: 0; text-decoration: line-through;">{data.get('title', 'Meeting')}</h3>
                            <p><span class="label">Date:</span> {data.get('meeting_date', 'N/A')}</p>
                            <p><span class="label">Time:</span> {data.get('start_time', '')} - {data.get('end_time', '')}</p>
                        </div>
                    </div>
                    <div class="footer">RFM Stellenbosch Department Selection</div>
                </div>
            </body></html>
        """,
    }

    return templates.get(event_type, "<p>Notification</p>")


def get_email_subject(event_type: EventType, data: Dict[str, Any]) -> str:
    """Get formatted email subject for an event type"""
    subject_template = EMAIL_SUBJECTS.get(event_type, "Notification")
    # Format with data if placeholders exist
    try:
        return subject_template.format(**data)
    except KeyError:
        return subject_template


def dispatch_event(
    db: Session,
    event_type: EventType,
    data: Dict[str, Any],
    recipients: Optional[List[Dict[str, Any]]] = None
):
    """
    Dispatch a notification event to all enabled channels.

    Args:
        db: Database session
        event_type: The type of event to dispatch
        data: Event-specific data for template rendering
        recipients: List of recipients, each with 'id', 'email', 'phone' keys.
                   If None, uses data['recipients'] or data for single recipient.
    """
    # Get notification config for this event
    config = get_notification_config(db, event_type)

    if not config:
        print(f"No notification config found for {event_type.value}")
        return

    # Check if email is enabled for this event
    if not config.email_enabled:
        print(f"Email notifications disabled for {event_type.value}")
        return

    # Get email settings and channel
    settings = get_email_settings(db)
    email_channel, channel_name = get_email_channel(settings)

    if not email_channel:
        print("No email channel configured, skipping notifications")
        return

    # Determine recipients
    if recipients is None:
        if 'recipients' in data:
            recipients = data['recipients']
        elif data.get('member_email'):
            recipients = [{
                'id': data.get('member_id'),
                'email': data.get('member_email'),
                'phone': data.get('member_phone')
            }]
        else:
            print(f"No recipients for event {event_type.value}")
            return

    # Render email template
    subject = get_email_subject(event_type, data)
    body = render_email_template(event_type, data)

    # Send to each recipient
    for recipient in recipients:
        recipient_email = recipient.get('email')
        if not recipient_email:
            continue

        success, error = email_channel.send(recipient_email, subject, body)

        # Log the notification
        log_notification(
            db=db,
            event_type=event_type,
            channel=channel_name,
            recipient_id=recipient.get('id'),
            recipient_email=recipient_email,
            recipient_phone=recipient.get('phone'),
            subject=subject,
            body=body,
            status="sent" if success else "failed",
            error_message=error
        )

        if success:
            print(f"[{channel_name}] Email sent to {recipient_email} for {event_type.value}")
        else:
            print(f"[{channel_name}] Failed to send to {recipient_email}: {error}")


def dispatch_to_admins(
    db: Session,
    event_type: EventType,
    data: Dict[str, Any]
):
    """
    Dispatch a notification event to admin email(s).
    Uses the from_email of the active channel as admin recipient.
    """
    settings = get_email_settings(db)

    # Get admin email from active channel
    admin_email = None
    if os.getenv('RESEND_ENABLED', '').lower() == 'true' or settings.get('resend_enabled') == 'true':
        admin_email = os.getenv('RESEND_FROM_EMAIL') or settings.get('resend_from_email')
    if not admin_email:
        admin_email = os.getenv('SMTP_FROM_EMAIL') or settings.get('smtp_from_email')

    if admin_email:
        dispatch_event(db, event_type, data, recipients=[{
            'id': None,
            'email': admin_email,
            'phone': None
        }])
