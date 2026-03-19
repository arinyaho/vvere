#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  echo "ERROR: .env not found. Copy .env.example and fill in the values."
  exit 1
fi

docker compose up -d
echo "Dashboard: http://$(grep PUBLIC_URL .env | cut -d= -f2 | sed 's|http://||' | cut -d/ -f1)"
