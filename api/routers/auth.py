"""
/api/auth  — Google OAuth login restricted to ALLOWED_DOMAIN
"""
import os
from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse
from authlib.integrations.httpx_client import AsyncOAuth2Client
from itsdangerous import TimestampSigner, BadSignature, SignatureExpired

GOOGLE_CLIENT_ID     = os.environ["GOOGLE_CLIENT_ID"]
GOOGLE_CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
SESSION_SECRET       = os.environ["SESSION_SECRET"]
PUBLIC_URL           = os.environ.get("PUBLIC_URL", "http://localhost:9339")
ALLOWED_DOMAIN       = os.environ["ALLOWED_DOMAIN"]

REDIRECT_URI = f"{PUBLIC_URL}/api/auth/callback"
COOKIE_NAME  = "session"
COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days

signer = TimestampSigner(SESSION_SECRET)
router = APIRouter()


def _set_session(response: Response, email: str):
    token = signer.sign(email).decode()
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
        email = signer.unsign(token, max_age=COOKIE_MAX_AGE).decode()
        return email
    except (BadSignature, SignatureExpired):
        return None


@router.get("/login")
async def login(request: Request):
    async with AsyncOAuth2Client(
        client_id=GOOGLE_CLIENT_ID,
        redirect_uri=REDIRECT_URI,
        scope="openid email profile",
    ) as client:
        uri, state = client.create_authorization_url(
            "https://accounts.google.com/o/oauth2/v2/auth",
            access_type="online",
        )
    response = RedirectResponse(uri)
    response.set_cookie("oauth_state", state, httponly=True, max_age=300, samesite="lax")
    return response


@router.get("/callback")
async def callback(request: Request, code: str, state: str):
    stored_state = request.cookies.get("oauth_state", "")

    async with AsyncOAuth2Client(
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        redirect_uri=REDIRECT_URI,
        state=stored_state,
    ) as client:
        token = await client.fetch_token(
            "https://oauth2.googleapis.com/token",
            code=code,
            authorization_response=str(request.url),
        )
        userinfo = await client.get("https://www.googleapis.com/oauth2/v3/userinfo")
        userinfo = userinfo.json()

    email: str = userinfo.get("email", "")
    if not email.endswith(f"@{ALLOWED_DOMAIN}"):
        return Response(
            content=f"Access denied: only @{ALLOWED_DOMAIN} accounts are allowed.",
            status_code=403,
        )

    response = RedirectResponse("/")
    response.delete_cookie("oauth_state")
    _set_session(response, email)
    return response


@router.get("/config")
async def config():
    """Public — exposes non-secret config the frontend needs before login."""
    return {"allowed_domain": ALLOWED_DOMAIN}


@router.get("/me")
async def me(request: Request):
    email = get_current_user(request)
    if not email:
        return {"authenticated": False}
    return {"authenticated": True, "email": email}


@router.post("/logout")
async def logout():
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(COOKIE_NAME)
    return response
