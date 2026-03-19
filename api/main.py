import os
import asyncpg
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from routers import overview, repos, prs, branches, collector_trigger, personal, snapshot, setup, recommend
from routers.demo import DEMO_MODE

DATABASE_URL = os.environ.get("DATABASE_URL", "")
AUTH_DISABLED = os.environ.get("AUTH_DISABLED", "").lower() == "true"
_pool: asyncpg.Pool | None = None


def _detect_auth_mode() -> str:
    if AUTH_DISABLED or DEMO_MODE:
        return "disabled"
    if os.environ.get("GOOGLE_CLIENT_ID"):
        return "google"
    if os.environ.get("GITHUB_CLIENT_ID") and os.environ.get("GITHUB_CLIENT_SECRET"):
        return "github_web"
    return "github_device"


AUTH_MODE = _detect_auth_mode()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _pool
    if not DEMO_MODE and DATABASE_URL:
        _pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
        app.state.pool = _pool
    yield
    if _pool:
        await _pool.close()


app = FastAPI(title="vvere API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
    allow_credentials=True,
)

# --- Auth router selection based on mode ---
if AUTH_MODE == "google":
    from routers import auth as auth_router_mod
    auth_router = auth_router_mod.router
    _get_current_user = auth_router_mod.get_current_user
elif AUTH_MODE in ("github_web", "github_device"):
    from routers import auth_github as auth_router_mod
    auth_router = auth_router_mod.router
    _get_current_user = auth_router_mod.get_current_user
else:
    from fastapi import APIRouter
    auth_router = APIRouter()

    @auth_router.get("/config")
    async def config_disabled():
        return {"mode": "disabled"}

    @auth_router.get("/me")
    async def me_disabled():
        return {"authenticated": True, "username": "demo" if DEMO_MODE else "anonymous"}

    def _get_current_user(request: Request) -> str:
        return "demo" if DEMO_MODE else "anonymous"


# --- Demo mode: override data endpoints with fake data ---
if DEMO_MODE:
    from routers import demo
    demo_router = APIRouter()

    @demo_router.get("/overview")
    async def demo_overview_ep():
        return demo.demo_overview()

    @demo_router.get("/repos")
    async def demo_repos_ep():
        return demo.demo_repos()

    @demo_router.get("/repos/{repo_id}/ci-status")
    async def demo_ci_status_ep(repo_id: int):
        return demo.demo_ci_status(repo_id)

    @demo_router.get("/repos/{repo_id}/ci-success-rate")
    async def demo_success_rate_ep(repo_id: int):
        return demo.demo_success_rates(repo_id)

    @demo_router.get("/repos/{repo_id}/ci-duration")
    async def demo_duration_ep(repo_id: int):
        return demo.demo_durations(repo_id)

    @demo_router.get("/prs/open")
    async def demo_open_prs_ep():
        return demo.demo_open_prs()

    @demo_router.get("/prs/summary")
    async def demo_pr_summary_ep():
        return []

    @demo_router.get("/branches/stale")
    async def demo_stale_ep():
        items = demo.demo_stale_branches()
        return {"total": len(items), "page": 1, "per_page": 20, "items": items}

    @demo_router.get("/branches/staging")
    async def demo_staging_ep():
        return demo.demo_staging()

    @demo_router.get("/snapshot")
    async def demo_snapshot_ep():
        return demo.demo_snapshot()

    app.include_router(auth_router, prefix="/api/auth")
    app.include_router(demo_router, prefix="/api")

else:
    @app.middleware("http")
    async def require_auth(request: Request, call_next):
        if request.url.path.startswith("/api/auth") or request.url.path.startswith("/api/setup") or request.url.path == "/healthz":
            return await call_next(request)
        if AUTH_DISABLED:
            return await call_next(request)
        user = _get_current_user(request)
        if not user:
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        return await call_next(request)

    app.include_router(auth_router,              prefix="/api/auth")
    app.include_router(overview.router,          prefix="/api/overview")
    app.include_router(repos.router,             prefix="/api/repos")
    app.include_router(prs.router,               prefix="/api/prs")
    app.include_router(branches.router,          prefix="/api/branches")
    app.include_router(collector_trigger.router, prefix="/api/refresh")
    app.include_router(personal.router,          prefix="/api/personal")
    app.include_router(snapshot.router,          prefix="/api/snapshot")
    app.include_router(setup.router,            prefix="/api/setup")
    app.include_router(recommend.router,        prefix="/api/recommend")


@app.get("/healthz")
async def health():
    return {"ok": True}
