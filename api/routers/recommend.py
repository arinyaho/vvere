"""
/api/recommend  — Prescriptive recommendation engine.

Layer 1: Rule-based alerts (deterministic, always-on)
Layer 2: LLM insight (Claude API, daily cadence)

Recommendations are cached in DB to avoid redundant LLM calls.
"""
import os
import json
import logging
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

log = logging.getLogger("recommend")
router = APIRouter()

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "claude-sonnet-4-6")
STALE_DAYS = int(os.environ.get("STALE_DAYS", 3))


# ---------------------------------------------------------------------------
# Layer 1: Rule-based alerts
# ---------------------------------------------------------------------------

async def _rule_alerts(pool) -> list[dict]:
    """Hard-threshold alerts that are universally true."""
    alerts = []

    # CI critical: any repo with success rate < 50% in 7 days
    rows = await pool.fetch(
        """
        SELECT r.owner, r.name,
            COUNT(*) FILTER (WHERE w.conclusion IN ('success','failure','timed_out')) AS total,
            COUNT(*) FILTER (WHERE w.conclusion = 'success') AS ok
        FROM workflow_runs w
        JOIN repos r ON r.id = w.repo_id
        WHERE w.status = 'completed'
          AND w.created_at >= NOW() - INTERVAL '7 days'
        GROUP BY r.id, r.owner, r.name
        HAVING COUNT(*) FILTER (WHERE w.conclusion IN ('success','failure','timed_out')) >= 3
        """
    )
    for row in rows:
        rate = round(row["ok"] / row["total"] * 100, 1) if row["total"] else 100
        if rate < 50:
            alerts.append({
                "level": "critical",
                "signal": f"{row['owner']}/{row['name']} CI success rate is {rate}% (7d, {row['total']} runs)",
                "action": "Freeze new features on this repo. Fix CI first.",
                "to_be": "Green CI baseline (>90%) before new work",
            })

    # Stale branches > 30
    stale_count = await pool.fetchval(
        """
        SELECT COUNT(*) FROM branches
        WHERE last_commit_at < NOW() - ($1 || ' days')::INTERVAL
          AND name NOT IN ('main','master','develop','dev')
          AND is_staging = FALSE
        """,
        str(STALE_DAYS),
    )
    if stale_count and stale_count > 30:
        alerts.append({
            "level": "warning",
            "signal": f"{stale_count} stale branches (no commit in {STALE_DAYS}+ days)",
            "action": "Run a branch cleanup session. Delete merged/abandoned branches.",
            "to_be": "Branches reflect active work only",
        })

    # Sync health
    sync = await pool.fetchrow("SELECT last_sync_at, last_sync_ok FROM sync_status WHERE id = 1")
    if sync and not sync["last_sync_ok"]:
        alerts.append({
            "level": "critical",
            "signal": "Data collection is failing. Last successful sync unknown.",
            "action": "Check collector logs. Verify GitHub token is valid.",
            "to_be": "Collector syncing every 5 minutes",
        })
    elif sync and sync["last_sync_at"]:
        age = datetime.now(timezone.utc) - sync["last_sync_at"].replace(tzinfo=timezone.utc)
        if age > timedelta(minutes=30):
            alerts.append({
                "level": "warning",
                "signal": f"Last sync was {int(age.total_seconds() / 60)} minutes ago",
                "action": "Check if collector container is running.",
                "to_be": "Sync within last 10 minutes",
            })

    return alerts


# ---------------------------------------------------------------------------
# Layer 2: LLM insight
# ---------------------------------------------------------------------------

