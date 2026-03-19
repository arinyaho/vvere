import httpx
from datetime import datetime, timezone
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from config import GITHUB_API


class RateLimitError(Exception):
    pass


_headers: dict = {}


def set_token(token: str):
    """Set the GitHub token used for all API calls."""
    global _headers
    from config import get_headers
    _headers = get_headers(token)


@retry(
    retry=retry_if_exception_type((httpx.TransportError, httpx.TimeoutException)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
async def gh_get(client: httpx.AsyncClient, url: str, params: dict = None) -> dict | list:
    r = await client.get(url, params=params, headers=_headers, timeout=30)
    if r.status_code == 403 and "rate limit" in r.text.lower():
        raise RateLimitError("GitHub rate limit hit")
    r.raise_for_status()
    return r.json()


async def fetch_workflow_runs(
    client: httpx.AsyncClient,
    owner: str,
    repo: str,
    since: datetime | None,
    last_seen_id: int | None,
) -> list[dict]:
    """Fetch runs newer than `since`, stopping when we hit `last_seen_id`."""
    runs = []
    page = 1
    params = {"per_page": 100, "page": page}
    if since:
        params["created"] = f">={since.strftime('%Y-%m-%dT%H:%M:%SZ')}"

    while True:
        params["page"] = page
        data = await gh_get(client, f"{GITHUB_API}/repos/{owner}/{repo}/actions/runs", params)
        batch = data.get("workflow_runs", [])
        if not batch:
            break
        for run in batch:
            if last_seen_id and run["id"] <= last_seen_id:
                return runs
            runs.append(run)
        if len(batch) < 100:
            break
        page += 1

    return runs


async def fetch_pull_requests(
    client: httpx.AsyncClient,
    owner: str,
    repo: str,
    since: datetime | None,
) -> list[dict]:
    """Fetch PRs sorted by updated desc, stopping when updated_at < since."""
    prs = []
    page = 1
    while True:
        data = await gh_get(
            client,
            f"{GITHUB_API}/repos/{owner}/{repo}/pulls",
            {"state": "all", "sort": "updated", "direction": "desc", "per_page": 100, "page": page},
        )
        if not data:
            break
        for pr in data:
            updated = datetime.fromisoformat(pr["updated_at"].replace("Z", "+00:00"))
            if since and updated <= since:
                return prs
            prs.append(pr)
        if len(data) < 100:
            break
        page += 1

    return prs


async def fetch_branches(
    client: httpx.AsyncClient,
    owner: str,
    repo: str,
) -> list[dict]:
    branches = []
    page = 1
    while True:
        data = await gh_get(
            client,
            f"{GITHUB_API}/repos/{owner}/{repo}/branches",
            {"per_page": 100, "page": page},
        )
        if not data:
            break
        branches.extend(data)
        if len(data) < 100:
            break
        page += 1
    return branches


async def fetch_commit_date(
    client: httpx.AsyncClient,
    owner: str,
    repo: str,
    sha: str,
) -> datetime | None:
    try:
        data = await gh_get(client, f"{GITHUB_API}/repos/{owner}/{repo}/commits/{sha}")
        ts = data["commit"]["committer"]["date"]
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None
