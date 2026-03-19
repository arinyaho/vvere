import os

DATABASE_URL = os.environ["DATABASE_URL"]
COLLECT_INTERVAL = int(os.environ.get("COLLECT_INTERVAL_SECONDS", 300))
STALE_DAYS = int(os.environ.get("STALE_DAYS", 3))
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", 90))
INITIAL_FETCH_DAYS = int(os.environ.get("INITIAL_FETCH_DAYS", 90))

GITHUB_API = "https://api.github.com"

# Token is resolved at runtime: env var first, then DB.
# Do NOT access GITHUB_TOKEN or HEADERS at import time.
GITHUB_TOKEN_ENV = os.environ.get("GITHUB_TOKEN", "")


def get_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


# Branches exempt from stale detection
STALE_EXEMPT_BRANCHES = {"main", "master", "develop", "dev"}
STAGING_PREFIX = "stage/"

# Repos to seed on startup: comma-separated "owner/repo" pairs.
# Example: REPOS=my-org/service-a,my-org/service-b
_repos_raw = os.environ.get("REPOS", "")
SEED_REPOS: list[tuple[str, str]] = [
    (parts[0], parts[1])
    for r in _repos_raw.split(",")
    if r.strip() and "/" in r.strip()
    for parts in [r.strip().split("/", 1)]
]