async def _get_snapshot_data(pool) -> dict:
    """Build a compact metrics summary for LLM context."""
    # Overview
    repo_count = await pool.fetchval("SELECT COUNT(*) FROM repos")
    open_prs = await pool.fetchval("SELECT COUNT(*) FROM pull_requests WHERE state='open'")
    merged_7d = await pool.fetchval(
        "SELECT COUNT(*) FROM pull_requests WHERE merged_at >= NOW() - INTERVAL '7 days'"
    )
    velocity = round(merged_7d / 7, 1) if merged_7d else 0.0

    # Per-repo CI success rates (7d)
    ci_rows = await pool.fetch(
        """
        SELECT r.owner || '/' || r.name AS repo,
            COUNT(*) FILTER (WHERE w.conclusion IN ('success','failure','timed_out')) AS total,
            COUNT(*) FILTER (WHERE w.conclusion = 'success') AS ok
        FROM workflow_runs w
        JOIN repos r ON r.id = w.repo_id
        WHERE w.status = 'completed' AND w.created_at >= NOW() - INTERVAL '7 days'
        GROUP BY r.id, r.owner, r.name
        """
    )
    ci_rates = {
        row["repo"]: f"{round(row['ok']/row['total']*100)}%" if row["total"] else "no data"
        for row in ci_rows
    }

    # PR age stats
    pr_stats = await pool.fetchrow(
        """
        SELECT
            ROUND(AVG(EXTRACT(EPOCH FROM (NOW() - created_at)) / 86400), 1) AS avg_age,
            MAX(EXTRACT(EPOCH FROM (NOW() - created_at)) / 86400) AS max_age
        FROM pull_requests WHERE state = 'open'
        """
    )

    # Top PR authors by open count
    author_rows = await pool.fetch(
        """
        SELECT author, COUNT(*) AS cnt
        FROM pull_requests WHERE state = 'open'
        GROUP BY author ORDER BY cnt DESC LIMIT 5
        """
    )

    # Reviewer concentration (who gets the most review requests)
    reviewer_rows = await pool.fetch(
        """
        SELECT r.value::text AS reviewer, COUNT(*) AS cnt
        FROM pull_requests p,
             jsonb_array_elements(p.requested_reviewers) AS r(value)
        WHERE p.state = 'open'
        GROUP BY r.value::text ORDER BY cnt DESC LIMIT 5
        """
    )

    stale_count = await pool.fetchval(
        """
        SELECT COUNT(*) FROM branches
        WHERE last_commit_at < NOW() - ($1 || ' days')::INTERVAL
          AND name NOT IN ('main','master','develop','dev')
          AND is_staging = FALSE
        """,
        str(STALE_DAYS),
    )

    return {
        "repos": repo_count,
        "open_prs": open_prs,
        "velocity_per_day_7d": velocity,
        "ci_success_rates_7d": ci_rates,
        "pr_avg_age_days": float(pr_stats["avg_age"]) if pr_stats and pr_stats["avg_age"] else None,
        "pr_max_age_days": round(float(pr_stats["max_age"]), 1) if pr_stats and pr_stats["max_age"] else None,
        "top_pr_authors": {r["author"]: r["cnt"] for r in author_rows},
        "top_requested_reviewers": {r["reviewer"]: r["cnt"] for r in reviewer_rows},
        "stale_branches": stale_count,
    }


async def _llm_recommend(metrics: dict) -> dict | None:
    """Call Claude API to generate one prescriptive recommendation."""
    if not ANTHROPIC_API_KEY:
        return None

    try:
        import httpx
        async with httpx.AsyncClient() as client:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": LLM_MODEL,
                    "max_tokens": 512,
                    "messages": [{
                        "role": "user",
                        "content": f"""You are a CI/CD productivity advisor. Analyze this team's metrics
and identify the single highest-ROI bottleneck to fix RIGHT NOW.

Metrics:
{json.dumps(metrics, indent=2, default=str)}

Rules:
- Be specific. Use actual numbers from the data.
- Recommend ONE action, not a list.
- If metrics look healthy, say so — don't invent problems.

Respond in this exact JSON format (no markdown, no explanation):
{{
  "signal": "what the data shows (with specific numbers)",
  "action": "one specific, actionable step",
  "to_be": "measurable success criteria"
}}

If all metrics look healthy, respond:
{{
  "signal": "All metrics within healthy range",
  "action": "No action needed — maintain current pace",
  "to_be": "Continue monitoring"
}}"""
                    }],
                },
                timeout=30,
            )
            if r.status_code != 200:
                log.error("LLM API error: %d %s", r.status_code, r.text[:200])
                return None

            data = r.json()
            text = data["content"][0]["text"].strip()
            return json.loads(text)

    except Exception as e:
        log.error("LLM recommendation failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Cache: store last LLM recommendation in DB
# ---------------------------------------------------------------------------

async def _get_cached_llm(pool) -> dict | None:
    """Get cached LLM recommendation if fresh (< 6 hours)."""
    row = await pool.fetchrow(
        """
        SELECT recommendation, generated_at FROM recommendations_cache
        WHERE id = 1 AND generated_at > NOW() - INTERVAL '6 hours'
        """
    )
    if row and row["recommendation"]:
        return json.loads(row["recommendation"])
    return None


async def _cache_llm(pool, rec: dict):
    """Cache LLM recommendation."""
    await pool.execute(
        """
        INSERT INTO recommendations_cache (id, recommendation, generated_at)
        VALUES (1, $1, NOW())
        ON CONFLICT (id) DO UPDATE
          SET recommendation = EXCLUDED.recommendation,
              generated_at = EXCLUDED.generated_at
        """,
        json.dumps(rec),
    )


# ---------------------------------------------------------------------------
# API endpoint
# ---------------------------------------------------------------------------

@router.get("")
async def get_recommendations(request: Request, refresh: bool = False):
    """Return current recommendations: rule alerts + LLM insight.

    ?refresh=true forces a new LLM call (ignores cache).
    """
    pool = request.app.state.pool

    # Layer 1: rules (always fresh)
    rule_alerts = await _rule_alerts(pool)

    # Layer 2: LLM (cached unless refresh requested)
    llm_rec = None
    if ANTHROPIC_API_KEY:
        if not refresh:
            llm_rec = await _get_cached_llm(pool)

        if not llm_rec:
            metrics = await _get_snapshot_data(pool)
            llm_rec = await _llm_recommend(metrics)
            if llm_rec:
                await _cache_llm(pool, llm_rec)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "alerts": rule_alerts,
        "insight": llm_rec,
        "llm_available": bool(ANTHROPIC_API_KEY),
    }
