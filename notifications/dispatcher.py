"""
Notification dispatcher - routes events to configured channels (email, SMS, push).
"""

import os
import json
import time
from datetime import datetime
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session

from notifications.events import EventType, EMAIL_SUBJECTS


def get_email_settings(db: Session) -> dict:
    """Get email settings from database"""
    from models import Settings
    keys = [
        'smtp_enabled', 'smtp_host', 'smtp_port',
        'smtp_username', 'smtp_password',
        'smtp_from_name', 'smtp_from_email',
        'resend_enabled', 'resend_api_key',
        'resend_from_name', 'resend_from_email'
    ]
    settings = {}
    for key in keys:
        s = db.query(Settings).filter(Settings.key == key).first()
        if s:
            settings[key] = s.value
    return settings


def log_notification(db: Session, event_type: EventType, channel: str, recipient_id: int = None,
                     recipient_email: str = None, recipient_phone: str = None,
                     status: str = "sent", error_message: str = None) -> None:
    """Log a notification to the database"""
    from models import NotificationLog
    log = NotificationLog(
        event_type=event_type.value,
        channel=channel,
        recipient_id=recipient_id,
        recipient_email=recipient_email,
        recipient_phone=recipient_phone,
        status=status,
        error_message=error_message,
        sent_at=datetime.utcnow()
    )
    db.add(log)
    db.commit()
    return log


