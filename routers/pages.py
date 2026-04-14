from fastapi import APIRouter, Request, Depends, Form, Response
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from typing import Optional
import hmac, hashlib, os

from database import get_db
from models import Settings, Member

router = APIRouter()
templates = Jinja2Templates(directory="templates")

ADMIN_COOKIE_NAME = "admin_session"
DESK_COOKIE_NAME = "desk_session"
MEMBER_COOKIE_NAME = "member_session"
SESSION_SECRET = os.environ.get("SESSION_SECRET", "rfm-stellenbosch-portal-2026")
SESSION_MAX_AGE = 90 * 24 * 60 * 60  # 90 days


def is_authenticated(request: Request) -> bool:
    """Check if user has valid admin session cookie"""
    return request.cookies.get(ADMIN_COOKIE_NAME) == "authenticated"


def is_desk_authenticated(request: Request) -> bool:
    """Check if user has valid desk session cookie"""
    return request.cookies.get(DESK_COOKIE_NAME) == "authenticated"


def _sign_member_session(member_id: int) -> str:
    """Create a signed session token for a member"""
    msg = str(member_id).encode()
    sig = hmac.new(SESSION_SECRET.encode(), msg, hashlib.sha256).hexdigest()[:24]
    return f"{member_id}.{sig}"


def _verify_member_session(token: str) -> Optional[int]:
    """Verify a signed session token, return member_id or None"""
    if not token:
        return None
    try:
        parts = token.split(".")
        if len(parts) != 2:
            return None
        member_id = int(parts[0])
        expected = _sign_member_session(member_id)
        if hmac.compare_digest(token, expected):
            return member_id
    except (ValueError, IndexError):
        pass
    return None


def get_current_member(request: Request, db: Session) -> Optional[Member]:
    """Get the currently logged-in member from session cookie"""
    token = request.cookies.get(MEMBER_COOKIE_NAME)
    member_id = _verify_member_session(token)
    if member_id:
        member = db.query(Member).filter(Member.id == member_id, Member.is_active == True).first()
        return member
    return None


def set_member_session(response: Response, member_id: int):
    """Set the member session cookie on a response"""
    token = _sign_member_session(member_id)
    response.set_cookie(
        key=MEMBER_COOKIE_NAME,
        value=token,
        httponly=True,
        max_age=SESSION_MAX_AGE,
        samesite="lax"
    )


# ============ PUBLIC ROUTES ============

@router.get("/")
async def landing(request: Request, db: Session = Depends(get_db)):
    """Login page — redirect to portal if already logged in"""
    member = get_current_member(request, db)
    if member:
        return RedirectResponse(url=f"/portal?phone={member.phone}", status_code=302)
    return templates.TemplateResponse("landing.html", {"request": request})


@router.get("/register")
async def register_page(request: Request):
    """Registration page"""
    return templates.TemplateResponse("register.html", {"request": request})


@router.get("/forgot-password")
async def forgot_password_page(request: Request):
    """Forgot password page"""
    return templates.TemplateResponse("forgot_password.html", {"request": request})


@router.get("/reset-password")
async def reset_password_page(request: Request):
    """Reset password page (with token from email)"""
    return templates.TemplateResponse("reset_password.html", {"request": request})


@router.get("/logout")
async def member_logout():
    """Log out member"""
    response = RedirectResponse(url="/", status_code=302)
    response.delete_cookie(key=MEMBER_COOKIE_NAME)
    return response


@router.get("/new")
async def new_selection(request: Request):
    """New department selection form"""
    return templates.TemplateResponse("index.html", {"request": request})


@router.get("/update")
async def update_lookup(request: Request):
    """Update selection - phone lookup page"""
    return templates.TemplateResponse("update.html", {"request": request})


@router.get("/edit/{member_id}")
async def edit_selection(request: Request, member_id: int):
    """Edit existing selection form"""
    return templates.TemplateResponse("edit.html", {"request": request})


