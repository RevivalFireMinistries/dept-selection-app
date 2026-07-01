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
    """Format prayer points as HTML paragraphs, showing linked activity if present."""
    items = []
    for pp in prayer_points:
        if isinstance(pp, dict):
            text = pp.get("text", "")
            linked = pp.get("linked_activity", "")
            if text:
                linked_html = f' <span style="font-size: 12px; color: #9ca3af; font-style: italic;">({linked})</span>' if linked else ""
                items.append(f'<p style="margin: 0 0 6px 0; color: #111827; font-size: 14px; line-height: 1.5; padding-left: 12px; border-left: 2px solid #f3f4f6;">{text}{linked_html}</p>')
        elif pp:
            items.append(f'<p style="margin: 0 0 6px 0; color: #111827; font-size: 14px; line-height: 1.5; padding-left: 12px; border-left: 2px solid #f3f4f6;">{pp}</p>')
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
    """Generate clean, minimal email templates matching portal design"""

    # ── Design System — white, minimal, portal-matching ──
    FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
    BG = "#ffffff"
    CARD_BG = "#ffffff"
    TEXT = "#111827"
    TEXT_SECONDARY = "#6b7280"
    TEXT_MUTED = "#9ca3af"
    BORDER = "#f3f4f6"
    ACCENT = "#4f46e5"
    CHURCH_NAME = "Revival Fire Ministries"

    def base_template(title: str, accent_color: str, content: str, button_text: str = None, button_url: str = None):
        button_html = ""
        if button_text and button_url:
            button_html = f'''
                <tr><td style="padding: 20px 0 0 0;">
                    <a href="{button_url}" style="display: inline-block; background: {accent_color}; color: #ffffff; font-family: {FONT}; font-size: 14px; font-weight: 600; text-decoration: none; padding: 10px 24px; border-radius: 10px;">{button_text}</a>
                </td></tr>
            '''

        return f'''<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>{title}</title></head>
<body style="margin: 0; padding: 0; background-color: {BG}; font-family: {FONT};">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background-color: {BG};">
<tr><td align="center" style="padding: 32px 16px;">

<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width: 520px;">
  <tr><td style="padding: 0 0 28px 0;">
    {content}
    {button_html}
  </td></tr>
  <tr><td style="border-top: 1px solid {BORDER}; padding: 16px 0 0 0;">
    <p style="margin: 0 0 6px 0; color: {TEXT}; font-size: 13px; font-weight: 600;">{CHURCH_NAME}</p>
    <p style="margin: 0 0 6px 0; color: {TEXT_MUTED}; font-size: 12px; line-height: 1.5;">South Africa</p>
    <p style="margin: 0; color: {TEXT_MUTED}; font-size: 11px; line-height: 1.5;">
      You're receiving this because you're a member of Revival Fire Ministries.
      To stop receiving these emails, reply with "unsubscribe" in the subject line.
    </p>
  </td></tr>
</table>

</td></tr>
</table>
</body></html>'''

    def heading(text: str) -> str:
        return f'<h2 style="margin: 0 0 4px 0; color: {TEXT}; font-size: 18px; font-weight: 700;">{text}</h2>'

    def greeting(name: str) -> str:
        return f'<p style="margin: 0 0 16px 0; color: {TEXT}; font-size: 15px; line-height: 1.6;">Hi <strong>{name}</strong>,</p>'

    def paragraph(text: str) -> str:
        return f'<p style="margin: 0 0 14px 0; color: {TEXT_SECONDARY}; font-size: 14px; line-height: 1.6;">{text}</p>'

    def detail_row(label: str, value: str) -> str:
        if not value:
            return ''
        return f'''<tr>
            <td style="padding: 6px 0; vertical-align: top; width: 110px;"><span style="color: {TEXT_MUTED}; font-size: 12px; text-transform: uppercase; letter-spacing: 0.3px;">{label}</span></td>
            <td style="padding: 6px 0; vertical-align: top;"><span style="color: {TEXT}; font-size: 14px; font-weight: 500;">{value}</span></td>
        </tr>'''

    def details_table(rows: list) -> str:
        html = ''.join(detail_row(label, value) for label, value in rows if value)
        if not html:
            return ''
        return f'''<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin: 14px 0;">
            <tr><td style="padding: 0;"><table role="presentation" width="100%" cellspacing="0" cellpadding="0">{html}</table></td></tr>
        </table>'''

    def section_heading(text: str) -> str:
        return f'<p style="margin: 20px 0 8px 0; font-size: 11px; font-weight: 600; color: {TEXT_MUTED}; text-transform: uppercase; letter-spacing: 0.5px;">{text}</p>'

    def divider() -> str:
        return f'<hr style="border: none; border-top: 1px solid {BORDER}; margin: 16px 0;">'

    def role_tags(items: list) -> str:
        return ''.join(
            f'<span style="display: inline-block; border: 1px solid {BORDER}; border-radius: 6px; padding: 4px 10px; margin: 0 6px 6px 0; color: {TEXT}; font-size: 13px; font-weight: 500;">{item}</span>'
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
                {details_table([
                    ("Meeting", data.get('title', 'Meeting')),
                    ("Date", data.get('meeting_date', 'TBD')),
                    ("Time", f"{data.get('start_time', '')} – {data.get('end_time', '')}"),
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
                {details_table([
                    ("Meeting", data.get('title', 'Meeting')),
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
                {details_table([
                    ("Meeting", data.get('title', 'Meeting')),
                    ("New Date", data.get('meeting_date', 'TBD')),
                    ("New Time", f"{data.get('start_time', '')} – {data.get('end_time', '')}"),
                    ("Location", data.get('location'))
                ])}
            '''
        ),

        EventType.MEETING_CANCELLED: base_template(
            title="Meeting Cancelled",
            accent_color="#ef4444",
            content=f'''
                {heading("Meeting Cancelled")}
                {paragraph("The following meeting has been cancelled.")}
                <p style="margin: 0 0 4px 0; color: {TEXT}; font-size: 15px; font-weight: 600; text-decoration: line-through;">{data.get('title', 'Meeting')}</p>
                <p style="margin: 0 0 16px 0; color: {TEXT_MUTED}; font-size: 14px;">{data.get('meeting_date', '')} &middot; {data.get('start_time', '')} – {data.get('end_time', '')}</p>
                {paragraph("Please disregard any previous meeting invitations.")}
            '''
        ),

        EventType.POSTER_REQUEST_SUBMITTED: base_template(
            title="New Poster Request",
            accent_color="#9333ea",
            content=f'''
                {heading("New Poster Request")}
                {paragraph("A new poster request has been submitted.")}
                {details_table([
                    ("Event", data.get('event_name', 'Event')),
                    ("Event Date", data.get('event_date', 'TBD')),
                    ("Time", data.get('event_time', '')),
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
            accent_color=ACCENT,
            content=f'''
                {heading(data.get('title', 'Service Program'))}
                {paragraph(data.get('service_date', ''))}
                {greeting(data.get('recipient_name', 'Member'))}
                {paragraph(f"You've been added to the program{(' by ' + data.get('created_by')) if data.get('created_by') and data.get('created_by') != 'Admin' else ''}.")}

                {section_heading("Your " + ("Roles" if len(data.get('roles', [])) > 1 else "Role"))}
                {role_tags(data.get('roles', [data.get('role', 'Participant')]))}

                {(divider() + section_heading("Admin Announcements") +
                    ''.join(f'<p style="margin: 0 0 6px 0; color: {TEXT}; font-size: 14px; line-height: 1.5; padding-left: 12px; border-left: 2px solid {BORDER};">{a}</p>' for a in data.get('admin_announcements', []))
                ) if data.get('admin_announcements') else ''}

                {(divider() + section_heading("Pastor's Announcements") +
                    ''.join(f'<p style="margin: 0 0 6px 0; color: {TEXT}; font-size: 14px; line-height: 1.5; padding-left: 12px; border-left: 2px solid {BORDER};">{a}</p>' for a in data.get('pastors_announcements', []))
                ) if data.get('pastors_announcements') else ''}

                {(divider() + section_heading("Prayer Points") +
                    _format_prayer_points_html(data.get('prayer_points', []))
                ) if data.get('prayer_points') else ''}

                {divider()}
                {paragraph("Please be prepared and arrive on time. If you're unable to attend, inform the service manager as soon as possible.")}
            ''',
            button_text="View Program" if data.get('app_url') and data.get('program_id') else None,
            button_url=f"{data.get('app_url')}/program/{data.get('program_id')}?phone={data.get('recipient_phone', '')}" if data.get('app_url') and data.get('program_id') else None
        ),

        EventType.HOME_CHURCH_ROSTER_PUBLISHED: base_template(
            title=f"{data.get('program_type_icon', '')} {data.get('program_type_name', 'Home Church')}",
            accent_color="#7c3aed",
            content=f'''
                {heading(data.get('home_church_name', 'Your Home Church'))}
                {paragraph(f"Week of {data.get('roster_date', '')} · {data.get('meeting_time', '19:00')}")}
                {greeting(data.get('leader_name', 'Leader'))}
                {paragraph(f"This week's programme at your home church: <strong>{data.get('program_type_name', '')}</strong>.")}
                {(section_heading("Your preacher this week") +
                    paragraph(f"<strong>{data.get('preacher_name', '')}</strong>" +
                        (f" · {data.get('preacher_phone', '')}" if data.get('preacher_phone') else ''))
                ) if data.get('requires_preacher') and data.get('preacher_name') else paragraph("No preacher this week — it's your programme to lead.")}
                {divider()}
                {paragraph("You'll get a reminder the evening before. Check the portal for the full schedule ahead.")}
            ''',
            button_text="Open Portal" if data.get('app_url') else None,
            button_url=f"{data.get('app_url')}/portal?phone={data.get('recipient_phone', '')}" if data.get('app_url') else None
        ),

        EventType.HOME_CHURCH_PREACHER_ASSIGNED: base_template(
            title="You're preaching this week",
            accent_color="#2563eb",
            content=f'''
                {heading(data.get('home_church_name', 'Home Church'))}
                {paragraph(f"{data.get('roster_date', '')} · {data.get('meeting_time', '19:00')}")}
                {greeting(data.get('preacher_name', 'Preacher'))}
                {paragraph(f"You have been assigned to preach at <strong>{data.get('home_church_name', '')}</strong>.")}
                {(section_heading("Host Leader") +
                    paragraph(f"<strong>{data.get('leader_name', '')}</strong>" +
                        (f" · {data.get('leader_phone', '')}" if data.get('leader_phone') else ''))
                ) if data.get('leader_name') else ''}
                {(section_heading("Address") + paragraph(data.get('home_church_address', ''))) if data.get('home_church_address') else ''}
                {divider()}
                {paragraph("Please confirm with the leader and arrive 10 minutes early. A reminder will be sent the evening before.")}
            ''',
            button_text="Open Portal" if data.get('app_url') else None,
            button_url=f"{data.get('app_url')}/portal?phone={data.get('recipient_phone', '')}" if data.get('app_url') else None
        ),

        EventType.HOME_CHURCH_REMINDER_LEADER: base_template(
            title="Tomorrow at your home church",
            accent_color="#7c3aed",
            content=f'''
                {heading(data.get('home_church_name', 'Your Home Church'))}
                {paragraph(f"Tomorrow · {data.get('roster_date', '')} · {data.get('meeting_time', '19:00')}")}
                {greeting(data.get('leader_name', 'Leader'))}
                {paragraph(f"<strong>{data.get('program_type_icon', '')} {data.get('program_type_name', '')}</strong>")}
                {(section_heading("Preacher") +
                    paragraph(f"<strong>{data.get('preacher_name', '')}</strong>" +
                        (f" · {data.get('preacher_phone', '')}" if data.get('preacher_phone') else ''))
                ) if data.get('requires_preacher') and data.get('preacher_name') else paragraph("No preacher this week — it's your programme to lead.")}
                {divider()}
                {paragraph("Everything is ready for tomorrow. Let us know if anything needs to change.")}
            ''',
        ),

        EventType.HOME_CHURCH_REMINDER_PREACHER: base_template(
            title="You're preaching tomorrow",
            accent_color="#2563eb",
            content=f'''
                {heading(data.get('home_church_name', 'Home Church'))}
                {paragraph(f"Tomorrow · {data.get('roster_date', '')} · {data.get('meeting_time', '19:00')}")}
                {greeting(data.get('preacher_name', 'Preacher'))}
                {paragraph(f"Reminder: you're preaching tomorrow at <strong>{data.get('home_church_name', '')}</strong>.")}
                {(section_heading("Host Leader") +
                    paragraph(f"<strong>{data.get('leader_name', '')}</strong>" +
                        (f" · {data.get('leader_phone', '')}" if data.get('leader_phone') else ''))
                ) if data.get('leader_name') else ''}
                {(section_heading("Address") + paragraph(data.get('home_church_address', ''))) if data.get('home_church_address') else ''}
                {divider()}
                {paragraph("Arrive 10 minutes early. Travel safely.")}
            ''',
        ),

        EventType.HOME_CHURCH_ATTENDANCE_REMINDER: base_template(
            title="Reports pending",
            accent_color="#d97706",
            content=f'''
                {heading("Home Church reports pending")}
                {paragraph(f"Monday {data.get('roster_date', '')}")}
                {greeting(data.get('recipient_name', 'Committee member'))}
                {paragraph(f"<strong>{data.get('pending_count', 0)} home church(es)</strong> haven't had their Monday attendance captured yet. Please follow up with the leaders via WhatsApp and capture the numbers on the portal.")}
                {section_heading("Still pending")}
                {''.join(f'<p style="margin:0 0 6px 0;color:#111827;font-size:14px;line-height:1.5;padding-left:12px;border-left:2px solid #fcd34d;"><strong>{hc.get("name", "")}</strong>' + (f' · leader: {hc.get("leader_name", "")} ({hc.get("leader_phone", "")})' if hc.get("leader_name") else '') + '</p>' for hc in data.get('pending_list', []))}
                {divider()}
                {paragraph("Reminder: leaders send their attendance + offering via WhatsApp after Monday's meeting. If you've already received the numbers, please capture them so we can keep the roster reports up to date.")}
            ''',
            button_text="Capture now" if data.get('app_url') else None,
            button_url=f"{data.get('app_url')}/admin/home-churches?phone={data.get('recipient_phone', '')}" if data.get('app_url') else None
        ),

        EventType.PRAYER_CHAIN_SCHEDULE: base_template(
            title="Prayer Chain Schedule",
            accent_color="#B8541C",
            content=f'''
                {heading(data.get('group_name', 'Your Prayer Group'))}
                {paragraph(f"🕊️ {data.get('title') or 'Chain Prayer'}" + (f" · {data.get('date_display')}" if data.get('date_display') else '') + (f" · {data.get('label')}" if data.get('label') else ''))}
                {greeting(data.get('recipient_name', 'Leader'))}
                {paragraph(f"You're leading <strong>{data.get('group_name', 'your group')}</strong> on the prayer chain. Here are your group's prayer slots — please share these times with your group and make sure each one is covered:")}
                {section_heading("Your group's prayer slots")}
                {''.join(f'<div style="margin:0 0 10px 0;padding-left:12px;border-left:3px solid #B8541C;"><p style="margin:0;color:{TEXT};font-size:16px;font-weight:600;line-height:1.4;">{s.get("start","")} – {s.get("end","")}</p>' + (f'<p style="margin:2px 0 0 0;color:{TEXT_SECONDARY};font-size:13px;line-height:1.4;">🙏 {s.get("prayer_point")}</p>' if s.get("prayer_point") else '') + '</div>' for s in data.get('slots', [])) or paragraph("No specific slots have been set yet.")}
                {divider()}
                {paragraph("Let's keep the chain unbroken. Rally your group, confirm who covers each slot, and lead them in prayer at the set times.")}
            ''',
            button_text="Open Portal" if data.get('app_url') else None,
            button_url=f"{data.get('app_url')}/portal?phone={data.get('recipient_phone', '')}" if data.get('app_url') else None
        ),

        EventType.PRAYER_REQUEST_SUBMITTED: base_template(
            title="New Prayer Request",
            accent_color="#7c3aed",
            content=f'''
                {heading("New prayer request" + (f" ({data.get('count')})" if (data.get('count') or 0) > 1 else ""))}
                {paragraph(f"From: <strong>{data.get('submitter', 'A member')}</strong>")}
                {greeting(data.get('recipient_name', 'Team'))}
                {paragraph("You've been assigned to receive prayer requests. Please pray and acknowledge receipt on the portal.")}
                {section_heading("Request" + ("s" if (data.get('count') or 0) > 1 else ""))}
                {''.join(f'<p style="margin:0 0 8px 0;color:{TEXT};font-size:14px;line-height:1.5;padding-left:12px;border-left:3px solid #7c3aed;">{t}</p>' for t in data.get('requests', []))}
                {divider()}
                {paragraph("Open the portal to acknowledge you've received and are praying for this.")}
            ''',
            button_text="Open Prayer Requests" if data.get('app_url') else None,
            button_url=f"{data.get('app_url')}/portal/prayer-requests" if data.get('app_url') else None
        ),

        EventType.PRAYER_REQUEST_REMINDER: base_template(
            title="Prayer Requests Awaiting You",
            accent_color="#d97706",
            content=f'''
                {heading(f"{data.get('count', 0)} request(s) still awaiting acknowledgment")}
                {paragraph(f"These have been waiting {data.get('days', 3)}+ days without being marked received.")}
                {greeting(data.get('recipient_name', 'Team'))}
                {paragraph("Please open the portal, mark them as received, and pray. A quick acknowledgment lets us know they're in hand.")}
                {section_heading("Waiting")}
                {''.join(f'<p style="margin:0 0 8px 0;color:{TEXT};font-size:14px;line-height:1.5;padding-left:12px;border-left:3px solid #d97706;">{t}</p>' for t in data.get('requests', []))}
                {divider()}
                {paragraph("Thank you for standing in the gap for our people.")}
            ''',
            button_text="Open Prayer Requests" if data.get('app_url') else None,
            button_url=f"{data.get('app_url')}/portal/prayer-requests" if data.get('app_url') else None
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

    # ---- Web Push via rfm-notify (best-effort, non-blocking) ----
    # Push lives in rfm-notify now: VAPID keys, browser subscriptions, and
    # the actual webpush call. We loop the same recipient list and fire
    # one /notify call per recipient with channels=["push"] and a JSON
    # body_override carrying the title/body/url the SW will render.
    # Failures here don't propagate — push is best-effort, email above
    # is the authoritative delivery path.
    try:
        from notifications import rfm_notify_push as _push_bridge
        from notifications.push_payload import build_push_payload

        if _push_bridge.is_configured():
            push_title, push_body, push_url = build_push_payload(
                event_type, {**data, "app_url": app_url}
            )
            for _r in recipients:
                _email = _r.get("email")
                if not _email:
                    continue  # rfm-notify needs email to resolve the recipient
                _push_idem = f"portal-push:{event_type.value}:{_r.get('id') or _email}"
                _push_bridge.send_event_push(
                    event_code=event_type.value,
                    recipient_email=_email,
                    recipient_member_id=_r.get("id"),
                    recipient_full_name=_r.get("name"),
                    title=push_title,
                    body=push_body,
                    url=push_url,
                    tag=f"event-{event_type.value}",
                    idempotency_key=_push_idem,
                )
    except Exception as _push_err:
        try:
            print(f"Push fan-out failed for {event_type.value}: {_push_err}")
        except Exception:
            pass

    # ---- Email via rfm-notify (post v1.2 migration) ----
    # We render the HTML locally as before (preserves all the existing
    # event-specific layouts) but ship it through rfm-notify instead of
    # calling Resend/SMTP directly. Benefits: centralised logging, the
    # opt-out + quiet-hours pipeline, and an auto-injected unsubscribe
    # footer on every email.
    from notifications.channels.rfm_notify import RfmNotifyChannel

    notify_channel = RfmNotifyChannel()
    if not notify_channel.is_configured():
        # Fail loudly but don't crash the request — caller (e.g. an admin
        # action) can still complete; emails just won't go out.
        print(
            "[dispatch_event] rfm-notify not configured "
            "(set RFM_NOTIFY_URL and RFM_NOTIFY_API_KEY). Skipping email send."
        )
        return

    for recipient in recipients:
        recipient_email = recipient.get('email')
        if not recipient_email:
            continue

        # Merge recipient data into template data for personalized emails
        recipient_data = {**data}
        recipient_data['recipient_name'] = recipient.get('name', '')
        recipient_data['recipient_phone'] = recipient.get('phone', '')
        recipient_data['recipient_email'] = recipient_email
        recipient_data['app_url'] = app_url

        # Build RSVP link if phone is available
        if recipient.get('phone') and app_url:
            recipient_data['rsvp_link'] = f"{app_url}/portal?phone={recipient.get('phone')}"

        # Render the portal's per-event HTML + subject.
        html_content = render_email_template(event_type, recipient_data)
        subject = get_email_subject(event_type, recipient_data)

        # Stable idempotency key per (event, recipient) so retries from a
        # failed scheduler tick don't double-send.
        idem_id = recipient.get('id') or recipient_email
        idempotency_key = f"portal:{event_type.value}:{idem_id}"

        try:
            success, error = notify_channel.send(
                recipient_email,
                subject,
                html_content,
                event_code=event_type.value,
                recipient_id=recipient.get('id'),
                recipient_name=recipient.get('name'),
                idempotency_key=idempotency_key,
            )
            log_notification(
                db, event_type, 'rfm-notify',
                recipient_id=recipient.get('id'),
                recipient_email=recipient_email,
                status='sent' if success else 'failed',
                error_message=None if success else (error or "rfm-notify failed"),
            )
        except Exception as e:
            print(f"Failed to send {event_type.value} to {recipient_email}: {e}")
            log_notification(
                db, event_type, 'rfm-notify',
                recipient_id=recipient.get('id'),
                recipient_email=recipient_email,
                status='failed',
                error_message=str(e),
            )


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
