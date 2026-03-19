#!/usr/bin/env bash
# Usage: ./scripts/logs.sh [service]
# Services: api, collector, frontend, postgres
set -euo pipefail
cd "$(dirname "$0")/.."
SERVICE=${1:-}
docker compose logs -f ${SERVICE}
