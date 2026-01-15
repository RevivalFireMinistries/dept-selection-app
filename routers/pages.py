from fastapi import APIRouter, Request, Depends, Form, Response
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from typing import Optional

from database import get_db
from models import Settings

router = APIRouter()
templates = Jinja2Templates(directory="templates")

ADMIN_COOKIE_NAME = "admin_session"


def is_authenticated(request: Request) -> bool:
    """Check if user has valid admin session cookie"""
    return request.cookies.get(ADMIN_COOKIE_NAME) == "authenticated"


# ============ PUBLIC ROUTES ============

@router.get("/")
async def home(request: Request):
    """Public department selection form"""
    return templates.TemplateResponse("index.html", {"request": request})


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
