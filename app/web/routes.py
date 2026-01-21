"""Web routes for the community portal."""

from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from typing import Optional
from datetime import datetime
import secrets

from ..config import config
from ..database import db
from ..services.bbs import bbs_service

router = APIRouter()

# Set up templates
templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(templates_dir))

# Simple session store (in production, use Redis or similar)
_sessions: dict[str, dict] = {}


def get_common_context(request: Request) -> dict:
    """Get common template context."""
    return {
        "request": request,
        "community_name": config.web.community_name,
        "community_description": config.web.community_description,
        "location_name": config.web.location_name,
        "current_year": datetime.now().year,
        # Branding assets
        "favicon": config.web.favicon,
        "logo_large": config.web.logo_large,
        "logo_small": config.web.logo_small,
        # Bot identity
        "bot_short_name": config.mesh.bot_short_name,
        "bot_long_name": config.mesh.bot_name,
        # MQTT
        "mqtt_enabled": config.mqtt.enabled,
        "mqtt_region_name": config.mqtt.region_name,
    }


def get_client_ip(request: Request) -> str:
    """Get the real client IP, accounting for proxies."""
    # Check for forwarded headers (Cloudflare, nginx, etc.)
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip
    
    # Direct connection
    return request.client.host if request.client else "unknown"


def is_admin_allowed(request: Request) -> bool:
    """Check if admin access is allowed from this IP."""
    client_ip = get_client_ip(request)
    access_mode = config.web.admin_access.lower()
    
    if access_mode == "all":
        return True
    
    if access_mode == "localhost":
        # Only allow localhost (127.0.0.1, ::1)
        return client_ip in ("127.0.0.1", "::1", "localhost")
    
    if access_mode == "local":
        # Allow localhost and private network ranges
        if client_ip in ("127.0.0.1", "::1", "localhost"):
            return True
        # Check for private IP ranges
        if client_ip.startswith("192.168.") or client_ip.startswith("10.") or client_ip.startswith("172."):
            return True
        return False
    
    # Default: localhost only
    return client_ip in ("127.0.0.1", "::1", "localhost")


def is_authenticated(request: Request) -> bool:
    """Check if user is authenticated."""
    session_id = request.cookies.get("session_id")
    return session_id in _sessions


