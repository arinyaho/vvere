"""
/api/setup  — First-run setup wizard.
Only accessible when admin_config.setup_complete is FALSE.
After setup, this endpoint returns 404.
"""
import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


async def _is_setup_complete(pool) -> bool:
    row = await pool.fetchrow("SELECT setup_complete FROM admin_config WHERE id = 1")
    return bool(row and row["setup_complete"])


@router.get("/status")
async def setup_status(request: Request):
    """Check if setup is needed (public endpoint)."""
    pool = request.app.state.pool
    complete = await _is_setup_complete(pool)
    return {"setup_complete": complete}


@router.get("/orgs")
async def list_orgs(request: Request):
    """List GitHub orgs the authenticated user belongs to.
    Requires the user's access token passed as Authorization header.
    """
    auth = request.headers.get("Authorization", "")
    if not auth:
        return JSONResponse({"error": "Authorization header required"}, status_code=401)

    async with httpx.AsyncClient() as client:
        r = await client.get(
            "https://api.github.com/user/orgs",
            headers={
                "Authorization": auth,
                "Accept": "application/vnd.github+json",
            },
            params={"per_page": 100},
            timeout=10,
        )
        if r.status_code != 200:
            return JSONResponse({"error": "Failed to fetch orgs"}, status_code=r.status_code)

        orgs = r.json()
        return [{"login": o["login"], "avatar_url": o.get("avatar_url", "")} for o in orgs]


@router.get("/repos")
async def list_org_repos(request: Request, org: str):
    """List repos in a GitHub org. Requires Authorization header."""
    auth = request.headers.get("Authorization", "")
    if not auth:
        return JSONResponse({"error": "Authorization header required"}, status_code=401)

    repos = []
    page = 1
    async with httpx.AsyncClient() as client:
        while True:
            r = await client.get(
                f"https://api.github.com/orgs/{org}/repos",
                headers={
                    "Authorization": auth,
                    "Accept": "application/vnd.github+json",
                },
                params={"per_page": 100, "page": page, "sort": "pushed", "direction": "desc"},
                timeout=15,
            )
            if r.status_code != 200:
                return JSONResponse({"error": f"Failed to fetch repos for {org}"}, status_code=r.status_code)

            batch = r.json()
            if not batch:
                break
            repos.extend(batch)
            if len(batch) < 100:
                break
            page += 1

    return [
        {
            "full_name": r["full_name"],
            "name": r["name"],
            "description": r.get("description", ""),
            "private": r["private"],
            "pushed_at": r.get("pushed_at", ""),
            "language": r.get("language", ""),
        }
        for r in repos
    ]


@router.post("/complete")
async def complete_setup(request: Request):
    """Complete the setup wizard. Stores token + config in DB.

    Body:
    {
        "github_token": "gho_...",
        "github_org": "my-org",
        "repos": ["my-org/repo1", "my-org/repo2"],
        "username": "admin-user",
        "initial_fetch_days": 90
    }
    """
    pool = request.app.state.pool

    if await _is_setup_complete(pool):
        return JSONResponse({"error": "Setup already complete"}, status_code=404)

    body = await request.json()
    token = body.get("github_token", "")
    org = body.get("github_org", "")
    repos_list = body.get("repos", [])
    username = body.get("username", "")
    initial_fetch_days = body.get("initial_fetch_days", 90)

    if not token:
        return JSONResponse({"error": "github_token is required"}, status_code=400)

    # Validate token
    async with httpx.AsyncClient() as client:
        r = await client.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
            timeout=10,
        )
        if r.status_code != 200:
            return JSONResponse({"error": "Invalid GitHub token"}, status_code=400)

    # Store config
    await pool.execute(
        """
        UPDATE admin_config
        SET github_token = $1,
            github_org = $2,
            initial_fetch_days = $3,
            setup_complete = TRUE,
            setup_by = $4,
            setup_at = NOW()
        WHERE id = 1
        """,
        token, org, initial_fetch_days, username,
    )

    # Seed repos
    if isinstance(repos_list, list):
        for r in repos_list:
            r = r.strip() if isinstance(r, str) else ""
            if "/" in r:
                owner, name = r.split("/", 1)
                await pool.execute(
                    "INSERT INTO repos (owner, name) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                    owner, name,
                )

    return {"ok": True, "message": "Setup complete. Collector will start shortly."}


@router.post("/extend-history")
async def extend_history(request: Request):
    """Extend the data fetch period. Resets cursors so collector re-fetches further back.

    Body: {"days": 90}
    """
    pool = request.app.state.pool
    body = await request.json()
    days = body.get("days", 90)

    if days < 1 or days > 730:
        return JSONResponse({"error": "days must be 1-730"}, status_code=400)

    # Update initial_fetch_days in admin_config
    await pool.execute(
        "UPDATE admin_config SET initial_fetch_days = $1 WHERE id = 1",
        days,
    )

    # Delete existing cursors — collector will re-fetch from new start date
    deleted = await pool.fetchval(
        "WITH d AS (DELETE FROM fetch_cursor RETURNING 1) SELECT COUNT(*) FROM d"
    )

    return {
        "ok": True,
        "message": f"History extended to {days} days. {deleted} cursors reset. Collector will re-fetch on next cycle.",
    }


@router.get("/collection-status")
async def collection_status(request: Request):
    """Current collection progress for the setup/onboarding UI."""
    pool = request.app.state.pool

    repos = await pool.fetch("SELECT id, owner, name FROM repos ORDER BY id")
    cursors = await pool.fetch("SELECT repo_id, data_type, last_fetched_at FROM fetch_cursor")
    cursor_map = {(c["repo_id"], c["data_type"]): c["last_fetched_at"] for c in cursors}

    sync = await pool.fetchrow("SELECT last_sync_at, last_sync_ok FROM sync_status WHERE id = 1")

    repo_status = []
    for r in repos:
        runs_done = (r["id"], "runs") in cursor_map
        prs_done = (r["id"], "prs") in cursor_map
        branches_done = (r["id"], "branches") in cursor_map
        repo_status.append({
            "repo": f"{r['owner']}/{r['name']}",
            "runs": runs_done,
            "prs": prs_done,
            "branches": branches_done,
            "complete": runs_done and prs_done and branches_done,
        })

    total = len(repos)
    done = sum(1 for r in repo_status if r["complete"])

    return {
        "total_repos": total,
        "repos_complete": done,
        "all_complete": done == total and total > 0,
        "repos": repo_status,
        "last_sync_at": sync["last_sync_at"].isoformat() if sync and sync["last_sync_at"] else None,
        "sync_ok": sync["last_sync_ok"] if sync else None,
    }
