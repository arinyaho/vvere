"""
CI/CD Dashboard Collector
Incrementally fetches GitHub Actions runs, PRs, and branches into PostgreSQL.
Runs on a fixed interval (COLLECT_INTERVAL_SECONDS) and also exposes a
manual trigger via SIGUSR1.
"""

import asyncio
import logging
import pathlib
from datetime import datetime, timezone

import asyncpg
import httpx

from config import (
    DATABASE_URL, COLLECT_INTERVAL, STALE_DAYS, RETENTION_DAYS,
    INITIAL_FETCH_DAYS, STAGING_PREFIX, SEED_REPOS,
    GITHUB_TOKEN_ENV,
)
from github_client import (
    RateLimitError, fetch_workflow_runs, fetch_pull_requests,
    fetch_branches, fetch_commit_date, set_token,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("collector")

TRIGGER_FILE = pathlib.Path("/trigger/refresh")


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def get_repos(pool: asyncpg.Pool) -> list[dict]:
    rows = await pool.fetch("SELECT id, owner, name FROM repos ORDER BY id")
    return [dict(r) for r in rows]


async def get_cursor(pool: asyncpg.Pool, repo_id: int, data_type: str) -> dict:
    row = await pool.fetchrow(
        "SELECT last_fetched_at, last_seen_id FROM fetch_cursor WHERE repo_id=$1 AND data_type=$2",
        repo_id, data_type,
    )
    if row:
        return dict(row)
    # First run: use INITIAL_FETCH_DAYS as the lookback window
    from datetime import timedelta
    initial_since = datetime.now(timezone.utc) - timedelta(days=INITIAL_FETCH_DAYS)
    log.info("No cursor for repo_id=%d/%s — backfilling %d days", repo_id, data_type, INITIAL_FETCH_DAYS)
    return {"last_fetched_at": initial_since, "last_seen_id": None}


async def set_cursor(pool: asyncpg.Pool, repo_id: int, data_type: str, fetched_at: datetime, last_seen_id: int | None = None):
    await pool.execute(
        """
        INSERT INTO fetch_cursor (repo_id, data_type, last_fetched_at, last_seen_id)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (repo_id, data_type) DO UPDATE
          SET last_fetched_at = EXCLUDED.last_fetched_at,
              last_seen_id    = COALESCE(EXCLUDED.last_seen_id, fetch_cursor.last_seen_id)
        """,
        repo_id, data_type, fetched_at, last_seen_id,
    )


# ---------------------------------------------------------------------------
# Collectors
# ---------------------------------------------------------------------------

def _classify_trigger(event: str) -> str:
    mapping = {
        "push": "push",
        "pull_request": "pull_request",
        "schedule": "schedule",
        "workflow_dispatch": "workflow_dispatch",
        "repository_dispatch": "workflow_dispatch",
    }
    return mapping.get(event, event)


async def collect_runs(pool: asyncpg.Pool, client: httpx.AsyncClient, repo: dict):
    cursor = await get_cursor(pool, repo["id"], "runs")
    since = cursor["last_fetched_at"]
    last_seen = cursor["last_seen_id"]
    now = datetime.now(timezone.utc)

    try:
        runs = await fetch_workflow_runs(client, repo["owner"], repo["name"], since, last_seen)
    except RateLimitError:
        log.warning("Rate limit hit for %s/%s runs — skipping", repo["owner"], repo["name"])
        return
    except Exception as e:
        log.error("Failed to fetch runs for %s/%s: %s", repo["owner"], repo["name"], e)
        return

    if not runs:
        await set_cursor(pool, repo["id"], "runs", now, None)
        return

    rows = []
    for r in runs:
        created = datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))
        updated = datetime.fromisoformat(r["updated_at"].replace("Z", "+00:00"))
        duration = None
        if r.get("run_started_at") and r.get("updated_at") and r["status"] == "completed":
            started = datetime.fromisoformat(r["run_started_at"].replace("Z", "+00:00"))
            duration = int((updated - started).total_seconds())

        rows.append((
            r["id"],
            repo["id"],
            r["workflow_id"],
            r["name"],
            _classify_trigger(r["event"]),
            r.get("head_branch"),
            r["status"],
            r.get("conclusion"),
            created,
            updated,
            duration,
            r["html_url"],
        ))

    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO workflow_runs
              (id, repo_id, workflow_id, workflow_name, trigger, branch, status, conclusion,
               created_at, updated_at, duration_seconds, run_url)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
            ON CONFLICT (id) DO UPDATE
              SET status=EXCLUDED.status, conclusion=EXCLUDED.conclusion,
                  updated_at=EXCLUDED.updated_at, duration_seconds=EXCLUDED.duration_seconds
            """,
            rows,
        )

    max_id = max(r[0] for r in rows)
    await set_cursor(pool, repo["id"], "runs", now, max_id)
    log.info("%s/%s: upserted %d runs", repo["owner"], repo["name"], len(runs))


async def collect_prs(pool: asyncpg.Pool, client: httpx.AsyncClient, repo: dict):
    cursor = await get_cursor(pool, repo["id"], "prs")
    since = cursor["last_fetched_at"]
    now = datetime.now(timezone.utc)

    try:
        prs = await fetch_pull_requests(client, repo["owner"], repo["name"], since)
    except RateLimitError:
        log.warning("Rate limit hit for %s/%s prs — skipping", repo["owner"], repo["name"])
        return
    except Exception as e:
        log.error("Failed to fetch prs for %s/%s: %s", repo["owner"], repo["name"], e)
        return

    if not prs:
        await set_cursor(pool, repo["id"], "prs", now)
        return

    import json
    rows = []
    for p in prs:
        reviewers = json.dumps([r["login"] for r in p.get("requested_reviewers", [])])
        rows.append((
            p["id"],
            repo["id"],
            p["number"],
            p["title"],
            p["user"]["login"],
            False,  # is_automated (unused, kept for schema compat)
            p["state"],
            p.get("merged_at") is not None,
            p.get("draft", False),
            p["head"]["ref"],
            p["base"]["ref"],
            datetime.fromisoformat(p["created_at"].replace("Z", "+00:00")),
            datetime.fromisoformat(p["updated_at"].replace("Z", "+00:00")),
            datetime.fromisoformat(p["merged_at"].replace("Z", "+00:00")) if p.get("merged_at") else None,
            datetime.fromisoformat(p["closed_at"].replace("Z", "+00:00")) if p.get("closed_at") else None,
            p["html_url"],
            reviewers,
        ))

    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO pull_requests
              (id, repo_id, number, title, author, is_automated, state, is_merged, draft,
               head_branch, base_branch, created_at, updated_at, merged_at, closed_at, pr_url,
               requested_reviewers)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
            ON CONFLICT (id) DO UPDATE
              SET state=EXCLUDED.state, is_merged=EXCLUDED.is_merged,
                  updated_at=EXCLUDED.updated_at, merged_at=EXCLUDED.merged_at,
                  closed_at=EXCLUDED.closed_at, title=EXCLUDED.title,
                  requested_reviewers=EXCLUDED.requested_reviewers
            """,
            rows,
        )

    await set_cursor(pool, repo["id"], "prs", now)
    log.info("%s/%s: upserted %d PRs", repo["owner"], repo["name"], len(prs))


