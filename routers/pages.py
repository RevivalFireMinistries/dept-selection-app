from fastapi import APIRouter, Request, Depends, Form, Response
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse, FileResponse
from sqlalchemy.orm import Session
from typing import Optional
import hmac, hashlib, os

from database import get_db
from models import Settings, Member

router = APIRouter()


class RFMTemplates(Jinja2Templates):
    """Jinja2Templates subclass that automatically injects the assembly's
    resolved feature flags into every TemplateResponse context.

    The flags are placed on request.state.features by inject_features_middleware
    (main.py) so every template can use {{ features.kg }}, {{ features.giving }},
    etc. without any individual route handler needing to pass them explicitly.
    """

    def TemplateResponse(self, *args, **kwargs):
        """Inject features + assembly into every template context.

        Handles both Starlette calling conventions:
          Old: TemplateResponse(name: str, context: dict, ...)
          New: TemplateResponse(request: Request, name: str, context: dict, ...)

        We detect which convention is in use by checking whether the first
        positional arg is a string (old) or a Request object (new).
        """
        # ── Find the context dict regardless of calling convention ───────────
        # Pull context from kwargs first; fall back to scanning positional args.
        context = kwargs.get("context")
        if not isinstance(context, dict):
            for arg in args:
                if isinstance(arg, dict):
                    context = arg
                    break

        # ── Find the request object ──────────────────────────────────────────
        # It may be in context["request"] or passed as a positional arg.
        req = None
        if isinstance(context, dict):
            req = context.get("request")
        if req is None:
            for arg in args:
                if hasattr(arg, "state"):   # duck-type: Request has .state
                    req = arg
                    break

        # ── Inject features and assembly ─────────────────────────────────────
        if isinstance(context, dict):
            if req is not None:
                try:
                    if "features" not in context:
                        context["features"] = req.state.features
                except AttributeError:
                    pass
                try:
                    if "assembly" not in context:
                        context["assembly"] = req.state.assembly
                except AttributeError:
                    pass
            if "features" not in context:
                from features import default_features
                context["features"] = default_features()
            if "assembly" not in context:
                context["assembly"] = {
                    "id": None,
                    "name": "Revival Fire Ministries",
                    "configured": False,
                }

        return super().TemplateResponse(*args, **kwargs)


templates = RFMTemplates(directory="templates")

ADMIN_COOKIE_NAME = "admin_session"
ADMIN_IDENTITY_COOKIE = "admin_identity"
DESK_COOKIE_NAME = "desk_session"
MEMBER_COOKIE_NAME = "member_session"
SESSION_SECRET = os.environ.get("SESSION_SECRET", "rfm-stellenbosch-portal-2026")
SESSION_MAX_AGE = 90 * 24 * 60 * 60  # 90 days


def is_authenticated(request: Request) -> bool:
    """Check if user has valid admin session cookie AND identity (phone-based login)"""
    if request.cookies.get(ADMIN_COOKIE_NAME) != "authenticated":
        return False
    # Require admin identity cookie (set during phone-based login)
    # Admins who logged in before identity tracking must re-login
    identity = get_admin_identity(request)
    if not identity or not identity.get("member_id"):
        return False
    return True


def get_admin_identity(request: Request) -> dict:
    """Get the admin's identity from signed cookie. Returns {member_id, name} or None."""
    token = request.cookies.get(ADMIN_IDENTITY_COOKIE)
    if not token:
        return None
    try:
        import json
        parts = token.rsplit(".", 1)
        if len(parts) != 2:
            return None
        payload, sig = parts
        expected = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()[:16]
        if not hmac.compare_digest(sig, expected):
            return None
        return json.loads(payload)
    except Exception:
        return None


def _sign_admin_identity(member_id: int, name: str) -> str:
    """Create a signed admin identity cookie value."""
    import json
    payload = json.dumps({"member_id": member_id, "name": name})
    sig = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()[:16]
    return f"{payload}.{sig}"


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


# ============ PWA ROUTES ============

