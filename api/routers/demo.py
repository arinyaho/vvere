"""
Demo mode data provider.
When DEMO_MODE=true, injects realistic fake data so the dashboard
works without any GitHub connection.
"""
import os
import random
from datetime import datetime, timezone, timedelta

DEMO_MODE = os.environ.get("DEMO_MODE", "").lower() == "true"

# --- Fake repos ---
DEMO_REPOS = [
    {"id": 1, "owner": "acme", "name": "api-gateway", "html_url": "https://github.com/acme/api-gateway"},
    {"id": 2, "owner": "acme", "name": "web-app", "html_url": "https://github.com/acme/web-app"},
    {"id": 3, "owner": "acme", "name": "ml-pipeline", "html_url": "https://github.com/acme/ml-pipeline"},
    {"id": 4, "owner": "acme", "name": "infra-deploy", "html_url": "https://github.com/acme/infra-deploy"},
    {"id": 5, "owner": "acme", "name": "shared-libs", "html_url": "https://github.com/acme/shared-libs"},
]

_now = datetime.now(timezone.utc)
_authors = ["alice", "bob", "charlie", "dana", "eve"]


def _rand_dt(days_ago_max=14):
    return _now - timedelta(days=random.uniform(0, days_ago_max))


def demo_overview():
    return {
        "last_collected_at": (_now - timedelta(minutes=3)).isoformat(),
        "repo_count": len(DEMO_REPOS),
        "open_prs": 18,
        "velocity_per_day": 3.4,
        "sync": {"last_sync_at": (_now - timedelta(minutes=3)).isoformat(), "ok": True, "error": None},
    }


def demo_repos():
    return DEMO_REPOS


def demo_ci_status(repo_id: int):
    conclusions = ["success", "success", "success", "failure"]
    return {
        "push": {
            "conclusion": random.choice(conclusions),
            "created_at": _rand_dt(2).isoformat(),
            "workflow_name": "CI",
            "duration_seconds": random.randint(120, 900),
        },
        "pull_request": {
            "conclusion": random.choice(conclusions),
            "created_at": _rand_dt(1).isoformat(),
            "workflow_name": "PR Check",
            "duration_seconds": random.randint(60, 600),
        },
    }


def demo_success_rates(repo_id: int):
    def _rate():
        return round(random.uniform(55, 100), 1)
    return {
        "push": {"7d": {"success_rate": _rate(), "total": random.randint(5, 30)},
                 "30d": {"success_rate": _rate(), "total": random.randint(20, 100)},
                 "90d": {"success_rate": _rate(), "total": random.randint(50, 300)}},
        "pull_request": {"7d": {"success_rate": _rate(), "total": random.randint(10, 50)},
                         "30d": {"success_rate": _rate(), "total": random.randint(30, 150)},
                         "90d": {"success_rate": _rate(), "total": random.randint(80, 400)}},
    }


def demo_durations(repo_id: int):
    def _dur():
        return random.randint(120, 1200)
    return {
        "push": {"avg_seconds_7d": _dur(), "avg_seconds_30d": _dur(), "avg_seconds_90d": _dur()},
        "pull_request": {"avg_seconds_7d": _dur(), "avg_seconds_30d": _dur(), "avg_seconds_90d": _dur()},
    }


def demo_open_prs():
    prs = []
    titles = [
        "feat: add rate limiting to API",
        "fix: resolve race condition in cache",
        "refactor: extract auth middleware",
        "feat: add dark mode toggle",
        "fix: memory leak in worker pool",
        "chore: update CI config",
        "feat: add webhook retry logic",
    ]
    for i in range(18):
        repo = random.choice(DEMO_REPOS)
        created = _rand_dt(10)
        prs.append({
            "repo": f"{repo['owner']}/{repo['name']}",
            "number": 100 + i,
            "title": titles[i % len(titles)],
            "author": random.choice(_authors),
            "draft": random.random() < 0.15,
            "age_days": round((_now - created).total_seconds() / 86400, 1),
            "pr_url": f"{repo['html_url']}/pull/{100 + i}",
        })
    return sorted(prs, key=lambda p: -p["age_days"])


def demo_stale_branches():
    branches = []
    names = ["feature/old-experiment", "fix/legacy-bug", "refactor/unused-module",
             "chore/update-deps", "spike/new-idea", "feature/abandoned"]
    for i, name in enumerate(names):
        repo = DEMO_REPOS[i % len(DEMO_REPOS)]
        branches.append({
            "repo": f"{repo['owner']}/{repo['name']}",
            "branch": name,
            "stale_days": round(random.uniform(4, 30), 1),
        })
    return branches


def demo_staging():
    return [
        {
            "repo": "acme/api-gateway",
            "branch": "stage/v2.1",
            "age_days": 2.3,
            "ci_conclusion": "success",
        },
        {
            "repo": "acme/web-app",
            "branch": "stage/dark-mode",
            "age_days": 0.8,
            "ci_conclusion": "failure",
        },
    ]


def demo_snapshot():
    return {
        "generated_at": _now.isoformat(),
        "overview": demo_overview(),
        "repos": [
            {
                "repo": f"{r['owner']}/{r['name']}",
                "ci_status": demo_ci_status(r["id"]),
                "success_rates": demo_success_rates(r["id"]),
                "durations": demo_durations(r["id"]),
            }
            for r in DEMO_REPOS
        ],
        "open_prs": demo_open_prs(),
        "stale_branches": demo_stale_branches(),
        "staging": demo_staging(),
    }
