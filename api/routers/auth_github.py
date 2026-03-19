"""
/api/auth  — GitHub Device Flow + Web Flow authentication.
Mode is auto-detected from env vars (see detect_auth_mode()).
"""
import os
import asyncio
import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse, JSONResponse
from itsdangerous import TimestampSigner, BadSignature, SignatureExpired

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# Auth mode detection (priority order):
# 1. AUTH_DISABLED=true -> no auth
# 2. GOOGLE_CLIENT_ID set -> Google OAuth (handled by auth.py, not this module)
# 3. GITHUB_CLIENT_ID + GITHUB_CLIENT_SECRET set -> GitHub Web Flow
# 4. Neither -> GitHub Device Flow (default, uses built-in VVERE_CLIENT_ID)

GITHUB_CLIENT_ID = os.environ.get("GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.environ.get("GITHUB_CLIENT_SECRET", "")
GITHUB_ORG = os.environ.get("GITHUB_ORG", "")
GITHUB_USERS = [
    u.strip() for u in os.environ.get("GITHUB_USERS", "").split(",") if u.strip()
]

# Built-in vvere OAuth App client ID for device flow (public, no secret needed).
# Replace with your own registered GitHub OAuth App client ID.
VVERE_CLIENT_ID = os.environ.get("VVERE_CLIENT_ID", "Ov23lixDQTNtWn5VIpTb")

SESSION_SECRET = os.environ.get("SESSION_SECRET", "")
if not SESSION_SECRET:
    import secrets
    SESSION_SECRET = secrets.token_hex(32)

ADMIN_USERS = [
    u.strip() for u in os.environ.get("ADMIN_USERS", "").split(",") if u.strip()
]

COOKIE_NAME = "session"
COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days
SUDO_COOKIE = "sudo"
SUDO_TTL = 60 * 10  # 10 minutes

signer = TimestampSigner(SESSION_SECRET)
sudo_signer = TimestampSigner(SESSION_SECRET + ":sudo")
router = APIRouter()


def _effective_client_id() -> str:
    """Return the client ID to use: explicit GITHUB_CLIENT_ID or built-in VVERE_CLIENT_ID."""
    return GITHUB_CLIENT_ID or VVERE_CLIENT_ID


def is_web_flow() -> bool:
    return bool(GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET)


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def _set_session(response: Response, username: str):
    token = signer.sign(username).decode()
    response.set_cookie(
        COOKIE_NAME, token,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
    )


def get_current_user(request: Request) -> str | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        username = signer.unsign(token, max_age=COOKIE_MAX_AGE).decode()
        return username
    except (BadSignature, SignatureExpired):
        return None


# ---------------------------------------------------------------------------
# Authorization: org membership or user allowlist
# ---------------------------------------------------------------------------

async def _is_authorized(username: str, access_token: str) -> bool:
    """Check if user is authorized via org membership or explicit allowlist."""
    # Explicit user allowlist
    if GITHUB_USERS and username in GITHUB_USERS:
        return True

    # Org membership check
    if GITHUB_ORG:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"https://api.github.com/orgs/{GITHUB_ORG}/members/{username}",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github+json",
                },
                timeout=10,
            )
            if r.status_code == 204:
                return True
            # 302 means user is member but needs 2FA — still authorized
            if r.status_code == 302:
                return True
            return False

    # No org or allowlist configured — allow anyone with valid GitHub login
    if not GITHUB_ORG and not GITHUB_USERS:
        return True

    return False


# ---------------------------------------------------------------------------
# Public config endpoint
# ---------------------------------------------------------------------------

@router.get("/config")
async def config():
    """Public — tells the frontend which auth mode to render."""
    client_id = _effective_client_id()
    return {
        "mode": "github_web" if is_web_flow() else "github_device",
        "github_org": GITHUB_ORG or None,
        "client_id": client_id if not is_web_flow() else None,
    }


# ---------------------------------------------------------------------------
# Device Flow endpoints
# ---------------------------------------------------------------------------

@router.post("/device/start")
async def device_start():
    """Step 1: Request a device code from GitHub."""
    client_id = _effective_client_id()
    if not client_id:
        return JSONResponse(
            {"error": "No GitHub OAuth App configured. Set VVERE_CLIENT_ID or GITHUB_CLIENT_ID."},
            status_code=500,
        )

    async with httpx.AsyncClient() as client:
        r = await client.post(
            "https://github.com/login/device/code",
            data={
                "client_id": client_id,
                "scope": "repo read:org",
            },
            headers={"Accept": "application/json"},
            timeout=10,
        )
        if r.status_code != 200:
            return JSONResponse({"error": "Failed to start device flow", "detail": r.text}, status_code=502)

        data = r.json()
        return {
            "device_code": data["device_code"],
            "user_code": data["user_code"],
            "verification_uri": data["verification_uri"],
            "expires_in": data["expires_in"],
            "interval": data.get("interval", 5),
        }