@router.get("/sw.js", include_in_schema=False)
async def service_worker():
    """Serve the service worker at the site root so it can claim '/' scope."""
    response = FileResponse("static/sw.js", media_type="application/javascript")
    # Allow root scope and prevent stale SW being cached aggressively
    response.headers["Service-Worker-Allowed"] = "/"
    response.headers["Cache-Control"] = "no-cache"
    return response


@router.get("/manifest.webmanifest", include_in_schema=False)
async def manifest():
    return FileResponse("static/manifest.webmanifest", media_type="application/manifest+json")


@router.get("/offline", include_in_schema=False)
async def offline_page(request: Request):
    return templates.TemplateResponse("offline.html", {"request": request})


@router.get("/not-configured", include_in_schema=False)
async def not_configured_page(request: Request):
    """Shown when the portal has no assembly UUID configured."""
    return templates.TemplateResponse("not_configured.html", {"request": request})


# ── Feature-gate helper ───────────────────────────────────────────────────────

def _require_feature(request: Request, flag: str):
    """Raise 404 if a feature flag is disabled for this assembly.
    Call at the top of any page route that should be inaccessible when
    the flag is off — returns None on success, raises HTTPException on fail."""
    from fastapi import HTTPException
    features = getattr(request.state, "features", {})
    if not features.get(flag, True):
        raise HTTPException(status_code=404, detail="This feature is not enabled for your assembly.")


# ============ PUBLIC ROUTES ============

@router.get("/")
async def landing(request: Request, db: Session = Depends(get_db)):
    """Login page — redirects to portal (or to ``?next=`` if set) when
    the member is already logged in. The ``next`` param is what lets
    deep links in emails (e.g. the EXAM_UNLOCKED notification's
    ``/portal/kg/cycle/{id}?start_exam=1``) survive the login bounce."""
    member = get_current_member(request, db)
    if member:
        nxt = (request.query_params.get("next") or "").strip()
        # Only honour same-site relative paths so an email link can't be
        # weaponised as an open-redirect (e.g. ``?next=https://evil.example``).
        if nxt.startswith("/") and not nxt.startswith("//"):
            return RedirectResponse(url=nxt, status_code=302)
        return RedirectResponse(url=f"/portal?phone={member.phone}", status_code=302)
    return templates.TemplateResponse("landing.html", {"request": request})


@router.get("/register")
async def register_page(request: Request):
    """Registration page"""
    return templates.TemplateResponse("register.html", {"request": request})


@router.get("/verify-email")
async def verify_email_page(request: Request):
    """Landing page for the email verification link. Reads ?token=… from
    the URL and calls /api/auth/verify-email client-side."""
    return templates.TemplateResponse("verify_email.html", {"request": request})


@router.get("/forgot-password")
async def forgot_password_page(request: Request):
    """Forgot password page"""
    return templates.TemplateResponse("forgot_password.html", {"request": request})


@router.get("/reset-password")
async def reset_password_page(request: Request):
    """Reset password page (with token from email)"""
    return templates.TemplateResponse("reset_password.html", {"request": request})


