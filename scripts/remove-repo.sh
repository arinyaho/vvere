#!/usr/bin/env bash
# Usage: ./scripts/remove-repo.sh <repo-name>
# Example: ./scripts/remove-repo.sh es2-core
set -euo pipefail
cd "$(dirname "$0")/.."

REPO=${1:?"Usage: $0 <repo-name>"}

docker compose exec postgres psql -U cicd -d cicd -c "
DO \$\$
DECLARE r_id INT;
BEGIN
  SELECT id INTO r_id FROM repos WHERE name = '${REPO}';
  IF r_id IS NULL THEN
    RAISE EXCEPTION 'Repo not found: ${REPO}';
  END IF;
  DELETE FROM fetch_cursor    WHERE repo_id = r_id;
  DELETE FROM branches        WHERE repo_id = r_id;
  DELETE FROM pull_requests   WHERE repo_id = r_id;
  DELETE FROM workflow_runs   WHERE repo_id = r_id;
  DELETE FROM repos           WHERE id = r_id;
  RAISE NOTICE 'Removed % and all associated data.', '${REPO}';
END \$\$;
"
