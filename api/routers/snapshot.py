"""
/api/snapshot  — full dashboard state as a single JSON blob.
Designed for agent consumption (Claude Code skills, CI bots, etc.)
"""
import os
from datetime import datetime, timezone
from fastapi import APIRouter, Request

STALE_DAYS = int(os.environ.get("STALE_DAYS", 3))

router = APIRouter()


@router.get("")
async def snapshot(request: Request):
    pool = request.app.state.pool

    # --- overview ---
    last_collected = await pool.fetchval(
        "SELECT MAX(last_fetched_at) FROM fetch_cursor"
    )
    repo_count = await pool.fetchval("SELECT COUNT(*) FROM repos")
    open_prs = await pool.fetchval(
        "SELECT COUNT(*) FROM pull_requests WHERE state='open'"
    )
    merged_7d = await pool.fetchval(
        "SELECT COUNT(*) FROM pull_requests WHERE merged_at >= NOW() - INTERVAL '7 days'"
    )
    velocity = round(merged_7d / 7, 1) if merged_7d else 0.0
    stale_count = await pool.fetchval(
        """
        SELECT COUNT(*) FROM branches
        WHERE last_commit_at < NOW() - ($1 || ' days')::INTERVAL
          AND name NOT IN ('main','master','develop','dev')
          AND is_staging = FALSE
        """,
        str(STALE_DAYS),
    )

    # --- repos + ci status + rates + durations ---
    repos = await pool.fetch("SELECT id, owner, name FROM repos ORDER BY name")
    repos_data = []
    for r in repos:
        rid = r["id"]

        ci_rows = await pool.fetch(
            """
            SELECT DISTINCT ON (trigger)
                trigger, conclusion, created_at, run_url, workflow_name, duration_seconds
            FROM workflow_runs
            WHERE repo_id = $1 AND status = 'completed'
            ORDER BY trigger, created_at DESC
            """,
            rid,
        )
        ci_status = {
            row["trigger"]: {
                "conclusion": row["conclusion"],
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                "workflow_name": row["workflow_name"],
                "duration_seconds": row["duration_seconds"],
            }
            for row in ci_rows
        }

        rate_rows = await pool.fetch(
            """
            SELECT trigger,
                SUM(CASE WHEN created_at >= NOW() - INTERVAL '7 days'  AND conclusion IN ('success','failure','timed_out') THEN 1 ELSE 0 END) AS t7,
                SUM(CASE WHEN created_at >= NOW() - INTERVAL '7 days'  AND conclusion = 'success' THEN 1 ELSE 0 END) AS o7,
                SUM(CASE WHEN created_at >= NOW() - INTERVAL '30 days' AND conclusion IN ('success','failure','timed_out') THEN 1 ELSE 0 END) AS t30,
                SUM(CASE WHEN created_at >= NOW() - INTERVAL '30 days' AND conclusion = 'success' THEN 1 ELSE 0 END) AS o30,
                SUM(CASE WHEN created_at >= NOW() - INTERVAL '90 days' AND conclusion IN ('success','failure','timed_out') THEN 1 ELSE 0 END) AS t90,
                SUM(CASE WHEN created_at >= NOW() - INTERVAL '90 days' AND conclusion = 'success' THEN 1 ELSE 0 END) AS o90
            FROM workflow_runs WHERE repo_id = $1 AND status = 'completed'
            GROUP BY trigger
            """,
            rid,
        )

        def _pct(ok, total):
            return round(ok / total * 100, 1) if total else None

        success_rates = {
            row["trigger"]: {
                "7d": _pct(row["o7"], row["t7"]),
                "30d": _pct(row["o30"], row["t30"]),
                "90d": _pct(row["o90"], row["t90"]),
            }
            for row in rate_rows
        }

        dur_rows = await pool.fetch(
            """
            SELECT trigger,
                ROUND(AVG(duration_seconds) FILTER (WHERE created_at >= NOW() - INTERVAL '7 days'))  AS d7,
                ROUND(AVG(duration_seconds) FILTER (WHERE created_at >= NOW() - INTERVAL '30 days')) AS d30,
                ROUND(AVG(duration_seconds) FILTER (WHERE created_at >= NOW() - INTERVAL '90 days')) AS d90
            FROM workflow_runs WHERE repo_id = $1 AND status = 'completed' AND duration_seconds IS NOT NULL
            GROUP BY trigger
            """,
            rid,
        )
        durations = {
            row["trigger"]: {
                "avg_seconds_7d": int(row["d7"]) if row["d7"] else None,
                "avg_seconds_30d": int(row["d30"]) if row["d30"] else None,
                "avg_seconds_90d": int(row["d90"]) if row["d90"] else None,
            }
            for row in dur_rows
        }

        repos_data.append({
            "repo": f"{r['owner']}/{r['name']}",
            "ci_status": ci_status,
            "success_rates": success_rates,
            "durations": durations,
        })

    # --- open PRs (top 50) ---
    pr_rows = await pool.fetch(
        """
               p.head_branch, p.base_branch, p.pr_url, p.created_at,
               EXTRACT(EPOCH FROM (NOW() - p.created_at)) / 86400 AS age_days,
               r.owner, r.name AS repo_name
        FROM pull_requests p
        JOIN repos r ON r.id = p.repo_id
        WHERE p.state = 'open'
        ORDER BY p.created_at ASC
        LIMIT 50
        """
    )
    open_prs_list = [
        {
            "repo": f"{row['owner']}/{row['repo_name']}",
            "number": row["number"],
            "title": row["title"],
            "author": row["author"],
            "draft": row["draft"],
            "age_days": round(row["age_days"], 1),
            "pr_url": row["pr_url"],
        }
        for row in pr_rows
    ]

    # --- stale branches (top 30) ---
    stale_rows = await pool.fetch(
        """
        SELECT b.name, b.last_commit_at,
               EXTRACT(EPOCH FROM (NOW() - b.last_commit_at)) / 86400 AS stale_days,
               r.owner, r.name AS repo_name
        FROM branches b
        JOIN repos r ON r.id = b.repo_id
        WHERE b.last_commit_at < NOW() - ($1 || ' days')::INTERVAL
          AND b.name NOT IN ('main','master','develop','dev')
          AND b.is_staging = FALSE
        ORDER BY b.last_commit_at ASC
        LIMIT 30
        """,
        str(STALE_DAYS),
    )
    stale_list = [
        {
            "repo": f"{row['owner']}/{row['repo_name']}",
            "branch": row["name"],
            "stale_days": round(float(row["stale_days"]), 1),
        }
        for row in stale_rows
    ]

    # --- staging branches ---
    staging_rows = await pool.fetch(
        """
        SELECT b.name, b.last_commit_at,
               EXTRACT(EPOCH FROM (NOW() - b.last_commit_at)) / 86400 AS age_days,
               r.id AS repo_id, r.owner, r.name AS repo_name
        FROM branches b
        JOIN repos r ON r.id = b.repo_id
        WHERE b.is_staging = TRUE
        ORDER BY b.last_commit_at DESC
        """
    )
    staging_list = []
    for row in staging_rows:
        run = await pool.fetchrow(
            """
            SELECT conclusion, run_url FROM workflow_runs
            WHERE repo_id = $1 AND branch = $2 AND trigger = 'push' AND status = 'completed'
            ORDER BY created_at DESC LIMIT 1
            """,
            row["repo_id"], row["name"],
        )
        staging_list.append({
            "repo": f"{row['owner']}/{row['repo_name']}",
            "branch": row["name"],
            "age_days": round(float(row["age_days"]), 1),
            "ci_conclusion": run["conclusion"] if run else None,
        })

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "overview": {
            "repos": repo_count,
            "open_prs": open_prs,
            "stale_branches": stale_count,
            "velocity_per_day": velocity,
            "last_collected_at": last_collected.isoformat() if last_collected else None,
        },
        "repos": repos_data,
        "open_prs": open_prs_list,
        "stale_branches": stale_list,
        "staging": staging_list,
    }
