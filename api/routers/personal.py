"""
/api/personal  — stats for a specific GitHub username
"""
from fastapi import APIRouter, Request, Query

router = APIRouter()


@router.get("")
async def personal_stats(request: Request, author: str = Query(...)):
    pool = request.app.state.pool

    # PRs authored (open)
    authored = await pool.fetch(
        """
        SELECT
            p.number, p.title, p.pr_url,
            EXTRACT(EPOCH FROM (NOW() - p.created_at)) / 86400 AS age_days,
            r.owner, r.name AS repo_name
        FROM pull_requests p
        JOIN repos r ON r.id = p.repo_id
        WHERE p.author = $1 AND p.state = 'open'
        ORDER BY p.created_at ASC
        """,
        author,
    )

    # PRs where user is a requested reviewer (open)
    review_requested = await pool.fetch(
        """
        SELECT
            p.number, p.title, p.pr_url, p.author,
            EXTRACT(EPOCH FROM (NOW() - p.created_at)) / 86400 AS age_days,
            r.owner, r.name AS repo_name
        FROM pull_requests p
        JOIN repos r ON r.id = p.repo_id
        WHERE p.state = 'open'
          AND p.requested_reviewers @> $1::jsonb
        ORDER BY p.created_at ASC
        """,
        f'["{author}"]',
    )

    def _pr(row):
        return {
            "number": row["number"],
            "title": row["title"],
            "url": row["pr_url"],
            "repo": f"{row['owner']}/{row['repo_name']}",
            "age_days": round(float(row["age_days"]), 1),
        }

    authored_list = [_pr(r) for r in authored]
    review_list   = [{**_pr(r), "author": r["author"]} for r in review_requested]

    return {
        "authored": {
            "count": len(authored_list),
            "oldest": authored_list[0] if authored_list else None,
            "prs": authored_list,
        },
        "review_requested": {
            "count": len(review_list),
            "oldest": review_list[0] if review_list else None,
            "prs": review_list,
        },
    }