async def collect_branches(pool: asyncpg.Pool, client: httpx.AsyncClient, repo: dict):
    now = datetime.now(timezone.utc)
    try:
        branches = await fetch_branches(client, repo["owner"], repo["name"])
    except Exception as e:
        log.error("Failed to fetch branches for %s/%s: %s", repo["owner"], repo["name"], e)
        return

    # Fetch commit dates concurrently (limit concurrency)
    sem = asyncio.Semaphore(5)

    async def _with_date(b):
        async with sem:
            sha = b["commit"]["sha"]
            dt = await fetch_commit_date(client, repo["owner"], repo["name"], sha)
            return b["name"], sha, dt

    results = await asyncio.gather(*[_with_date(b) for b in branches])

    rows = []
    for name, sha, commit_at in results:
        rows.append((
            repo["id"],
            name,
            commit_at,
            sha,
            name.startswith(STAGING_PREFIX),
            now,
        ))

    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO branches (repo_id, name, last_commit_at, last_commit_sha, is_staging, fetched_at)
            VALUES ($1,$2,$3,$4,$5,$6)
            ON CONFLICT (repo_id, name) DO UPDATE
              SET last_commit_at=EXCLUDED.last_commit_at,
                  last_commit_sha=EXCLUDED.last_commit_sha,
                  is_staging=EXCLUDED.is_staging,
                  fetched_at=EXCLUDED.fetched_at
            """,
            rows,
        )

    # Delete branches that no longer exist on remote
    current_names = [r[1] for r in rows]
    await pool.execute(
        "DELETE FROM branches WHERE repo_id=$1 AND name != ALL($2::text[])",
        repo["id"], current_names,
    )

    await set_cursor(pool, repo["id"], "branches", now)
    log.info("%s/%s: upserted %d branches", repo["owner"], repo["name"], len(branches))


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

async def prune_old_data(pool: asyncpg.Pool):
    """Delete data older than RETENTION_DAYS to prevent unbounded DB growth."""
    interval = f"{RETENTION_DAYS} days"
    deleted_runs = await pool.fetchval(
        f"WITH d AS (DELETE FROM workflow_runs WHERE created_at < NOW() - INTERVAL '{interval}' RETURNING 1) SELECT COUNT(*) FROM d"
    ) or 0
    deleted_prs = await pool.fetchval(
        f"WITH d AS (DELETE FROM pull_requests WHERE updated_at < NOW() - INTERVAL '{interval}' AND state = 'closed' RETURNING 1) SELECT COUNT(*) FROM d"
    ) or 0
    if deleted_runs or deleted_prs:
        log.info("Retention prune: removed %d runs, %d closed PRs older than %d days",
                 deleted_runs, deleted_prs, RETENTION_DAYS)


async def update_sync_status(pool: asyncpg.Pool, ok: bool, error: str | None = None):
    """Update the singleton sync_status row for dashboard health display."""
    await pool.execute(
        """
        UPDATE sync_status
        SET last_sync_at = NOW(), last_sync_ok = $1, last_error = $2
        WHERE id = 1
        """,
        ok, error,
    )


async def log_rate_limit(client: httpx.AsyncClient):
    """Log GitHub API rate limit remaining after a collection cycle."""
    from github_client import _headers
    try:
        r = await client.get("https://api.github.com/rate_limit", headers=_headers, timeout=10)
        if r.status_code == 200:
            core = r.json().get("resources", {}).get("core", {})
            remaining = core.get("remaining", "?")
            limit = core.get("limit", "?")
            log.info("GitHub API rate limit: %s/%s remaining", remaining, limit)
            return remaining if isinstance(remaining, int) else 5000
    except Exception:
        pass
    return 5000


async def run_collection(pool: asyncpg.Pool):
    # Re-read config from DB each cycle (picks up new repos from setup/admin)
    global INITIAL_FETCH_DAYS
    db_days = await pool.fetchval(
        "SELECT initial_fetch_days FROM admin_config WHERE id = 1 AND setup_complete = TRUE"
    )
    if db_days and db_days != INITIAL_FETCH_DAYS:
        INITIAL_FETCH_DAYS = db_days
        log.info("Initial fetch days updated from DB: %d", INITIAL_FETCH_DAYS)

    # Seed repos from env (idempotent)
    await seed_repos(pool)

    repos = await get_repos(pool)
    log.info("Starting collection for %d repos", len(repos))
    error_msg = None

    try:
        async with httpx.AsyncClient() as client:
            remaining = await log_rate_limit(client)

            for repo in repos:
                if remaining < 100:
                    log.warning("Rate limit < 100, skipping remaining repos")
                    break

                skip_branches = remaining < 500
                if skip_branches:
                    log.warning("Rate limit < 500, skipping branch collection for %s/%s",
                                repo["owner"], repo["name"])

                log.info("Collecting %s/%s ...", repo["owner"], repo["name"])
                await asyncio.gather(
                    collect_runs(pool, client, repo),
                    collect_prs(pool, client, repo),
                )

                if not skip_branches:
                    await collect_branches(pool, client, repo)

                remaining = await log_rate_limit(client)

        # Prune old data after successful collection
        await prune_old_data(pool)
        await update_sync_status(pool, ok=True)
        log.info("Collection complete")

    except Exception as e:
        error_msg = str(e)[:500]
        log.error("Collection failed: %s", error_msg)
        await update_sync_status(pool, ok=False, error=error_msg)


def _check_trigger() -> bool:
    """Return True and remove the trigger file if it exists."""
    if TRIGGER_FILE.exists():
        try:
            TRIGGER_FILE.unlink()
        except FileNotFoundError:
            pass
        return True
    return False


async def seed_repos(pool: asyncpg.Pool):
    """Insert repos from REPOS env var if not already present."""
    if not SEED_REPOS:
        return
    for owner, name in SEED_REPOS:
        await pool.execute(
            "INSERT INTO repos (owner, name) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            owner, name,
        )
        log.info("Seeded repo %s/%s", owner, name)


async def resolve_token(pool: asyncpg.Pool) -> str | None:
    """Resolve GitHub token: env var first, then DB admin_config.

    Priority:
    1. GITHUB_TOKEN env var -> use directly
    2. DB admin_config.github_token -> use stored token
    3. Neither -> return None (collector waits)
    """
    if GITHUB_TOKEN_ENV:
        return GITHUB_TOKEN_ENV

    row = await pool.fetchrow(
        "SELECT github_token FROM admin_config WHERE id = 1 AND setup_complete = TRUE"
    )
    if row and row["github_token"]:
        return row["github_token"]

    return None


async def main():
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=5)
    log.info("DB connected. Collection interval: %ds", COLLECT_INTERVAL)

    # Resolve token (wait if neither env var nor DB token available)
    token = await resolve_token(pool)
    while not token:
        log.warning("No GitHub token available (env or DB). Waiting for /setup wizard...")
        await asyncio.sleep(10)
        token = await resolve_token(pool)

    set_token(token)
    log.info("GitHub token resolved (%s)", "env" if GITHUB_TOKEN_ENV else "db")

    # Run immediately on start
    await run_collection(pool)

    elapsed = 0
    poll = 2  # seconds between trigger-file checks
    while True:
        await asyncio.sleep(poll)
        elapsed += poll
        if _check_trigger():
            log.info("Manual refresh triggered via trigger file")
            elapsed = 0
            # Re-resolve token in case admin refreshed it
            new_token = await resolve_token(pool)
            if new_token and new_token != token:
                set_token(new_token)
                log.info("GitHub token refreshed from DB")
            await run_collection(pool)
        elif elapsed >= COLLECT_INTERVAL:
            log.info("Scheduled collection starting")
            elapsed = 0
            new_token = await resolve_token(pool)
            if new_token and new_token != token:
                set_token(new_token)
                log.info("GitHub token refreshed from DB")
            await run_collection(pool)


if __name__ == "__main__":
    asyncio.run(main())
