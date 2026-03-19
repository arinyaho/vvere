# vvere

Prescriptive CI/CD dashboard — tells your team what to fix, not just what's broken.

vvere tracks GitHub Actions CI health, PR velocity, stale branches, and merge trends across your repos. Instead of just showing metrics, it recommends the highest-ROI action to unblock your team.

## Quick Start

```bash
git clone https://github.com/arinyaho/vvere.git
cd vvere
cp .env.example .env
# Edit .env: set GITHUB_TOKEN, REPOS, POSTGRES_PASSWORD
docker compose up -d
```

Open `http://localhost:9339`

**Try without GitHub:** set `DEMO_MODE=true` in `.env` to explore with realistic fake data.

## Auth

vvere auto-detects auth mode from env vars (priority order):

| Mode | Env vars | Description |
|------|----------|-------------|
| **GitHub Device Flow** | _(default, no config)_ | Zero-config, like `gh auth login` |
| **GitHub Web Flow** | `GITHUB_CLIENT_ID` + `GITHUB_CLIENT_SECRET` | Standard OAuth redirect |
| **Google OAuth** | `GOOGLE_CLIENT_ID` + `GOOGLE_CLIENT_SECRET` | Backward compatible |
| **No auth** | `AUTH_DISABLED=true` | For trusted networks |

Authorization: set `GITHUB_ORG` (org membership check) or `GITHUB_USERS` (explicit allowlist).

## Configuration

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GITHUB_TOKEN` | yes* | - | PAT with repo + workflow read |
| `REPOS` | yes | - | Comma-separated `owner/repo` to track |
| `POSTGRES_PASSWORD` | yes | - | PostgreSQL password |
| `GITHUB_ORG` | no | - | GitHub org for login access control |
| `GITHUB_USERS` | no | - | Comma-separated GitHub usernames allowed |
| `ADMIN_USERS` | no | - | Comma-separated admin usernames |
| `DEMO_MODE` | no | false | Run with fake data (no GitHub needed) |
| `AUTH_DISABLED` | no | false | Disable authentication |
| `RETENTION_DAYS` | no | 90 | Auto-prune data older than this |
| `COLLECT_INTERVAL_SECONDS` | no | 300 | GitHub fetch interval (seconds) |
| `STALE_DAYS` | no | 3 | Days without commit = stale branch |

\* Not required if using the setup wizard or `DEMO_MODE=true`.

## API

All data is available as JSON:

```
GET /api/snapshot          # Full dashboard state in one call
GET /api/overview          # Summary stats + sync health
GET /api/repos             # Repo list
GET /api/repos/:id/ci-status
GET /api/repos/:id/ci-success-rate
GET /api/repos/:id/ci-duration
GET /api/prs/open
GET /api/prs/summary
GET /api/branches/stale
GET /api/branches/staging
GET /api/setup/status      # Check if setup wizard is needed
```

## Features

- Per-repo CI status (latest run per trigger type)
- Success rates and avg CI duration: 7d / 30d / 90d
- Open PR summary: count, avg age, oldest PR
- Staging branch status with CI result
- Stale branch detection: paginated, sortable
- Merge velocity (merges/day, 7d)
- Collector health monitoring (last sync status)
- Data retention (auto-prune old records)
- GitHub API rate limit handling
- Demo mode for evaluation
- JSON snapshot API for agents and automation

## Architecture

```
Browser (:9339)
    |
    v
nginx (frontend)
    |
    +-- /api/* --> FastAPI (api)
    |                  |
    |              PostgreSQL
    |                  ^
    +-- static     Collector (cron + on-demand)
                       |
                   GitHub API
```

| Service | Role |
|---------|------|
| `frontend` | nginx serving static dashboard + proxying /api/* |
| `api` | FastAPI: data queries + auth (GitHub/Google/disabled) |
| `collector` | Incremental GitHub fetch, runs every 5 min |
| `postgres` | Persistent storage for runs, PRs, branches |

## Managing Repos

```bash
./scripts/add-repo.sh my-org my-repo
./scripts/remove-repo.sh old-repo-name
```

## License

MIT
