"""
Event dispatcher for the notification system.
Routes events to appropriate channels based on configuration.
"""

import os
import time
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
    """Generate modern email template with table-based layout for email client compatibility"""

    def base_template(title: str, icon: str, accent_color: str, content: str, button_text: str = None, button_url: str = None):
        """Generate a modern email template wrapper"""
        button_html = ""
        if button_text and button_url:
            button_html = f'''
                <tr>
                    <td align="center" style="padding: 30px 0 20px 0;">
                        <a href="{button_url}" style="display: inline-block; background: {accent_color}; color: #ffffff; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 16px; font-weight: 600; text-decoration: none; padding: 14px 32px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.1);">{button_text}</a>
                    </td>
                </tr>
            '''

        return f'''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
</head>
<body style="margin: 0; padding: 0; background-color: #f4f4f5; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background-color: #f4f4f5;">
        <tr>
            <td align="center" style="padding: 40px 20px;">
                <!-- Main Container -->
                <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width: 600px; background-color: #ffffff; border-radius: 16px; box-shadow: 0 4px 6px rgba(0, 0, 0, 0.05), 0 10px 20px rgba(0, 0, 0, 0.03); overflow: hidden;">
                    <!-- Header -->
                    <tr>
                        <td style="background: linear-gradient(135deg, {accent_color} 0%, {accent_color}dd 100%); padding: 40px 40px 30px 40px; text-align: center;">
                            <div style="font-size: 48px; margin-bottom: 16px;">{icon}</div>
                            <h1 style="margin: 0; color: #ffffff; font-size: 24px; font-weight: 700; letter-spacing: -0.5px;">{title}</h1>
                        </td>
                    </tr>
                    <!-- Content -->
                    <tr>
                        <td style="padding: 40px;">
                            {content}
                        </td>
                    </tr>
                    <!-- Button -->
                    {button_html}
                    <!-- Footer -->
                    <tr>
                        <td style="background-color: #fafafa; padding: 30px 40px; border-top: 1px solid #e5e7eb;">
                            <table role="presentation" width="100%" cellspacing="0" cellpadding="0">
                                <tr>
                                    <td align="center">
                                        <p style="margin: 0 0 8px 0; color: #6b7280; font-size: 14px; font-weight: 600;">Revival Fire Ministries</p>
                                        <p style="margin: 0; color: #9ca3af; font-size: 13px;">Stellenbosch, South Africa</p>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                </table>
                <!-- Sub-footer -->
                <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width: 600px;">
                    <tr>
                        <td align="center" style="padding: 24px 20px;">
                            <p style="margin: 0; color: #9ca3af; font-size: 12px;">
                                This is an automated message from the Department Selection System.
                            </p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>
'''

    def info_card(items: list) -> str:
        """Generate an info card with key-value pairs"""
        rows = ""
        for label, value in items:
            if value:
                rows += f'''
                    <tr>
                        <td style="padding: 12px 0; border-bottom: 1px solid #f3f4f6;">
                            <span style="color: #6b7280; font-size: 13px; text-transform: uppercase; letter-spacing: 0.5px;">{label}</span>
                            <p style="margin: 4px 0 0 0; color: #111827; font-size: 16px; font-weight: 500;">{value}</p>
                        </td>
                    </tr>
                '''
        return f'''
            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background-color: #f9fafb; border-radius: 12px; margin: 24px 0;">
                <tr>
                    <td style="padding: 20px 24px;">
                        <table role="presentation" width="100%" cellspacing="0" cellpadding="0">
                            {rows}
                        </table>
                    </td>
                </tr>
            </table>
        '''

    def greeting(name: str) -> str:
        return f'<p style="margin: 0 0 20px 0; color: #374151; font-size: 16px; line-height: 1.6;">Dear <strong>{name}</strong>,</p>'

    def paragraph(text: str) -> str:
        return f'<p style="margin: 0 0 16px 0; color: #374151; font-size: 16px; line-height: 1.6;">{text}</p>'

    # Build portal link for member auto-login
    portal_link = None
    if data.get('app_url') and data.get('recipient_phone'):
        portal_link = f"{data.get('app_url')}/portal?phone={data.get('recipient_phone')}"

    # Build admin link
    admin_link = f"{data.get('app_url')}/admin" if data.get('app_url') else None

    templates = {
        EventType.MEMBER_APPROVED: base_template(
            title="Selection Approved!",
            icon="&#127881;",  # Party popper
            accent_color="#10b981",  # Green
            content=f'''
                {greeting(data.get('recipient_name') or data.get('member_name', 'Member'))}
                {paragraph(f"Great news! Your selection for <strong>{data.get('department_name', 'the department')}</strong> has been approved.")}
                {info_card([
                    ("Department", data.get('department_name')),
                    ("Category", data.get('category_name'))
                ])}
                {paragraph("You can now view your approved departments in the member portal. We're excited to have you serve with us!")}
            ''',
            button_text="View My Departments" if portal_link else None,
            button_url=portal_link
        ),

        EventType.MEMBER_REJECTED: base_template(
            title="Selection Update",
            icon="&#128172;",  # Speech bubble
            accent_color="#6b7280",  # Gray
            content=f'''
                {greeting(data.get('recipient_name') or data.get('member_name', 'Member'))}
                {paragraph(f"We regret to inform you that your selection for <strong>{data.get('department_name', 'the department')}</strong> was not approved at this time.")}
                {info_card([
                    ("Department", data.get('department_name')),
                    ("Feedback", data.get('admin_note'))
                ]) if data.get('admin_note') else info_card([("Department", data.get('department_name'))])}
                {paragraph("If you have any questions or would like to discuss other opportunities to serve, please don't hesitate to reach out to a church leader.")}
            ''',
            button_text="View My Portal" if portal_link else None,
            button_url=portal_link
        ),

        EventType.DEPARTMENT_ASSIGNED: base_template(
            title="You've Been Assigned!",
            icon="&#11088;",  # Star
            accent_color="#8b5cf6",  # Purple
            content=f'''
                {greeting(data.get('recipient_name') or data.get('member_name', 'Member'))}
                {paragraph(f"You have been assigned to <strong>{data.get('department_name', 'a department')}</strong> by our leadership team.")}
                {info_card([
                    ("Department", data.get('department_name')),
                    ("Category", data.get('category_name')),
                    ("Note", data.get('admin_note'))
                ])}
                {paragraph("Click below to view your assignments and connect with your team.")}
            ''',
            button_text="View Assignment" if portal_link else None,
            button_url=portal_link
        ),

        EventType.RESULTS_PUBLISHED: base_template(
            title="Results Are Live!",
            icon="&#128227;",  # Megaphone
            accent_color="#4f46e5",  # Indigo
            content=f'''
                {greeting(data.get('recipient_name', 'Member'))}
                {paragraph(f"The department selection results for <strong>{data.get('year', '2026')}</strong> are now available!")}
                {paragraph("Click below to view your approved department assignments and start connecting with your teams.")}
            ''',
            button_text="View My Results" if portal_link else None,
            button_url=portal_link
        ),

        EventType.APPEAL_SUBMITTED: base_template(
            title="New Appeal Received",
            icon="&#128221;",  # Memo
            accent_color="#f59e0b",  # Amber
            content=f'''
                {paragraph("A new appeal has been submitted and requires your attention.")}
                {info_card([
                    ("Member", data.get('member_name')),
                    ("Phone", data.get('member_phone')),
                    ("Current Department", data.get('unwanted_department')),
                    ("Requested Department", data.get('wanted_department')),
                    ("Reason", data.get('reason'))
                ])}
                {paragraph("Please review this appeal in the admin panel at your earliest convenience.")}
            ''',
            button_text="Review Appeal" if admin_link else None,
            button_url=admin_link
        ),

        EventType.APPEAL_RESOLVED: base_template(
            title="Appeal Resolved",
            icon="&#9989;" if data.get('status') == 'approved' else "&#128172;",  # Checkmark or speech bubble
            accent_color="#10b981" if data.get('status') == 'approved' else "#6b7280",
            content=f'''
                {greeting(data.get('recipient_name') or data.get('member_name', 'Member'))}
                {paragraph(f"Your appeal has been reviewed and <strong>{data.get('status', 'processed')}</strong>.")}
                {info_card([("Response", data.get('admin_response'))]) if data.get('admin_response') else ''}
                {paragraph("Click below to view your updated department assignments.")}
            ''',
            button_text="View Updates" if portal_link else None,
            button_url=portal_link
        ),

        EventType.MEETING_CREATED: base_template(
            title="New Meeting Invite",
            icon="&#128197;",  # Calendar
            accent_color="#4f46e5",  # Indigo
            content=f'''
                {greeting(data.get('recipient_name', 'Member'))}
                {paragraph("You're invited to a meeting scheduled for your department. Please RSVP to let us know if you'll be attending.")}
                <div style="background: linear-gradient(135deg, #4f46e5 0%, #7c3aed 100%); border-radius: 12px; padding: 24px; margin: 24px 0;">
                    <h2 style="margin: 0 0 16px 0; color: #ffffff; font-size: 20px; font-weight: 600;">{data.get('title', 'Meeting')}</h2>
                    <table role="presentation" cellspacing="0" cellpadding="0">
                        <tr>
                            <td style="padding-right: 24px;">
                                <p style="margin: 0; color: rgba(255,255,255,0.8); font-size: 12px; text-transform: uppercase;">Date</p>
                                <p style="margin: 4px 0 0 0; color: #ffffff; font-size: 16px; font-weight: 500;">{data.get('meeting_date', 'TBD')}</p>
                            </td>
                            <td>
                                <p style="margin: 0; color: rgba(255,255,255,0.8); font-size: 12px; text-transform: uppercase;">Time</p>
                                <p style="margin: 4px 0 0 0; color: #ffffff; font-size: 16px; font-weight: 500;">{data.get('start_time', '')} - {data.get('end_time', '')}</p>
                            </td>
                        </tr>
                    </table>
                </div>
                {info_card([
                    ("Location", data.get('location')),
                    ("Department", data.get('department_name')),
                    ("Details", data.get('description'))
                ])}
                {paragraph("Please click the button below to RSVP for this meeting.") if data.get('rsvp_link') else ''}
            ''',
            button_text="RSVP Now" if data.get('rsvp_link') else ("Join Meeting" if data.get('meeting_link') else None),
            button_url=data.get('rsvp_link') or data.get('meeting_link')
        ),

        EventType.MEETING_REMINDER: base_template(
            title="Meeting Today",
            icon="&#9200;",  # Alarm clock
            accent_color="#10b981",  # Green
            content=f'''
                {greeting(data.get('recipient_name', 'Member'))}
                {paragraph("This is a friendly reminder about your meeting today.")}
                <div style="background: linear-gradient(135deg, #10b981 0%, #059669 100%); border-radius: 12px; padding: 24px; margin: 24px 0;">
                    <h2 style="margin: 0 0 16px 0; color: #ffffff; font-size: 20px; font-weight: 600;">{data.get('title', 'Meeting')}</h2>
                    <table role="presentation" cellspacing="0" cellpadding="0">
                        <tr>
                            <td style="padding-right: 24px;">
                                <p style="margin: 0; color: rgba(255,255,255,0.8); font-size: 12px; text-transform: uppercase;">Time</p>
                                <p style="margin: 4px 0 0 0; color: #ffffff; font-size: 16px; font-weight: 500;">{data.get('start_time', '')} - {data.get('end_time', '')}</p>
                            </td>
                            <td>
                                <p style="margin: 0; color: rgba(255,255,255,0.8); font-size: 12px; text-transform: uppercase;">Location</p>
                                <p style="margin: 4px 0 0 0; color: #ffffff; font-size: 16px; font-weight: 500;">{data.get('location', 'TBD')}</p>
                            </td>
                        </tr>
                    </table>
                </div>
            ''',
            button_text="View in Portal" if portal_link else ("Join Meeting" if data.get('meeting_link') else None),
            button_url=portal_link or data.get('meeting_link')
        ),

        EventType.MEETING_UPDATED: base_template(
            title="Meeting Updated",
            icon="&#128260;",  # Arrows
            accent_color="#f59e0b",  # Amber
            content=f'''
                {paragraph("A meeting has been updated. Please note the new details below.")}
                <div style="background: linear-gradient(135deg, #f59e0b 0%, #d97706 100%); border-radius: 12px; padding: 24px; margin: 24px 0;">
                    <h2 style="margin: 0 0 16px 0; color: #ffffff; font-size: 20px; font-weight: 600;">{data.get('title', 'Meeting')}</h2>
                    <table role="presentation" cellspacing="0" cellpadding="0">
                        <tr>
                            <td style="padding-right: 24px;">
                                <p style="margin: 0; color: rgba(255,255,255,0.8); font-size: 12px; text-transform: uppercase;">New Date</p>
                                <p style="margin: 4px 0 0 0; color: #ffffff; font-size: 16px; font-weight: 500;">{data.get('meeting_date', 'TBD')}</p>
                            </td>
                            <td>
                                <p style="margin: 0; color: rgba(255,255,255,0.8); font-size: 12px; text-transform: uppercase;">New Time</p>
                                <p style="margin: 4px 0 0 0; color: #ffffff; font-size: 16px; font-weight: 500;">{data.get('start_time', '')} - {data.get('end_time', '')}</p>
                            </td>
                        </tr>
                    </table>
                </div>
                {info_card([("Location", data.get('location'))]) if data.get('location') else ''}
            '''
        ),

        EventType.MEETING_CANCELLED: base_template(
            title="Meeting Cancelled",
            icon="&#10060;",  # X mark
            accent_color="#ef4444",  # Red
            content=f'''
                {paragraph("The following meeting has been cancelled.")}
                <div style="background-color: #fef2f2; border: 2px solid #fecaca; border-radius: 12px; padding: 24px; margin: 24px 0;">
                    <h2 style="margin: 0 0 16px 0; color: #991b1b; font-size: 20px; font-weight: 600; text-decoration: line-through;">{data.get('title', 'Meeting')}</h2>
                    <table role="presentation" cellspacing="0" cellpadding="0">
                        <tr>
                            <td style="padding-right: 24px;">
                                <p style="margin: 0; color: #991b1b; font-size: 13px; text-transform: uppercase;">Date</p>
                                <p style="margin: 4px 0 0 0; color: #7f1d1d; font-size: 16px;">{data.get('meeting_date', 'N/A')}</p>
                            </td>
                            <td>
                                <p style="margin: 0; color: #991b1b; font-size: 13px; text-transform: uppercase;">Time</p>
                                <p style="margin: 4px 0 0 0; color: #7f1d1d; font-size: 16px;">{data.get('start_time', '')} - {data.get('end_time', '')}</p>
                            </td>
                        </tr>
                    </table>
                </div>
                {paragraph("Please disregard any previous meeting invitations. We apologize for any inconvenience.")}
            '''
        ),

        EventType.POSTER_REQUEST_SUBMITTED: base_template(
            title="New Poster Request",
            icon="&#127912;",  # Art palette
            accent_color="#9333ea",  # Purple
            content=f'''
                {paragraph("A new poster request has been submitted and needs your attention.")}
                <div style="background: linear-gradient(135deg, #9333ea 0%, #7c3aed 100%); border-radius: 12px; padding: 24px; margin: 24px 0;">
                    <h2 style="margin: 0 0 16px 0; color: #ffffff; font-size: 20px; font-weight: 600;">{data.get('event_name', 'Event')}</h2>
                    <table role="presentation" cellspacing="0" cellpadding="0">
                        <tr>
                            <td style="padding-right: 24px;">
                                <p style="margin: 0; color: rgba(255,255,255,0.8); font-size: 12px; text-transform: uppercase;">Event Date</p>
                                <p style="margin: 4px 0 0 0; color: #ffffff; font-size: 16px; font-weight: 500;">{data.get('event_date', 'TBD')}</p>
                            </td>
                            <td>
                                <p style="margin: 0; color: rgba(255,255,255,0.8); font-size: 12px; text-transform: uppercase;">Time</p>
                                <p style="margin: 4px 0 0 0; color: #ffffff; font-size: 16px; font-weight: 500;">{data.get('event_time', '')}</p>
                            </td>
                        </tr>
                    </table>
                </div>
                {info_card([
                    ("Requested By", data.get('requester_name')),
                    ("Contact Email", data.get('requester_email')),
                    ("Ministry/Department", data.get('ministry_department')),
                    ("Venue/Platform", data.get('venue_platform')),
                    ("Purpose", data.get('purpose')),
                    ("Output Formats", data.get('output_formats_display')),
                    ("Speakers", data.get('speakers_display')),
                    ("Theme/Tagline", data.get('theme_tagline')),
                    ("Scripture", data.get('scripture')),
                    ("Target Audience", data.get('target_audience')),
                    ("Additional Notes", data.get('additional_notes'))
                ])}
                {paragraph("Please log in to the portal to view the full request details and acknowledge it.")}
            ''',
            button_text="View Request" if portal_link else None,
            button_url=portal_link
        ),

        EventType.POSTER_REQUEST_ACKNOWLEDGED: base_template(
            title="Your Poster Request is Being Processed",
            icon="&#9989;",  # Checkmark
            accent_color="#10b981",  # Green
            content=f'''
                {greeting(data.get('recipient_name', 'Member'))}
                {paragraph(f"Great news! Your poster request for <strong>{data.get('event_name', 'your event')}</strong> has been acknowledged and is now being worked on by the media team.")}
                {info_card([
                    ("Event", data.get('event_name')),
                    ("Event Date", data.get('event_date')),
                    ("Acknowledged By", data.get('acknowledged_by_name'))
                ])}
                {paragraph("You will receive your poster design soon. If you have any updates or additional requirements, please reach out to the media team.")}
            ''',
            button_text="View My Requests" if portal_link else None,
            button_url=portal_link
        ),

        EventType.POSTER_REQUEST_COMPLETED: base_template(
            title="Your Poster is Ready!",
            icon="&#127912;",  # Art palette
            accent_color="#10b981",  # Green
            content=f'''
                {greeting(data.get('recipient_name', 'Member'))}
                {paragraph(f"Your poster for <strong>{data.get('event_name', 'your event')}</strong> is now complete!")}
                {info_card([
                    ("Event", data.get('event_name')),
                    ("Event Date", data.get('event_date')),
                    ("Completed By", data.get('completed_by_name'))
                ])}
                <div style="background-color: #ecfdf5; border: 2px solid #a7f3d0; border-radius: 12px; padding: 20px; margin: 24px 0; text-align: center;">
                    <p style="margin: 0; color: #065f46; font-size: 16px; font-weight: 600;">
                        &#128172; Your poster has been shared in the WhatsApp group
                    </p>
                    <p style="margin: 8px 0 0 0; color: #047857; font-size: 14px;">
                        Please check the group to download your design
                    </p>
                </div>
                {paragraph("Thank you for using the poster request service. If you need any changes, please reach out to the media team.")}
            ''',
            button_text="View My Requests" if portal_link else None,
            button_url=portal_link
        ),

        EventType.PROGRAM_PARTICIPANT_ADDED: base_template(
            title="You're on the Program!",
            icon="&#127926;",  # Musical note
            accent_color="#4f46e5",  # Indigo
            content=f'''
                {greeting(data.get('recipient_name', 'Member'))}
                {paragraph(f"You have been added as a participant in an upcoming service program.")}
                <div style="background: linear-gradient(135deg, #4f46e5 0%, #7c3aed 100%); border-radius: 12px; padding: 24px; margin: 24px 0;">
                    <h2 style="margin: 0 0 16px 0; color: #ffffff; font-size: 20px; font-weight: 600;">{data.get('title', 'Service Program')}</h2>
                    <table role="presentation" cellspacing="0" cellpadding="0">
                        <tr>
                            <td style="padding-right: 24px;">
                                <p style="margin: 0; color: rgba(255,255,255,0.8); font-size: 12px; text-transform: uppercase;">Date</p>
                                <p style="margin: 4px 0 0 0; color: #ffffff; font-size: 16px; font-weight: 500;">{data.get('service_date', 'TBD')}</p>
                            </td>
                            <td>
                                <p style="margin: 0; color: rgba(255,255,255,0.8); font-size: 12px; text-transform: uppercase;">Your Role</p>
                                <p style="margin: 4px 0 0 0; color: #ffffff; font-size: 16px; font-weight: 500;">{data.get('role', 'Participant')}</p>
                            </td>
                        </tr>
                    </table>
                </div>
                {paragraph("Please make sure you are prepared and arrive on time. If you have any questions or are unable to attend, please inform the service coordinator as soon as possible.")}
            '''
        ),
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

    # Get base URL for links in emails
    app_url = os.getenv('APP_URL', '').rstrip('/')

    # Send to each recipient
    # Rate limiting: Resend allows max 2 requests per second, so we wait 0.5s between emails
    is_resend = channel_name == "resend"

    for i, recipient in enumerate(recipients):
        recipient_email = recipient.get('email')
        if not recipient_email:
            continue

        # Apply rate limiting for Resend (after first email)
        if is_resend and i > 0:
            time.sleep(0.5)  # 500ms delay = max 2 requests per second

        # Merge recipient data into template data for personalized emails
        recipient_data = {**data}
        recipient_data['recipient_name'] = recipient.get('name', '')
        recipient_data['recipient_phone'] = recipient.get('phone', '')
        recipient_data['recipient_email'] = recipient_email
        recipient_data['app_url'] = app_url

        # Build RSVP link if phone is available
        if recipient.get('phone') and app_url:
            recipient_data['rsvp_link'] = f"{app_url}/portal?phone={recipient.get('phone')}"

        # Render email template per-recipient for personalized content
        subject = get_email_subject(event_type, recipient_data)
        body = render_email_template(event_type, recipient_data)

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