@router.get("/setup-password")
async def setup_password_page(request: Request):
    """Landing page for admin-issued portal invites. Same form + same
    underlying /auth/reset-password endpoint as the reset flow — the
    template just reads ?setup=1 / a flag to swap the headline so a
    member who has never had a password isn't confused by the word
    "Reset". Reuses reset_password.html with an `is_setup` context flag.
    """
    return templates.TemplateResponse(
        "reset_password.html",
        {"request": request, "is_setup": True},
    )


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
    phone: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    """Process admin login with phone number identification"""
    phone = phone.strip()
    if not phone:
        return templates.TemplateResponse(
            "admin/login.html",
            {"request": request, "error": "Phone number is required", "phone": ""}
        )

    # Look up the member by phone
    normalized = phone.replace(" ", "").replace("-", "")
    member = None
    for m in db.query(Member).all():
        m_normalized = m.phone.strip().replace(" ", "").replace("-", "")
        if m_normalized == normalized or m.phone == phone:
            member = m
            break

    if not member:
        return templates.TemplateResponse(
            "admin/login.html",
            {"request": request, "error": "No member found with this phone number", "phone": phone}
        )

    # Check if member has admin role
    import json as _json
    roles = []
    if member.leadership_roles:
        try:
            roles = _json.loads(member.leadership_roles) if isinstance(member.leadership_roles, str) else member.leadership_roles
        except (ValueError, TypeError):
            roles = []
    if "admin" not in roles:
        return templates.TemplateResponse(
            "admin/login.html",
            {"request": request, "error": "You do not have admin access. Please contact an administrator.", "phone": phone}
        )

    # Verify admin password
    setting = db.query(Settings).filter(Settings.key == "adminPassword").first()
    correct_password = setting.value if setting else "admin123"

    if password != correct_password:
        return templates.TemplateResponse(
            "admin/login.html",
            {"request": request, "error": "Invalid admin password", "phone": phone}
        )

    # Log the admin login
    from models import AdminAuditLog
    log = AdminAuditLog(
        admin_member_id=member.id,
        admin_name=member.full_name,
        action="admin_login",
        entity_type="admin",
        details=f"Admin login from {request.client.host}" if request.client else "Admin login",
        ip_address=request.client.host if request.client else None
    )
    db.add(log)
    db.commit()

    response = RedirectResponse(url="/admin", status_code=302)
    response.set_cookie(key=ADMIN_COOKIE_NAME, value="authenticated", httponly=True)
    response.set_cookie(
        key=ADMIN_IDENTITY_COOKIE,
        value=_sign_admin_identity(member.id, member.full_name),
        httponly=True,
        max_age=SESSION_MAX_AGE
    )
    return response


@router.get("/admin/logout")
async def admin_logout():
    """Log out admin"""
    response = RedirectResponse(url="/admin/login", status_code=302)
    response.delete_cookie(key=ADMIN_COOKIE_NAME)
    response.delete_cookie(key=ADMIN_IDENTITY_COOKIE)
    return response


@router.get("/admin")
async def admin_dashboard(request: Request):
    """Admin dashboard"""
    if not is_authenticated(request):
        return RedirectResponse(url="/admin/login", status_code=302)
    return templates.TemplateResponse("admin/dashboard.html", {"request": request})


@router.get("/admin/audit-log")
async def admin_audit_log_page(request: Request):
    """Admin audit log page"""
    if not is_authenticated(request):
        return RedirectResponse(url="/admin/login", status_code=302)
    return templates.TemplateResponse("admin/audit_log.html", {"request": request})


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


@router.get("/admin/features")
async def admin_features_page(request: Request):
    """Admin: enable / disable features for this assembly deployment."""
    if not is_authenticated(request):
        return RedirectResponse(url="/admin/login", status_code=302)
    from features import FEATURES
    return templates.TemplateResponse(
        "admin/features.html",
        {"request": request, "FEATURES": FEATURES},
    )


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


@router.get("/give")
async def public_give_page(request: Request):
    """Public Yoco giving page. No login required."""
    _require_feature(request, "public_giving")
    return templates.TemplateResponse("give.html", {"request": request})


@router.get("/give/success")
async def public_give_success_page(request: Request):
    return templates.TemplateResponse("give_success.html", {"request": request})


@router.get("/give/cancel")
async def public_give_cancel_page(request: Request):
    return templates.TemplateResponse("payment_cancel.html", {"request": request})


@router.get("/give/failure")
async def public_give_failure_page(request: Request):
    return templates.TemplateResponse("payment_failure.html", {"request": request})


@router.get("/connect")
async def public_connect_form(request: Request):
    """Public visitor connect card. No login required."""
    _require_feature(request, "connect_form")
    return templates.TemplateResponse("connect.html", {"request": request})