def require_auth(request: Request):
    """Dependency to require authentication."""
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Not authenticated")
    return True


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Community info/landing page."""
    context = get_common_context(request)
    context["page_title"] = "Home"
    return templates.TemplateResponse("index.html", context)


@router.get("/status", response_class=HTMLResponse)
async def status(request: Request):
    """Combined status page with weather, nodes, and activity."""
    from ..main import app_state
    
    context = get_common_context(request)
    context["page_title"] = "Status"
    
    # Get messages
    messages = await db.get_messages(limit=50)
    context["messages"] = messages
    
    # Get nodes
    nodes = []
    if app_state.mesh:
        nodes = app_state.mesh.get_all_nodes()
        # Sort: online first, then by signal strength (SNR), then by last heard
        nodes.sort(key=lambda n: (
            not n.is_online,  # False (online) sorts before True (offline)
            -(n.snr or -999),  # Higher SNR first (negate for descending)
            -(n.last_heard.timestamp() if n.last_heard else 0)  # More recent first
        ))
    context["nodes"] = nodes
    
    # Get BBS posts
    bbs_posts = await bbs_service.get_all_posts(limit=20)
    context["bbs_posts"] = bbs_posts
    
    context["is_connected"] = app_state.mesh and app_state.mesh.is_connected()
    
    return templates.TemplateResponse("dashboard.html", context)


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_redirect(request: Request):
    """Redirect old dashboard URL to status."""
    return RedirectResponse(url="/status", status_code=301)


@router.get("/bbs", response_class=HTMLResponse)
async def bbs_page(request: Request, board: Optional[str] = None):
    """Bulletin board page."""
    context = get_common_context(request)
    context["page_title"] = "Bulletin Board"
    
    # Get boards
    context["boards"] = config.bbs.boards
    context["current_board"] = board or "General"
    
    # Get posts for selected board
    if board:
        posts = await bbs_service.get_board_posts(board, limit=50)
    else:
        posts = await bbs_service.get_all_posts(limit=50)
    
    context["posts"] = posts
    
    return templates.TemplateResponse("bbs.html", context)


@router.get("/help", response_class=HTMLResponse)
async def help_page(request: Request):
    """Help and commands reference."""
    context = get_common_context(request)
    context["page_title"] = "Help & Commands"
    context["command_prefix"] = config.safety.command_prefix
    
    # Define available commands
    context["commands"] = [
        {"name": "help", "desc": "Show this help message"},
        {"name": "ping", "desc": "Test connection to the bot"},
        {"name": "weather / wx", "desc": "Get current weather conditions"},
        {"name": "forecast", "desc": "Get 3-day weather forecast"},
        {"name": "bbs", "desc": "Show bulletin board posts"},
        {"name": "mail", "desc": "Check your mail messages"},
        {"name": "post <msg>", "desc": "Post a message to the BBS"},
        {"name": "post @node <msg>", "desc": "Send private mail to a node"},
        {"name": "read <#>", "desc": "Read a specific post"},
        {"name": "nodes", "desc": "List discovered nodes"},
        {"name": "info", "desc": "Show community info"},
        {"name": "ai <question>", "desc": "Ask the AI assistant"},
        {"name": "clear", "desc": "Clear your AI conversation history"},
    ]
    
    return templates.TemplateResponse("help.html", context)


# ─────────────────────────────────────────────────────────────
# ADMIN ROUTES
# ─────────────────────────────────────────────────────────────

@router.get("/admin/login", response_class=HTMLResponse)
async def admin_login_page(request: Request):
    """Admin login page."""
    # Check IP-based access control first
    if not is_admin_allowed(request):
        context = get_common_context(request)
        context["page_title"] = "Access Denied"
        context["error_message"] = f"Admin access is restricted. Your IP: {get_client_ip(request)}"
        return templates.TemplateResponse("admin_denied.html", context)
    
    if is_authenticated(request):
        return RedirectResponse(url="/admin", status_code=302)
    
    context = get_common_context(request)
    context["page_title"] = "Admin Login"
    context["error"] = request.query_params.get("error")
    return templates.TemplateResponse("admin_login.html", context)


@router.post("/admin/login")
async def admin_login(request: Request, password: str = Form(...)):
    """Handle admin login."""
    # Check IP-based access control
    if not is_admin_allowed(request):
        return RedirectResponse(url="/admin/login", status_code=302)
    
    if password == config.web.admin_password:
        session_id = secrets.token_urlsafe(32)
        _sessions[session_id] = {"created": datetime.now()}
        
        response = RedirectResponse(url="/admin", status_code=302)
        response.set_cookie(
            key="session_id",
            value=session_id,
            httponly=True,
            max_age=86400,  # 24 hours
            samesite="lax"
        )
        return response
    else:
        return RedirectResponse(url="/admin/login?error=invalid", status_code=302)


@router.get("/admin/logout")
async def admin_logout(request: Request):
    """Handle admin logout."""
    session_id = request.cookies.get("session_id")
    if session_id in _sessions:
        del _sessions[session_id]
    
    response = RedirectResponse(url="/", status_code=302)
    response.delete_cookie("session_id")
    return response


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    """Admin panel - requires authentication."""
    # Check IP-based access control first
    if not is_admin_allowed(request):
        context = get_common_context(request)
        context["page_title"] = "Access Denied"
        context["error_message"] = f"Admin access is restricted. Your IP: {get_client_ip(request)}"
        return templates.TemplateResponse("admin_denied.html", context)
    
    if not is_authenticated(request):
        return RedirectResponse(url="/admin/login", status_code=302)
    
    from ..main import app_state
    
    context = get_common_context(request)
    context["page_title"] = "Admin Panel"
    context["is_admin"] = True
    
    # Get stats
    messages = await db.get_messages(limit=1000)
    nodes_db = await db.get_nodes()
    
    context["message_count"] = len(messages)
    context["node_count"] = len(nodes_db)
    context["config"] = config
    context["is_connected"] = app_state.mesh and app_state.mesh.is_connected() if app_state.mesh else False
    
    return templates.TemplateResponse("admin.html", context)
