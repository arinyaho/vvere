#!/usr/bin/env bash
# Usage: ./scripts/add-repo.sh <owner> <repo-name>
# Example: ./scripts/add-repo.sh my-org my-repo
set -euo pipefail
cd "$(dirname "$0")/.."

OWNER=${1:?"Usage: $0 <owner> <repo-name>"}
REPO=${2:?"Usage: $0 <owner> <repo-name>"}

docker compose exec postgres psql -U cicd -d cicd -c \
  "INSERT INTO repos (owner, name) VALUES ('${OWNER}', '${REPO}') ON CONFLICT DO NOTHING RETURNING id, owner, name;"
echo "Done. Collector will pick up ${OWNER}/${REPO} on next cycle."