@router.get("/admin/connect-submissions")
async def admin_connect_submissions_page(request: Request):
    if not is_authenticated(request):
        return RedirectResponse(url="/admin/login", status_code=302)
    _require_feature(request, "admin_connect_submissions")
    return templates.TemplateResponse("admin/connect_submissions.html", {"request": request})


@router.get("/admin/devotionals")
async def admin_devotionals_page(request: Request):
    if not is_authenticated(request):
        return RedirectResponse(url="/admin/login", status_code=302)
    _require_feature(request, "admin_devotionals")
    return templates.TemplateResponse("admin/devotionals/list.html", {"request": request})


@router.get("/admin/devotionals/builder")
async def admin_devotionals_builder(request: Request):
    if not is_authenticated(request):
        return RedirectResponse(url="/admin/login", status_code=302)
    _require_feature(request, "admin_devotionals")
    return templates.TemplateResponse("admin/devotionals/builder.html", {"request": request})


@router.get("/admin/announcements")
async def admin_announcements_page(request: Request):
    if not is_authenticated(request):
        return RedirectResponse(url="/admin/login", status_code=302)
    return templates.TemplateResponse("admin/announcements.html", {"request": request})


@router.get("/admin/change-requests")
async def admin_change_requests_page(request: Request):
    if not is_authenticated(request):
        return RedirectResponse(url="/admin/login", status_code=302)
    return templates.TemplateResponse("admin/change_requests.html", {"request": request})


@router.get("/admin/surveys")
async def admin_surveys_page(request: Request):
    """Admin: list all surveys + manage who can create them."""
    if not is_authenticated(request):
        return RedirectResponse(url="/admin/login", status_code=302)
    _require_feature(request, "admin_surveys")
    return templates.TemplateResponse("admin/surveys.html", {"request": request})


@router.get("/admin/surveys/builder")
async def admin_surveys_builder(request: Request):
    """Admin: create or edit a survey (?id=N for edit)."""
    if not is_authenticated(request):
        return RedirectResponse(url="/admin/login", status_code=302)
    _require_feature(request, "admin_surveys")
    return templates.TemplateResponse("admin/survey_builder.html", {"request": request})


@router.get("/admin/surveys/{survey_id}/responses")
async def admin_survey_responses_page(request: Request, survey_id: int):
    """Admin: view responses for a survey."""
    if not is_authenticated(request):
        return RedirectResponse(url="/admin/login", status_code=302)
    _require_feature(request, "admin_surveys")
    return templates.TemplateResponse("admin/survey_responses.html", {"request": request})


@router.get("/surveys")
async def member_surveys_page(request: Request, db: Session = Depends(get_db)):
    """Authorised member's survey list."""
    _require_feature(request, "surveys")
    member = get_current_member(request, db)
    if not member:
        return RedirectResponse(url="/?next=/surveys", status_code=302)
    if not getattr(member, "can_create_surveys", False) and not is_authenticated(request):
        # Not authorised — show the explanatory page so they know who to ask.
        return templates.TemplateResponse(
            "surveys_unauthorised.html", {"request": request}, status_code=403
        )
    return templates.TemplateResponse("surveys_list.html", {"request": request})


@router.get("/surveys/builder")
async def member_surveys_builder(request: Request, db: Session = Depends(get_db)):
    """Member-side builder."""
    member = get_current_member(request, db)
    if not member:
        return RedirectResponse(url="/?next=/surveys/builder", status_code=302)
    if not getattr(member, "can_create_surveys", False) and not is_authenticated(request):
        return templates.TemplateResponse(
            "surveys_unauthorised.html", {"request": request}, status_code=403
        )
    return templates.TemplateResponse("survey_builder.html", {"request": request})


@router.get("/surveys/{survey_id}/responses")
async def member_survey_responses_page(request: Request, survey_id: int, db: Session = Depends(get_db)):
    member = get_current_member(request, db)
    if not member:
        return RedirectResponse(url=f"/?next=/surveys/{survey_id}/responses", status_code=302)
    if not getattr(member, "can_create_surveys", False) and not is_authenticated(request):
        return templates.TemplateResponse(
            "surveys_unauthorised.html", {"request": request}, status_code=403
        )
    return templates.TemplateResponse("survey_responses.html", {"request": request})