@router.get("/thank-you")
async def thank_you(request: Request):
    """Submission confirmation page"""
    return templates.TemplateResponse("thank_you.html", {"request": request})


# ============ ADMIN ROUTES ============

@router.get("/admin/login")
async def admin_login_page(request: Request):
    """Admin login page"""
    if is_authenticated(request):
        return RedirectResponse(url="/admin", status_code=302)
    return templates.TemplateResponse("admin/login.html", {"request": request, "error": None})


@router.post("/admin/login")
async def admin_login(
    request: Request,
    response: Response,
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    """Process admin login"""
    setting = db.query(Settings).filter(Settings.key == "adminPassword").first()
    correct_password = setting.value if setting else "admin123"

    if password == correct_password:
        response = RedirectResponse(url="/admin", status_code=302)
        response.set_cookie(key=ADMIN_COOKIE_NAME, value="authenticated", httponly=True)
        return response
    else:
        return templates.TemplateResponse(
            "admin/login.html",
            {"request": request, "error": "Invalid password"}
        )


@router.get("/admin/logout")
async def admin_logout():
    """Log out admin"""
    response = RedirectResponse(url="/admin/login", status_code=302)
    response.delete_cookie(key=ADMIN_COOKIE_NAME)
    return response


@router.get("/admin")
async def admin_dashboard(request: Request):
    """Admin dashboard"""
    if not is_authenticated(request):
        return RedirectResponse(url="/admin/login", status_code=302)
    return templates.TemplateResponse("admin/dashboard.html", {"request": request})


@router.get("/admin/department-stats")
async def admin_department_stats(request: Request):
    """Admin department stats - view all department totals"""
    if not is_authenticated(request):
        return RedirectResponse(url="/admin/login", status_code=302)
    return templates.TemplateResponse("admin/department_stats.html", {"request": request})


@router.get("/admin/department/{department_id}")
async def admin_department_detail(request: Request, department_id: int):
    """Admin department detail - view members"""
    if not is_authenticated(request):
        return RedirectResponse(url="/admin/login", status_code=302)
    return templates.TemplateResponse("admin/department_detail.html", {"request": request})


@router.get("/admin/submissions")
async def admin_submissions(request: Request):
    """Admin submissions list"""
    if not is_authenticated(request):
        return RedirectResponse(url="/admin/login", status_code=302)
    return templates.TemplateResponse("admin/submissions.html", {"request": request})


@router.get("/admin/departments")
async def admin_departments(request: Request):
    """Admin departments management"""
    if not is_authenticated(request):
        return RedirectResponse(url="/admin/login", status_code=302)
    return templates.TemplateResponse("admin/departments.html", {"request": request})


@router.get("/admin/categories")
async def admin_categories(request: Request):
    """Admin categories management"""
    if not is_authenticated(request):
        return RedirectResponse(url="/admin/login", status_code=302)
    return templates.TemplateResponse("admin/categories.html", {"request": request})


@router.get("/admin/settings")
async def admin_settings(request: Request):
    """Admin settings page"""
    if not is_authenticated(request):
        return RedirectResponse(url="/admin/login", status_code=302)
    return templates.TemplateResponse("admin/settings.html", {"request": request})


@router.get("/admin/notifications")
async def admin_notifications(request: Request):
    """Admin notification settings page"""
    if not is_authenticated(request):
        return RedirectResponse(url="/admin/login", status_code=302)
    return templates.TemplateResponse("admin/notifications.html", {"request": request})


@router.get("/admin/members")
async def admin_members(request: Request):
    """Admin member search page"""
    if not is_authenticated(request):
        return RedirectResponse(url="/admin/login", status_code=302)
    return templates.TemplateResponse("admin/members.html", {"request": request})


@router.get("/admin/member/{member_id}/profile")
async def admin_member_profile(request: Request, member_id: int):
    """Admin member profile page"""
    if not is_authenticated(request):
        return RedirectResponse(url="/admin/login", status_code=302)
    return templates.TemplateResponse("admin/member_profile.html", {"request": request})


# ============ DESK ROUTES ============

@router.get("/desk/login")
async def desk_login_page(request: Request):
    """Desk login page"""
    if is_desk_authenticated(request):
        return RedirectResponse(url="/desk", status_code=302)
    return templates.TemplateResponse("desk/login.html", {"request": request, "error": None})


@router.post("/desk/login")
async def desk_login(
    request: Request,
    response: Response,
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    """Process desk login"""
    setting = db.query(Settings).filter(Settings.key == "deskPassword").first()
    correct_password = setting.value if setting else "desk123"

    if password == correct_password:
        response = RedirectResponse(url="/desk", status_code=302)
        response.set_cookie(key=DESK_COOKIE_NAME, value="authenticated", httponly=True)
        return response
    else:
        return templates.TemplateResponse(
            "desk/login.html",
            {"request": request, "error": "Invalid password"}
        )


@router.get("/desk/logout")
async def desk_logout():
    """Log out desk user"""
    response = RedirectResponse(url="/desk/login", status_code=302)
    response.delete_cookie(key=DESK_COOKIE_NAME)
    return response


@router.get("/desk")
async def desk_dashboard(request: Request):
    """Desk dashboard - search and new submissions"""
    if not is_desk_authenticated(request):
        return RedirectResponse(url="/desk/login", status_code=302)
    return templates.TemplateResponse("desk/dashboard.html", {"request": request})


@router.get("/desk/new")
async def desk_new_submission(request: Request):
    """Desk new submission form"""
    if not is_desk_authenticated(request):
        return RedirectResponse(url="/desk/login", status_code=302)
    return templates.TemplateResponse("desk/new.html", {"request": request})


@router.get("/desk/member/{member_id}")
async def desk_member_edit(request: Request, member_id: int):
    """Desk member edit page"""
    if not is_desk_authenticated(request):
        return RedirectResponse(url="/desk/login", status_code=302)
    return templates.TemplateResponse("desk/member.html", {"request": request})


# ============ ADMIN APPROVAL ROUTES ============

@router.get("/admin/approvals")
async def admin_approvals(request: Request):
    """Admin approvals management"""
    if not is_authenticated(request):
        return RedirectResponse(url="/admin/login", status_code=302)
    return templates.TemplateResponse("admin/approvals.html", {"request": request})


@router.get("/admin/publish")
async def admin_publish(request: Request):
    """Admin publish management"""
    if not is_authenticated(request):
        return RedirectResponse(url="/admin/login", status_code=302)
    return templates.TemplateResponse("admin/publish.html", {"request": request})


@router.get("/admin/appeals")
async def admin_appeals(request: Request):
    """Admin appeals management"""
    if not is_authenticated(request):
        return RedirectResponse(url="/admin/login", status_code=302)
    return templates.TemplateResponse("admin/appeals.html", {"request": request})


# ============ MEMBER RESULTS ROUTES ============

@router.get("/portal")
async def member_portal(request: Request, db: Session = Depends(get_db)):
    """Member portal - requires login session or redirects to login"""
    member = get_current_member(request, db)
    phone = request.query_params.get("phone")
    if not member and not phone:
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse("portal.html", {"request": request})


@router.get("/programs")
async def member_programs(request: Request, phone: str = None, db: Session = Depends(get_db)):
    """Service programs page for service managers, HODs, and elders"""
    import json

    if not phone:
        return RedirectResponse(url="/", status_code=302)

    # Find member by phone
    normalized = phone.strip().replace(" ", "").replace("-", "")
    member = None
    for m in db.query(Member).all():
        m_normalized = m.phone.strip().replace(" ", "").replace("-", "")
        if m_normalized == normalized or m.phone == phone:
            member = m
            break

    if not member:
        return RedirectResponse(url="/", status_code=302)

    # Check leadership roles
    roles = []
    if member.leadership_roles:
        try:
            roles = json.loads(member.leadership_roles) if isinstance(member.leadership_roles, str) else member.leadership_roles
        except (ValueError, TypeError):
            roles = []

    allowed_roles = {"service_manager", "elder", "deacon", "admin"}
    is_hod = db.query(Settings).filter(False).first() is None  # placeholder
    # Check if member is HOD of any department
    from models import Department
    hod_depts = db.query(Department).filter(Department.hod_member_id == member.id).all()
    is_hod = len(hod_depts) > 0

    if not (set(roles) & allowed_roles) and not is_hod:
        return RedirectResponse(url=f"/portal?phone={phone}", status_code=302)

    return templates.TemplateResponse("programs.html", {"request": request})


@router.get("/program/{program_id}")
async def view_program(request: Request, program_id: int):
    """Read-only program view for participants"""
    return templates.TemplateResponse("program_view.html", {"request": request})


@router.get("/results")
async def member_results(request: Request):
    """Member results lookup page (legacy - redirects to portal)"""
    return templates.TemplateResponse("results.html", {"request": request})


@router.get("/appeal")
async def submit_appeal_page(request: Request):
    """Appeal submission page"""
    return templates.TemplateResponse("appeal.html", {"request": request})


# ============ DESK MEMBER PROFILE ROUTE ============

@router.get("/desk/member/{member_id}/profile")
async def desk_member_profile(request: Request, member_id: int):
    """Desk view of member's approved profile and appeals"""
    if not is_desk_authenticated(request):
        return RedirectResponse(url="/desk/login", status_code=302)
    return templates.TemplateResponse("desk/profile.html", {"request": request})


# ============ MEETING ROUTES ============

@router.get("/hod/meetings")
async def hod_meetings(request: Request):
    """HOD meeting management with calendar"""
    return templates.TemplateResponse("hod/meetings.html", {"request": request})


@router.get("/admin/meetings")
async def admin_meetings(request: Request):
    """Admin meeting management"""
    if not is_authenticated(request):
        return RedirectResponse(url="/admin/login", status_code=302)
    return templates.TemplateResponse("admin/meetings.html", {"request": request})


# ============ POSTER REQUEST ROUTES ============

@router.get("/poster-request")
async def poster_request_form(request: Request):
    """Poster request form - requires phone login"""
    return templates.TemplateResponse("poster_request.html", {"request": request})


@router.get("/admin/poster-requests")
async def admin_poster_requests(request: Request):
    """Admin poster requests management"""
    if not is_authenticated(request):
        return RedirectResponse(url="/admin/login", status_code=302)
    return templates.TemplateResponse("admin/poster_requests.html", {"request": request})


@router.get("/admin/programs")
async def admin_programs(request: Request):
    """Admin service programs management"""
    if not is_authenticated(request):
        return RedirectResponse(url="/admin/login", status_code=302)
    return templates.TemplateResponse("admin/programs.html", {"request": request})


@router.get("/admin/program-templates")
async def admin_program_templates(request: Request):
    """Admin program templates management"""
    if not is_authenticated(request):
        return RedirectResponse(url="/admin/login", status_code=302)
    return templates.TemplateResponse("admin/program_templates.html", {"request": request})


@router.get("/admin/schedules")
async def admin_schedules(request: Request):
    """Admin service schedule management"""
    if not is_authenticated(request):
        return RedirectResponse(url="/admin/login", status_code=302)
    return templates.TemplateResponse("admin/schedules.html", {"request": request})


@router.get("/display/submit")
async def display_submit_page(request: Request):
    """Public page for submitting content to FirePresenter"""
    return templates.TemplateResponse("display_submit.html", {"request": request})

@router.get("/songlist")
async def songlist_page(request: Request):
    """Public page for the music team to submit song lists and new songs"""
    return templates.TemplateResponse("songlist_submit.html", {"request": request})
