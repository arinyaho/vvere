"""
/api/branches  — branch staleness and staging overview
"""
import os
from fastapi import APIRouter, Query, Request

STALE_DAYS = int(os.environ.get("STALE_DAYS", 3))

router = APIRouter()


@router.get("/stale")
async def stale_branches(
    request: Request,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    order: str = Query("desc", pattern="^(asc|desc)$"),
):
    """Branches with no commit in STALE_DAYS days, paginated and sortable by last_commit_at."""
    pool = request.app.state.pool
    direction = "DESC" if order == "desc" else "ASC"
    offset = (page - 1) * per_page

    total = await pool.fetchval(
        """
        SELECT COUNT(*) FROM branches b
        WHERE b.last_commit_at < NOW() - ($1 || ' days')::INTERVAL
          AND b.name NOT IN ('main','master','develop','dev')
          AND b.is_staging = FALSE
        """,
        str(STALE_DAYS),
    )

    rows = await pool.fetch(
        f"""
        SELECT
            b.name,
            b.last_commit_at,
            EXTRACT(EPOCH FROM (NOW() - b.last_commit_at)) / 86400 AS stale_days,
            r.owner, r.name AS repo_name,
            p.number  AS pr_number,
            p.title   AS pr_title,
            p.pr_url  AS pr_url
        FROM branches b
        JOIN repos r ON r.id = b.repo_id
        LEFT JOIN LATERAL (
            SELECT number, title, pr_url
            FROM pull_requests
            WHERE repo_id = b.repo_id
              AND head_branch = b.name
              AND state = 'open'
            ORDER BY created_at DESC
            LIMIT 1
        ) p ON TRUE
        WHERE
            b.last_commit_at < NOW() - ($1 || ' days')::INTERVAL
            AND b.name NOT IN ('main','master','develop','dev')
            AND b.is_staging = FALSE
        ORDER BY b.last_commit_at {direction}
        LIMIT $2 OFFSET $3
        """,
        str(STALE_DAYS), per_page, offset,
    )

    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "items": [
            {
                "repo": f"{r['owner']}/{r['repo_name']}",
                "branch": r["name"],
                "last_commit_at": r["last_commit_at"],
                "stale_days": round(float(r["stale_days"]), 1),
                "pr": {
                    "number": r["pr_number"],
                    "title": r["pr_title"],
                    "url": r["pr_url"],
                } if r["pr_number"] else None,
            }
            for r in rows
        ],
    }


@router.get("/staging")
async def staging_branches(request: Request):
    """All stage/* branches with age and recent CI status."""
    pool = request.app.state.pool

    rows = await pool.fetch(
        """
        SELECT
            b.name,
            b.last_commit_at,
            b.last_commit_sha,
            EXTRACT(EPOCH FROM (NOW() - b.last_commit_at)) / 86400 AS age_days,
            r.id AS repo_id, r.owner, r.name AS repo_name
        FROM branches b
        JOIN repos r ON r.id = b.repo_id
        WHERE b.is_staging = TRUE
        ORDER BY b.last_commit_at DESC
        """
    )

    result = []
    for r in rows:
        # Latest push run on this staging branch
        run = await pool.fetchrow(
            """
            SELECT conclusion, created_at, run_url
            FROM workflow_runs
            WHERE repo_id = $1 AND branch = $2 AND trigger = 'push' AND status = 'completed'
            ORDER BY created_at DESC LIMIT 1
            """,
            r["repo_id"], r["name"],
        )
        result.append({
            "repo": f"{r['owner']}/{r['repo_name']}",
            "branch": r["name"],
            "last_commit_at": r["last_commit_at"],
            "age_days": round(float(r["age_days"]), 1),
            "latest_ci": {
                "conclusion": run["conclusion"] if run else None,
                "created_at": run["created_at"] if run else None,
                "run_url": run["run_url"] if run else None,
            },
        })

    return result