@router.get("/s/{slug}")
async def public_survey_page(request: Request, slug: str, db: Session = Depends(get_db)):
    _require_feature(request, "public_surveys")
    """Public response page — no auth required."""
    """Public response page — no auth required. Server-rendered meta so link
    previews (WhatsApp / FB / Twitter) show the survey title."""
    from models import Survey
    survey = db.query(Survey).filter(Survey.slug == slug).first()
    meta = {
        "title": "RFM Survey",
        "description": "Networking nations through Christ Jesus.",
        "is_anonymous": False,
        "exists": survey is not None,
    }
    if survey:
        meta["title"] = survey.title
        meta["is_anonymous"] = bool(survey.is_anonymous)
        if survey.is_anonymous:
            meta["description"] = "Anonymous survey — your identity is not recorded."
        elif survey.description:
            meta["description"] = survey.description[:200]
        else:
            meta["description"] = f"Respond to: {survey.title[:140]}"
    return templates.TemplateResponse(
        "public_survey.html",
        {"request": request, "slug": slug, "meta": meta},
    )


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
    _require_feature(request, "info_desk")
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
    """Member-centric portal - requires login session or redirects to login.
    Replaced the legacy department-selection view in May 2026; the old
    portal lives at /portal/classic for any flows we haven't migrated yet."""
    member = get_current_member(request, db)
    phone = request.query_params.get("phone")
    if not member and not phone:
        return RedirectResponse(url="/", status_code=302)
    # Honour ?next= for deep links surviving the post-login bounce (the
    # landing/login flow sets ?phone=... and then this hop carries the
    # member to the original destination — e.g. an EXAM_UNLOCKED email
    # link to ``/portal/kg/cycle/{id}?start_exam=1``).
    nxt = (request.query_params.get("next") or "").strip()
    if member and nxt.startswith("/") and not nxt.startswith("//"):
        return RedirectResponse(url=nxt, status_code=302)
    return templates.TemplateResponse("portal.html", {"request": request})


@router.get("/reading-plans")
async def member_reading_plans(request: Request, db: Session = Depends(get_db)):
    member = get_current_member(request, db)
    if not member:
        return RedirectResponse(url="/?next=/reading-plans", status_code=302)
    return templates.TemplateResponse("reading_plans/discover.html", {"request": request})


@router.get("/reading-plans/{slug}")
async def member_reading_plan_detail(request: Request, slug: str, db: Session = Depends(get_db)):
    member = get_current_member(request, db)
    if not member:
        return RedirectResponse(url=f"/?next=/reading-plans/{slug}", status_code=302)
    return templates.TemplateResponse("reading_plans/detail.html", {"request": request, "slug": slug})


@router.get("/admin/reading-plans")
async def admin_reading_plans_page(request: Request):
    if not is_authenticated(request):
        return RedirectResponse(url="/admin/login", status_code=302)
    return templates.TemplateResponse("admin/reading_plans/list.html", {"request": request})


@router.get("/admin/reading-plans/builder")
async def admin_reading_plans_builder(request: Request):
    if not is_authenticated(request):
        return RedirectResponse(url="/admin/login", status_code=302)
    return templates.TemplateResponse("admin/reading_plans/builder.html", {"request": request})


@router.get("/admin/reading-plans/{plan_id}/metrics")
async def admin_reading_plan_metrics_page(request: Request, plan_id: int):
    if not is_authenticated(request):
        return RedirectResponse(url="/admin/login", status_code=302)
    return templates.TemplateResponse("admin/reading_plans/metrics.html", {"request": request})


@router.get("/portal/payment/success")
async def payment_success_page(request: Request):
    return templates.TemplateResponse("payment_success.html", {"request": request})


@router.get("/portal/payment/cancel")
async def payment_cancel_page(request: Request):
    return templates.TemplateResponse("payment_cancel.html", {"request": request})


