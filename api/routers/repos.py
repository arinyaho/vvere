"""
/api/repos  — per-repo CI stats
"""
from fastapi import APIRouter, Request

router = APIRouter()

TRIGGER_LABELS = {
    "push": "main push",
    "schedule": "nightly",
    "pull_request": "PR",
    "workflow_dispatch": "manual",
}


@router.get("")
async def list_repos(request: Request):
    pool = request.app.state.pool
    repos = await pool.fetch("SELECT id, owner, name FROM repos ORDER BY name")
    return [
        {
            "id": r["id"],
            "owner": r["owner"],
            "name": r["name"],
            "html_url": f"https://github.com/{r['owner']}/{r['name']}",
        }
        for r in repos
    ]


@router.get("/{repo_id}/ci-status")
async def repo_ci_status(repo_id: int, request: Request):
    """Latest run conclusion per trigger type."""
    pool = request.app.state.pool

    rows = await pool.fetch(
        """
        SELECT DISTINCT ON (trigger)
            trigger, conclusion, created_at, run_url, workflow_name, duration_seconds
        FROM workflow_runs
        WHERE repo_id = $1 AND status = 'completed'
        ORDER BY trigger, created_at DESC
        """,
        repo_id,
    )
    return {
        r["trigger"]: {
            "conclusion": r["conclusion"],
            "created_at": r["created_at"],
            "run_url": r["run_url"],
            "workflow_name": r["workflow_name"],
            "duration_seconds": r["duration_seconds"],
        }
        for r in rows
    }


@router.get("/{repo_id}/ci-success-rate")
async def repo_ci_success_rate(repo_id: int, request: Request):
    """Success rates for 7d / 30d / 90d broken down by trigger."""
    pool = request.app.state.pool

    rows = await pool.fetch(
        """
        SELECT
            trigger,
            SUM(CASE WHEN created_at >= NOW() - INTERVAL '7 days'  AND conclusion IN ('success','failure','timed_out') THEN 1 ELSE 0 END) AS total_7d,
            SUM(CASE WHEN created_at >= NOW() - INTERVAL '7 days'  AND conclusion = 'success' THEN 1 ELSE 0 END) AS ok_7d,
            SUM(CASE WHEN created_at >= NOW() - INTERVAL '30 days' AND conclusion IN ('success','failure','timed_out') THEN 1 ELSE 0 END) AS total_30d,
            SUM(CASE WHEN created_at >= NOW() - INTERVAL '30 days' AND conclusion = 'success' THEN 1 ELSE 0 END) AS ok_30d,
            SUM(CASE WHEN created_at >= NOW() - INTERVAL '90 days' AND conclusion IN ('success','failure','timed_out') THEN 1 ELSE 0 END) AS total_90d,
            SUM(CASE WHEN created_at >= NOW() - INTERVAL '90 days' AND conclusion = 'success' THEN 1 ELSE 0 END) AS ok_90d
        FROM workflow_runs
        WHERE repo_id = $1 AND status = 'completed'
        GROUP BY trigger
        """,
        repo_id,
    )

    def _rate(ok, total):
        return round(ok / total * 100, 1) if total else None

    return {
        r["trigger"]: {
            "7d":  {"success_rate": _rate(r["ok_7d"],  r["total_7d"]),  "total": r["total_7d"]},
            "30d": {"success_rate": _rate(r["ok_30d"], r["total_30d"]), "total": r["total_30d"]},
            "90d": {"success_rate": _rate(r["ok_90d"], r["total_90d"]), "total": r["total_90d"]},
        }
        for r in rows
    }


@router.get("/{repo_id}/ci-duration")
async def repo_ci_duration(repo_id: int, request: Request):
    """Average CI duration in seconds for 7d / 30d / 90d by trigger."""
    pool = request.app.state.pool

    rows = await pool.fetch(
        """
        SELECT
            trigger,
            ROUND(AVG(duration_seconds) FILTER (WHERE created_at >= NOW() - INTERVAL '7 days'))  AS avg_7d,
            ROUND(AVG(duration_seconds) FILTER (WHERE created_at >= NOW() - INTERVAL '30 days')) AS avg_30d,
            ROUND(AVG(duration_seconds) FILTER (WHERE created_at >= NOW() - INTERVAL '90 days')) AS avg_90d
        FROM workflow_runs
        WHERE repo_id = $1 AND status = 'completed' AND duration_seconds IS NOT NULL
        GROUP BY trigger
        """,
        repo_id,
    )

    return {
        r["trigger"]: {
            "avg_seconds_7d":  r["avg_7d"],
            "avg_seconds_30d": r["avg_30d"],
            "avg_seconds_90d": r["avg_90d"],
        }
        for r in rows
    }
