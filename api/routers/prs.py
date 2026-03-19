"""
/api/prs  — pull request stats
"""
from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/open")
async def open_prs(request: Request):
    """All open PRs with age and stale flag."""
    pool = request.app.state.pool

    rows = await pool.fetch(
        """
        SELECT
            p.id, p.number, p.title, p.author, p.is_automated, p.draft,
            p.head_branch, p.base_branch, p.pr_url,
            p.created_at, p.updated_at,
            EXTRACT(EPOCH FROM (NOW() - p.created_at)) / 86400 AS age_days,
            EXTRACT(EPOCH FROM (NOW() - p.updated_at)) / 86400 AS stale_days,
            r.owner, r.name AS repo_name
        FROM pull_requests p
        JOIN repos r ON r.id = p.repo_id
        WHERE p.state = 'open'
        ORDER BY p.created_at ASC
        """
    )

    return [
        {
            "id": r["id"],
            "number": r["number"],
            "title": r["title"],
            "author": r["author"],
            "is_automated": r["is_automated"],
            "draft": r["draft"],
            "repo": f"{r['owner']}/{r['repo_name']}",
            "head_branch": r["head_branch"],
            "base_branch": r["base_branch"],
            "pr_url": r["pr_url"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
            "age_days": round(r["age_days"], 1),
            "stale_days": round(r["stale_days"], 1),
        }
        for r in rows
    ]


@router.get("/summary")
async def pr_summary(request: Request):
    """Per-repo open PR count, avg age, longest open PR."""
    pool = request.app.state.pool

    rows = await pool.fetch(
        """
        SELECT
            r.id AS repo_id,
            r.owner,
            r.name AS repo_name,
            COUNT(*) AS open_count,
            ROUND(AVG(EXTRACT(EPOCH FROM (NOW() - p.created_at)) / 86400), 1) AS avg_age_days,
            MAX(EXTRACT(EPOCH FROM (NOW() - p.created_at)) / 86400) AS max_age_days,
            (
                SELECT pr_url FROM pull_requests
                WHERE repo_id = r.id AND state = 'open'
                ORDER BY created_at ASC LIMIT 1
            ) AS oldest_pr_url,
            (
                SELECT number FROM pull_requests
                WHERE repo_id = r.id AND state = 'open'
                ORDER BY created_at ASC LIMIT 1
            ) AS oldest_pr_number
        FROM pull_requests p
        JOIN repos r ON r.id = p.repo_id
        WHERE p.state = 'open'
        GROUP BY r.id, r.owner, r.name
        ORDER BY r.name
        """
    )

    return [
        {
            "repo": f"{r['owner']}/{r['repo_name']}",
            "open_count": r["open_count"],
            "avg_age_days": float(r["avg_age_days"]) if r["avg_age_days"] else None,
            "max_age_days": round(float(r["max_age_days"]), 1) if r["max_age_days"] else None,
            "oldest_pr_url": r["oldest_pr_url"],
            "oldest_pr_number": r["oldest_pr_number"],
        }
        for r in rows
    ]