def _format_prayer_points_html(prayer_points: list) -> str:
    """Format prayer points as HTML list items, showing linked activity if present."""
    items = []
    for pp in prayer_points:
        if isinstance(pp, dict):
            text = pp.get("text", "")
            linked = pp.get("linked_activity", "")
            if text:
                linked_html = f' <span style="font-size: 12px; color: #e11d48; font-style: italic;">({linked})</span>' if linked else ""
                items.append(f'<li style="margin-bottom: 8px; font-size: 14px; line-height: 1.5;">{text}{linked_html}</li>')
        elif pp:
            items.append(f'<li style="margin-bottom: 8px; font-size: 14px; line-height: 1.5;">{pp}</li>')
    return "".join(items)


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
    """Generate modern, clean email templates"""

    # ── Design System ──
    FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
    BG = "#f8fafc"
    CARD_BG = "#ffffff"
    TEXT = "#1e293b"
    TEXT_SECONDARY = "#64748b"
    TEXT_MUTED = "#94a3b8"
    BORDER = "#e2e8f0"
    CHURCH_NAME = "Revival Fire Ministries"

    def base_template(title: str, accent_color: str, content: str, button_text: str = None, button_url: str = None):
        button_html = ""
        if button_text and button_url:
            button_html = f'''
                <tr><td style="padding: 8px 0 0 0;">
                    <a href="{button_url}" style="display: inline-block; background: {accent_color}; color: #ffffff; font-family: {FONT}; font-size: 14px; font-weight: 600; text-decoration: none; padding: 12px 28px; border-radius: 8px;">{button_text}</a>
                </td></tr>
            '''

        return f'''<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>{title}</title></head>
<body style="margin: 0; padding: 0; background-color: {BG}; font-family: {FONT};">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background-color: {BG};">
<tr><td align="center" style="padding: 32px 16px;">

<!-- Card -->
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width: 560px; background-color: {CARD_BG}; border-radius: 12px; border: 1px solid {BORDER};">
  <!-- Accent bar -->
  <tr><td style="height: 4px; background: {accent_color}; border-radius: 12px 12px 0 0;"></td></tr>
  <!-- Body -->
  <tr><td style="padding: 32px 32px 24px 32px;">
    {content}
    {button_html}
  </td></tr>
  <!-- Footer -->
  <tr><td style="padding: 16px 32px 20px 32px; border-top: 1px solid {BORDER};">
    <p style="margin: 0; color: {TEXT_MUTED}; font-size: 12px;">{CHURCH_NAME} &middot; Stellenbosch</p>
  </td></tr>
</table>

</td></tr>
</table>
</body></html>'''

    def heading(text: str) -> str:
        return f'<h2 style="margin: 0 0 16px 0; color: {TEXT}; font-size: 20px; font-weight: 700; letter-spacing: -0.3px;">{text}</h2>'

    def greeting(name: str) -> str:
        return f'<p style="margin: 0 0 16px 0; color: {TEXT}; font-size: 15px; line-height: 1.6;">Hi <strong>{name}</strong>,</p>'

    def paragraph(text: str) -> str:
        return f'<p style="margin: 0 0 14px 0; color: {TEXT_SECONDARY}; font-size: 15px; line-height: 1.6;">{text}</p>'

    def detail_row(label: str, value: str) -> str:
        if not value:
            return ''
        return f'''<tr>
            <td style="padding: 8px 0; vertical-align: top; width: 120px;"><span style="color: {TEXT_MUTED}; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px;">{label}</span></td>
            <td style="padding: 8px 0; vertical-align: top;"><span style="color: {TEXT}; font-size: 14px; font-weight: 500;">{value}</span></td>
        </tr>'''

    def details_table(rows: list) -> str:
        html = ''.join(detail_row(label, value) for label, value in rows if value)
        if not html:
            return ''
        return f'''<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background: {BG}; border-radius: 8px; margin: 16px 0;">
            <tr><td style="padding: 12px 16px;"><table role="presentation" width="100%" cellspacing="0" cellpadding="0">{html}</table></td></tr>
        </table>'''

    def highlight_card(accent: str, title_text: str, items: list) -> str:
        """A colored highlight card with a title and key-value items"""
        item_html = ''
        for label, value in items:
            if value:
                item_html += f'''<td style="padding-right: 20px;">
                    <p style="margin: 0; color: rgba(255,255,255,0.7); font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px;">{label}</p>
                    <p style="margin: 3px 0 0 0; color: #ffffff; font-size: 15px; font-weight: 500;">{value}</p>
                </td>'''
        return f'''<div style="background: {accent}; border-radius: 10px; padding: 20px; margin: 16px 0;">
            <p style="margin: 0 0 12px 0; color: #ffffff; font-size: 17px; font-weight: 600;">{title_text}</p>
            <table role="presentation" cellspacing="0" cellpadding="0"><tr>{item_html}</tr></table>
        </div>'''

    def section_card(title_text: str, color: str, bg_color: str, content_html: str) -> str:
        """A subtle section card for announcements / prayer points"""
        return f'''<div style="background: {bg_color}; border-radius: 8px; padding: 16px; margin: 12px 0; border-left: 3px solid {color};">
            <p style="margin: 0 0 10px 0; font-size: 12px; font-weight: 700; color: {color}; text-transform: uppercase; letter-spacing: 0.05em;">{title_text}</p>
            {content_html}
        </div>'''

    def badge_list(items: list) -> str:
        return ''.join(
            f'<span style="display: inline-block; background: rgba(255,255,255,0.2); border-radius: 6px; padding: 5px 12px; margin: 0 6px 6px 0; color: #ffffff; font-size: 13px; font-weight: 500;">{item}</span>'
            for item in items
        )

    # ── Build portal / admin links ──
    portal_link = None
    if data.get('app_url') and data.get('recipient_phone'):
        portal_link = f"{data.get('app_url')}/portal?phone={data.get('recipient_phone')}"
    admin_link = f"{data.get('app_url')}/admin" if data.get('app_url') else None

    # ── Templates ──
    templates = {

        EventType.MEMBER_APPROVED: base_template(
            title="Selection Approved",
            accent_color="#10b981",
            content=f'''
                {heading("Selection Approved")}
                {greeting(data.get('recipient_name') or data.get('member_name', 'Member'))}
                {paragraph(f"Your selection for <strong>{data.get('department_name', 'the department')}</strong> has been approved.")}
                {details_table([
                    ("Department", data.get('department_name')),
                    ("Category", data.get('category_name'))
                ])}
                {paragraph("You can view your approved departments in the member portal.")}
            ''',
            button_text="View My Departments" if portal_link else None,
            button_url=portal_link
        ),

        EventType.MEMBER_REJECTED: base_template(
            title="Selection Update",
            accent_color="#64748b",
            content=f'''
                {heading("Selection Update")}
                {greeting(data.get('recipient_name') or data.get('member_name', 'Member'))}
                {paragraph(f"Your selection for <strong>{data.get('department_name', 'the department')}</strong> was not approved at this time.")}
                {details_table([
                    ("Department", data.get('department_name')),
                    ("Feedback", data.get('admin_note'))
                ])}
                {paragraph("If you have questions or would like to explore other opportunities to serve, please reach out to a church leader.")}
            ''',
            button_text="View My Portal" if portal_link else None,
            button_url=portal_link
        ),

        EventType.DEPARTMENT_ASSIGNED: base_template(
            title="Department Assignment",
            accent_color="#8b5cf6",
            content=f'''
                {heading("You've Been Assigned")}
                {greeting(data.get('recipient_name') or data.get('member_name', 'Member'))}
                {paragraph(f"You have been assigned to <strong>{data.get('department_name', 'a department')}</strong> by our leadership team.")}
                {details_table([
                    ("Department", data.get('department_name')),
                    ("Category", data.get('category_name')),
                    ("Note", data.get('admin_note'))
                ])}
            ''',
            button_text="View Assignment" if portal_link else None,
            button_url=portal_link
        ),

        EventType.RESULTS_PUBLISHED: base_template(
            title="Results Published",
            accent_color="#4f46e5",
            content=f'''
                {heading("Results Are Live")}
                {greeting(data.get('recipient_name', 'Member'))}
                {paragraph(f"The department selection results for <strong>{data.get('year', '2026')}</strong> are now available. View your approved assignments below.")}
            ''',
            button_text="View My Results" if portal_link else None,
            button_url=portal_link
        ),

        EventType.APPEAL_SUBMITTED: base_template(
            title="New Appeal",
            accent_color="#f59e0b",
            content=f'''
                {heading("New Appeal Received")}
                {paragraph("A new appeal has been submitted and requires your review.")}
                {details_table([
                    ("Member", data.get('member_name')),
                    ("Phone", data.get('member_phone')),
                    ("Current Dept", data.get('unwanted_department')),
                    ("Requested Dept", data.get('wanted_department')),
                    ("Reason", data.get('reason'))
                ])}
            ''',
            button_text="Review Appeal" if admin_link else None,
            button_url=admin_link
        ),

        EventType.APPEAL_RESOLVED: base_template(
            title="Appeal Resolved",
            accent_color="#10b981" if data.get('status') == 'approved' else "#64748b",
            content=f'''
                {heading("Appeal " + (data.get('status', 'processed')).title())}
                {greeting(data.get('recipient_name') or data.get('member_name', 'Member'))}
                {paragraph(f"Your appeal has been reviewed and <strong>{data.get('status', 'processed')}</strong>.")}
                {details_table([("Response", data.get('admin_response'))]) if data.get('admin_response') else ''}
            ''',
            button_text="View Updates" if portal_link else None,
            button_url=portal_link
        ),

        EventType.MEETING_CREATED: base_template(
            title="Meeting Invite",
            accent_color="#4f46e5",
            content=f'''
                {heading("Meeting Invite")}
                {greeting(data.get('recipient_name', 'Member'))}
                {paragraph("You're invited to a meeting. Please RSVP to confirm your attendance.")}
                {highlight_card('#4f46e5', data.get('title', 'Meeting'), [
                    ("Date", data.get('meeting_date', 'TBD')),
                    ("Time", f"{data.get('start_time', '')} – {data.get('end_time', '')}")
                ])}
                {details_table([
                    ("Location", data.get('location')),
                    ("Department", data.get('department_name')),
                    ("Details", data.get('description'))
                ])}
            ''',
            button_text="RSVP Now" if data.get('rsvp_link') else ("Join Meeting" if data.get('meeting_link') else None),
            button_url=data.get('rsvp_link') or data.get('meeting_link')
        ),

        EventType.MEETING_REMINDER: base_template(
            title="Meeting Today",
            accent_color="#10b981",
            content=f'''
                {heading("Meeting Reminder")}
                {greeting(data.get('recipient_name', 'Member'))}
                {paragraph("Friendly reminder — you have a meeting today.")}
                {highlight_card('#10b981', data.get('title', 'Meeting'), [
                    ("Time", f"{data.get('start_time', '')} – {data.get('end_time', '')}"),
                    ("Location", data.get('location', 'TBD'))
                ])}
            ''',
            button_text="View in Portal" if portal_link else ("Join Meeting" if data.get('meeting_link') else None),
            button_url=portal_link or data.get('meeting_link')
        ),

        EventType.MEETING_UPDATED: base_template(
            title="Meeting Updated",
            accent_color="#f59e0b",
            content=f'''
                {heading("Meeting Updated")}
                {paragraph("A meeting has been updated. Please note the new details below.")}
                {highlight_card('#f59e0b', data.get('title', 'Meeting'), [
                    ("New Date", data.get('meeting_date', 'TBD')),
                    ("New Time", f"{data.get('start_time', '')} – {data.get('end_time', '')}")
                ])}
                {details_table([("Location", data.get('location'))]) if data.get('location') else ''}
            '''
        ),

        EventType.MEETING_CANCELLED: base_template(
            title="Meeting Cancelled",
            accent_color="#ef4444",
            content=f'''
                {heading("Meeting Cancelled")}
                {paragraph("The following meeting has been cancelled.")}
                <div style="background: #fef2f2; border-radius: 8px; padding: 16px; margin: 16px 0; border: 1px solid #fecaca;">
                    <p style="margin: 0 0 8px 0; color: #991b1b; font-size: 16px; font-weight: 600; text-decoration: line-through;">{data.get('title', 'Meeting')}</p>
                    <p style="margin: 0; color: #b91c1c; font-size: 14px;">{data.get('meeting_date', '')} &middot; {data.get('start_time', '')} – {data.get('end_time', '')}</p>
                </div>
                {paragraph("Please disregard any previous meeting invitations.")}
            '''
        ),

        EventType.POSTER_REQUEST_SUBMITTED: base_template(
            title="New Poster Request",
            accent_color="#9333ea",
            content=f'''
                {heading("New Poster Request")}
                {paragraph("A new poster request has been submitted.")}
                {highlight_card('#9333ea', data.get('event_name', 'Event'), [
                    ("Event Date", data.get('event_date', 'TBD')),
                    ("Time", data.get('event_time', ''))
                ])}
                {details_table([
                    ("Requested By", data.get('requester_name')),
                    ("Email", data.get('requester_email')),
                    ("Ministry", data.get('ministry_department')),
                    ("Venue", data.get('venue_platform')),
                    ("Purpose", data.get('purpose')),
                    ("Formats", data.get('output_formats_display')),
                    ("Speakers", data.get('speakers_display')),
                    ("Theme", data.get('theme_tagline')),
                    ("Scripture", data.get('scripture')),
                    ("Audience", data.get('target_audience')),
                    ("Notes", data.get('additional_notes'))
                ])}
            ''',
            button_text="View Request" if portal_link else None,
            button_url=portal_link
        ),

        EventType.POSTER_REQUEST_ACKNOWLEDGED: base_template(
            title="Poster Request Acknowledged",
            accent_color="#10b981",
            content=f'''
                {heading("Request Acknowledged")}
                {greeting(data.get('recipient_name', 'Member'))}
                {paragraph(f"Your poster request for <strong>{data.get('event_name', 'your event')}</strong> has been acknowledged and is being worked on.")}
                {details_table([
                    ("Event", data.get('event_name')),
                    ("Event Date", data.get('event_date')),
                    ("Acknowledged By", data.get('acknowledged_by_name'))
                ])}
            ''',
            button_text="View My Requests" if portal_link else None,
            button_url=portal_link
        ),

        EventType.POSTER_REQUEST_COMPLETED: base_template(
            title="Poster Ready",
            accent_color="#10b981",
            content=f'''
                {heading("Your Poster is Ready")}
                {greeting(data.get('recipient_name', 'Member'))}
                {paragraph(f"Your poster for <strong>{data.get('event_name', 'your event')}</strong> is now complete.")}
                {details_table([
                    ("Event", data.get('event_name')),
                    ("Event Date", data.get('event_date')),
                    ("Completed By", data.get('completed_by_name'))
                ])}
                <div style="background: #f0fdf4; border-radius: 8px; padding: 14px 16px; margin: 12px 0; border: 1px solid #bbf7d0;">
                    <p style="margin: 0; color: #166534; font-size: 14px; font-weight: 500;">Your poster has been shared in the WhatsApp group. Please check the group to download your design.</p>
                </div>
            ''',
            button_text="View My Requests" if portal_link else None,
            button_url=portal_link
        ),

        EventType.PROGRAM_PARTICIPANT_ADDED: base_template(
            title="You're on the Program",
            accent_color="#4f46e5",
            content=f'''
                {heading(data.get('title', 'Service Program'))}
                {greeting(data.get('recipient_name', 'Member'))}
                {paragraph(f"You have been added to <strong>{data.get('title', 'the service program')}</strong> on <strong>{data.get('service_date', 'TBD')}</strong>{(' by <strong>' + data.get('created_by') + '</strong>') if data.get('created_by') and data.get('created_by') != 'Admin' else ''}.")}

                <div style="background: #4f46e5; border-radius: 10px; padding: 20px; margin: 16px 0;">
                    <table role="presentation" cellspacing="0" cellpadding="0" style="margin-bottom: 14px;">
                        <tr>
                            <td style="padding-right: 24px;">
                                <p style="margin: 0; color: rgba(255,255,255,0.7); font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px;">Date</p>
                                <p style="margin: 3px 0 0 0; color: #ffffff; font-size: 15px; font-weight: 500;">{data.get('service_date', 'TBD')}</p>
                            </td>
                            {f"""<td>
                                <p style="margin: 0; color: rgba(255,255,255,0.7); font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px;">Service Manager</p>
                                <p style="margin: 3px 0 0 0; color: #ffffff; font-size: 15px; font-weight: 500;">{data.get('created_by', '')}</p>
                            </td>""" if data.get('created_by') and data.get('created_by') != 'Admin' else ''}
                        </tr>
                    </table>
                    <p style="margin: 0 0 8px 0; color: rgba(255,255,255,0.7); font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px;">{"Your Roles" if len(data.get('roles', [])) > 1 else "Your Role"}</p>
                    {badge_list(data.get('roles', [data.get('role', 'Participant')]))}
                </div>

                {section_card("Admin Announcements", "#2563eb", "#f0f9ff",
                    '<ul style="margin: 0; padding: 0 0 0 18px; color: #1e293b;">' +
                    ''.join(f'<li style="margin-bottom: 6px; font-size: 14px; line-height: 1.5;">{a}</li>' for a in data.get('admin_announcements', [])) +
                    '</ul>'
                ) if data.get('admin_announcements') else ''}

                {section_card("Pastor's Announcements", "#7c3aed", "#faf5ff",
                    '<ul style="margin: 0; padding: 0 0 0 18px; color: #1e293b;">' +
                    ''.join(f'<li style="margin-bottom: 6px; font-size: 14px; line-height: 1.5;">{a}</li>' for a in data.get('pastors_announcements', [])) +
                    '</ul>'
                ) if data.get('pastors_announcements') else ''}

                {section_card("Prayer Points", "#e11d48", "#fef2f2",
                    '<ul style="margin: 0; padding: 0 0 0 18px; color: #1e293b;">' +
                    _format_prayer_points_html(data.get('prayer_points', [])) +
                    '</ul>'
                ) if data.get('prayer_points') else ''}

                {paragraph("Please be prepared and arrive on time. If you're unable to attend, inform the service coordinator as soon as possible.")}
            ''',
            button_text="View Program" if data.get('app_url') and data.get('program_id') else None,
            button_url=f"{data.get('app_url')}/program/{data.get('program_id')}?phone={data.get('recipient_phone', '')}" if data.get('app_url') and data.get('program_id') else None
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
    if recipients is None:
        if 'recipients' in data:
            recipients = data['recipients']
        else:
            recipients = [{
                'id': data.get('member_id'),
                'email': data.get('member_email'),
                'phone': data.get('member_phone')
            }]

    settings = get_email_settings(db)

    # Check for app URL to include in templates
    app_url = os.getenv('RAILWAY_PUBLIC_DOMAIN')
    if app_url and not app_url.startswith('http'):
        app_url = f"https://{app_url}"
    if not app_url:
        app_url = os.getenv('APP_URL', '')

    # Determine active email channel
    is_resend = (
        os.getenv('RESEND_ENABLED', '').lower() == 'true' or
        settings.get('resend_enabled') == 'true'
    )
    is_smtp = (
        os.getenv('SMTP_ENABLED', '').lower() == 'true' or
        settings.get('smtp_enabled') == 'true'
    )

    if not is_resend and not is_smtp:
        return  # No email channel enabled

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
        html_content = render_email_template(event_type, recipient_data)
        subject = get_email_subject(event_type, recipient_data)

        try:
            if is_resend:
                from notifications.channels.resend import send_email as send_resend
                from_name = os.getenv('RESEND_FROM_NAME') or settings.get('resend_from_name', 'RFM Stellenbosch')
                from_email = os.getenv('RESEND_FROM_EMAIL') or settings.get('resend_from_email', '')

                if from_email:
                    send_resend(
                        api_key=os.getenv('RESEND_API_KEY') or settings.get('resend_api_key', ''),
                        from_email=f"{from_name} <{from_email}>",
                        to_email=recipient_email,
                        subject=subject,
                        html_content=html_content
                    )
                    log_notification(db, event_type, 'resend',
                                   recipient_id=recipient.get('id'),
                                   recipient_email=recipient_email)
            elif is_smtp:
                from notifications.channels.email import send_email as send_smtp
                send_smtp(
                    host=os.getenv('SMTP_HOST') or settings.get('smtp_host', ''),
                    port=int(os.getenv('SMTP_PORT') or settings.get('smtp_port', '587')),
                    username=os.getenv('SMTP_USERNAME') or settings.get('smtp_username', ''),
                    password=os.getenv('SMTP_PASSWORD') or settings.get('smtp_password', ''),
                    from_name=os.getenv('SMTP_FROM_NAME') or settings.get('smtp_from_name', 'RFM Stellenbosch'),
                    from_email=os.getenv('SMTP_FROM_EMAIL') or settings.get('smtp_from_email', ''),
                    to_email=recipient_email,
                    subject=subject,
                    html_content=html_content
                )
                log_notification(db, event_type, 'smtp',
                               recipient_id=recipient.get('id'),
                               recipient_email=recipient_email)

        except Exception as e:
            print(f"Failed to send {event_type.value} to {recipient_email}: {e}")
            log_notification(db, event_type, 'resend' if is_resend else 'smtp',
                           recipient_id=recipient.get('id'),
                           recipient_email=recipient_email,
                           status='failed',
                           error_message=str(e))


def dispatch_admin_event(db: Session, event_type: EventType, data: Dict[str, Any]):
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