@router.post("/device/poll")
async def device_poll(request: Request):
    """Step 2: Frontend polls this until the user approves on GitHub."""
    body = await request.json()
    device_code = body.get("device_code", "")
    client_id = _effective_client_id()

    async with httpx.AsyncClient() as client:
        r = await client.post(
            "https://github.com/login/oauth/access_token",
            data={
                "client_id": client_id,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
            headers={"Accept": "application/json"},
            timeout=10,
        )
        data = r.json()

        error = data.get("error")
        if error == "authorization_pending":
            return {"status": "pending"}
        if error == "slow_down":
            return {"status": "slow_down", "interval": data.get("interval", 10)}
        if error == "expired_token":
            return JSONResponse({"status": "expired"}, status_code=410)
        if error:
            return JSONResponse({"status": "error", "error": error}, status_code=400)

        # Success — we have an access token
        access_token = data["access_token"]

        # Fetch user info
        user_r = await client.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github+json",
            },
            timeout=10,
        )
        user_data = user_r.json()
        username = user_data.get("login", "")

        # Authorization check
        if not await _is_authorized(username, access_token):
            org_msg = f"@{GITHUB_ORG}" if GITHUB_ORG else "allowlist"
            return JSONResponse(
                {"status": "denied", "error": f"Access denied: {username} is not a member of {org_msg}"},
                status_code=403,
            )

        # Set session cookie — include access_token for setup wizard
        response = JSONResponse({
            "status": "ok",
            "username": username,
            "access_token": access_token,
        })
        _set_session(response, username)
        return response


# ---------------------------------------------------------------------------
# Web Flow endpoints (standard OAuth redirect)
# ---------------------------------------------------------------------------

@router.get("/login")
async def login(request: Request):
    """Redirect to GitHub OAuth authorize page (Web Flow)."""
    if is_web_flow():
        client_id = GITHUB_CLIENT_ID
        redirect_uri = str(request.base_url).rstrip("/") + "/api/auth/callback"
        url = (
            f"https://github.com/login/oauth/authorize"
            f"?client_id={client_id}"
            f"&redirect_uri={redirect_uri}"
            f"&scope=repo%20read:org"
        )
        return RedirectResponse(url)
    else:
        # Device flow — frontend handles it, this shouldn't be called
        return JSONResponse({"error": "Use device flow"}, status_code=400)


@router.get("/callback")
async def callback(request: Request, code: str):
    """GitHub OAuth callback (Web Flow only)."""
    if not is_web_flow():
        return JSONResponse({"error": "Web flow not configured"}, status_code=400)

    async with httpx.AsyncClient() as client:
        # Exchange code for token
        r = await client.post(
            "https://github.com/login/oauth/access_token",
            data={
                "client_id": GITHUB_CLIENT_ID,
                "client_secret": GITHUB_CLIENT_SECRET,
                "code": code,
            },
            headers={"Accept": "application/json"},
            timeout=10,
        )
        data = r.json()
        access_token = data.get("access_token", "")
        if not access_token:
            return JSONResponse({"error": "Failed to get access token"}, status_code=400)

        # Fetch user
        user_r = await client.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github+json",
            },
            timeout=10,
        )
        user_data = user_r.json()
        username = user_data.get("login", "")

        # Authorization check
        if not await _is_authorized(username, access_token):
            org_msg = f"@{GITHUB_ORG}" if GITHUB_ORG else "allowlist"
            return Response(
                content=f"Access denied: {username} is not a member of {org_msg}",
                status_code=403,
            )

    response = RedirectResponse("/")
    _set_session(response, username)
    return response


# ---------------------------------------------------------------------------
# Common endpoints
# ---------------------------------------------------------------------------

@router.get("/me")
async def me(request: Request):
    username = get_current_user(request)
    if not username:
        return {"authenticated": False}
    is_admin = await _is_admin(username, request.app.state.pool)
    has_sudo = _has_sudo(request)
    return {
        "authenticated": True,
        "username": username,
        "is_admin": is_admin,
        "sudo_active": has_sudo,
    }


@router.post("/logout")
async def logout():
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(COOKIE_NAME)
    response.delete_cookie(SUDO_COOKIE)
    return response


# ---------------------------------------------------------------------------
# Role & Sudo
# ---------------------------------------------------------------------------

async def _is_admin(username: str, pool) -> bool:
    """Check if user is an admin: ADMIN_USERS env or setup_by in DB."""
    if ADMIN_USERS and username in ADMIN_USERS:
        return True
    row = await pool.fetchrow(
        "SELECT setup_by FROM admin_config WHERE id = 1 AND setup_complete = TRUE"
    )
    if row and row["setup_by"] == username:
        return True
    return False


def _has_sudo(request: Request) -> bool:
    """Check if the current session has active sudo (10min TTL)."""
    token = request.cookies.get(SUDO_COOKIE)
    if not token:
        return False
    try:
        sudo_signer.unsign(token, max_age=SUDO_TTL)
        return True
    except (BadSignature, SignatureExpired):
        return False


@router.post("/sudo")
async def activate_sudo(request: Request):
    """Activate sudo mode for the current session (10min TTL).
    Requires re-confirming the user is authenticated.
    """
    username = get_current_user(request)
    if not username:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)

    pool = request.app.state.pool
    if not await _is_admin(username, pool):
        return JSONResponse({"error": "Not an admin"}, status_code=403)

    response = JSONResponse({"ok": True, "sudo_ttl_seconds": SUDO_TTL})
    sudo_token = sudo_signer.sign(username).decode()
    response.set_cookie(
        SUDO_COOKIE, sudo_token,
        max_age=SUDO_TTL,
        httponly=True,
        samesite="lax",
    )
    return response


def require_sudo(request: Request) -> str | None:
    """Verify current user is admin with active sudo. Returns username or None."""
    username = get_current_user(request)
    if not username:
        return None
    if not _has_sudo(request):
        return None
    return username