@router.get("/portal/payment/failure")
async def payment_failure_page(request: Request):
    return templates.TemplateResponse("payment_failure.html", {"request": request})


@router.get("/portal/devotional")
async def member_devotional_page(request: Request, db: Session = Depends(get_db)):
    """Full-page reading view of today's (or an archived) devotional."""
    member = get_current_member(request, db)
    if not member:
        return RedirectResponse(url="/?next=/portal/devotional", status_code=302)
    return templates.TemplateResponse("portal_devotional.html", {"request": request})


@router.get("/portal/profile")
async def member_profile_page(request: Request, db: Session = Depends(get_db)):
    """Member-facing profile page — shows everything we know about them."""
    member = get_current_member(request, db)
    if not member:
        return RedirectResponse(url="/?next=/portal/profile", status_code=302)
    return templates.TemplateResponse("portal_profile.html", {"request": request})


@router.get("/portal/classic")
async def member_portal_classic(request: Request, db: Session = Depends(get_db)):
    """Legacy portal — kept for flows that haven't moved to the new design
    (department selection form, family profile selector, design-team etc.)."""
    member = get_current_member(request, db)
    phone = request.query_params.get("phone")
    if not member and not phone:
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse("portal_classic.html", {"request": request})


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
    _require_feature(request, "meetings")
    return templates.TemplateResponse("hod/meetings.html", {"request": request})


@router.get("/admin/meetings")
async def admin_meetings(request: Request):
    """Admin meeting management"""
    if not is_authenticated(request):
        return RedirectResponse(url="/admin/login", status_code=302)
    _require_feature(request, "admin_meetings")
    return templates.TemplateResponse("admin/meetings.html", {"request": request})


# ============ POSTER REQUEST ROUTES ============

@router.get("/poster-request")
async def poster_request_form(request: Request):
    """Poster request form - requires phone login"""
    _require_feature(request, "poster_requests")
    return templates.TemplateResponse("poster_request.html", {"request": request})


@router.get("/admin/poster-requests")
async def admin_poster_requests(request: Request):
    """Admin poster requests management"""
    if not is_authenticated(request):
        return RedirectResponse(url="/admin/login", status_code=302)
    _require_feature(request, "admin_poster_requests")
    return templates.TemplateResponse("admin/poster_requests.html", {"request": request})


@router.get("/admin/programs")
async def admin_programs(request: Request):
    """Admin service programs management"""
    if not is_authenticated(request):
        return RedirectResponse(url="/admin/login", status_code=302)
    _require_feature(request, "admin_programs")
    return templates.TemplateResponse("admin/programs.html", {"request": request})


@router.get("/admin/program-templates")
async def admin_program_templates(request: Request):
    """Admin program templates management"""
    if not is_authenticated(request):
        return RedirectResponse(url="/admin/login", status_code=302)
    _require_feature(request, "admin_programs")
    return templates.TemplateResponse("admin/program_templates.html", {"request": request})


@router.get("/admin/schedules")
async def admin_schedules(request: Request):
    """Admin service schedule management"""
    if not is_authenticated(request):
        return RedirectResponse(url="/admin/login", status_code=302)
    _require_feature(request, "admin_programs")
    return templates.TemplateResponse("admin/schedules.html", {"request": request})


@router.get("/admin/rfm-sync")
async def admin_rfm_sync(request: Request):
    """Admin: review which local members are matched to / orphaned from
    the central rfm-database directory."""
    if not is_authenticated(request):
        return RedirectResponse(url="/admin/login", status_code=302)
    _require_feature(request, "admin_rfm_sync")
    return templates.TemplateResponse("admin/rfm_sync.html", {"request": request})


