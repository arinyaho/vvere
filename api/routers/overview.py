"""
/api/overview  — top-level dashboard summary
"""
from fastapi import APIRouter, Request

router = APIRouter()


@router.get("")
async def get_overview(request: Request):
    pool = request.app.state.pool

    # Last collection timestamp
    last_collected = await pool.fetchval(
        "SELECT MAX(last_fetched_at) FROM fetch_cursor"
    )

    # Open PR totals
    open_prs = await pool.fetchval(
        "SELECT COUNT(*) FROM pull_requests WHERE state='open'"
    )
    # Repo count
    repo_count = await pool.fetchval("SELECT COUNT(*) FROM repos")

    # Merge velocity: merged PRs in last 7 days / 7
    merged_7d = await pool.fetchval(
        "SELECT COUNT(*) FROM pull_requests"
        " WHERE merged_at >= NOW() - INTERVAL '7 days'"
    )
    velocity_per_day = round(merged_7d / 7, 1) if merged_7d else 0.0

    # Sync health
    sync = await pool.fetchrow("SELECT last_sync_at, last_sync_ok, last_error FROM sync_status WHERE id = 1")

    return {
        "last_collected_at": last_collected,
        "repo_count": repo_count,
        "open_prs": open_prs,
        "velocity_per_day": velocity_per_day,
        "sync": {
            "last_sync_at": sync["last_sync_at"] if sync else None,
            "ok": sync["last_sync_ok"] if sync else None,
            "error": sync["last_error"] if sync else None,
        } if sync else None,
    }