@router.get("/admin/home-churches")
async def admin_home_churches(request: Request, phone: Optional[str] = None):
    _require_feature(request, "admin_home_churches")
    """Home Church Committee: manage home churches and preacher roster.

    Admins get the full admin chrome. Committee members (by member_session OR
    by ?phone= fallback from the portal link) see a committee-branded view."""
    if is_authenticated(request):
        return templates.TemplateResponse(
            "admin/home_churches.html",
            {
                "request": request,
                "is_admin": True,
                "actor_phone": None,
                "is_hc_leader": False,
                "is_committee": True,
                "leader_hc_ids": [],
            },
        )

    from database import get_db as _get_db
    from models import Member, Department, MemberDepartment

    # Resolve acting member — prefer signed cookie, fall back to ?phone=
    member_id = _verify_member_session(request.cookies.get(MEMBER_COOKIE_NAME))
    db_iter = _get_db()
    db = next(db_iter)
    actor_phone = None
    try:
        acting_member: Optional[Member] = None
        if member_id:
            acting_member = db.query(Member).filter(Member.id == member_id).first()
        if not acting_member and phone:
            normalized = phone.strip().replace(" ", "").replace("-", "")
            for m in db.query(Member).all():
                if m.phone.strip().replace(" ", "").replace("-", "") == normalized:
                    acting_member = m
                    break
        if not acting_member:
            return RedirectResponse(url="/admin/login", status_code=302)

        # Case-insensitive match: "Home Church Committee", "Home Church committee", etc.
        committee_dept_ids = [
            d.id for d in db.query(Department).all()
            if "home church" in (d.name or "").lower() and "committee" in (d.name or "").lower()
        ]
        if not committee_dept_ids:
            return RedirectResponse(url="/admin/login", status_code=302)
        is_committee = db.query(MemberDepartment).filter(
            MemberDepartment.member_id == acting_member.id,
            MemberDepartment.department_id.in_(committee_dept_ids),
            MemberDepartment.status == "approved",
        ).count() > 0
        # Home church leaders also get access (their own home church only —
        # the API enforces per-cell access checks via _require_hc_access).
        from models import HomeChurch as _HC
        is_hc_leader = db.query(_HC).filter(_HC.leader_member_id == acting_member.id).count() > 0
        if not is_committee and not is_hc_leader:
            return RedirectResponse(url=f"/portal?phone={acting_member.phone}", status_code=302)
        actor_phone = acting_member.phone
        leader_hc_ids = [
            hc.id for hc in db.query(_HC).filter(_HC.leader_member_id == acting_member.id).all()
        ]
    finally:
        try:
            db.close()
        except Exception:
            pass

    response = templates.TemplateResponse(
        "admin/home_churches.html",
        {
            "request": request,
            "is_admin": False,
            "actor_phone": actor_phone,
            "is_hc_leader": is_hc_leader,
            "is_committee": is_committee,
            "leader_hc_ids": leader_hc_ids,
        },
    )
    # Set the member session cookie so the API calls from this page pass auth
    if member_id is None and actor_phone:
        # We came in via ?phone=; resolve member_id and set the signed cookie
        db2 = next(_get_db())
        try:
            m = db2.query(Member).filter(Member.phone == actor_phone).first()
            if m:
                response.set_cookie(
                    key=MEMBER_COOKIE_NAME,
                    value=_sign_member_session(m.id),
                    httponly=True,
                    max_age=SESSION_MAX_AGE,
                    samesite="lax",
                )
        finally:
            db2.close()
    return response


@router.get("/display/submit")
async def display_submit_page(request: Request, db: Session = Depends(get_db)):
    """Display submission page — requires login and projector_submit feature."""
    _require_feature(request, "projector_submit")
    member = get_current_member(request, db)
    if not member:
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse("display_submit.html", {"request": request})

@router.get("/projector")
async def projector_page(request: Request, db: Session = Depends(get_db)):
    """Projector operator screen — requires login and projector_operator feature."""
    _require_feature(request, "projector_operator")
    member = get_current_member(request, db)
    if not member:
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse("projector.html", {"request": request})

@router.get("/songlist")
async def songlist_page(request: Request):
    """Legacy songlist page — redirects to projector"""
    _require_feature(request, "songs")
    return RedirectResponse(url="/projector", status_code=302)
