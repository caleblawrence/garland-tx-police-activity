#!/usr/bin/env bash
# Refresh the weekly incidents data and rebuild the map.
#
# 1. Runs the deep agent (downloads PDF, extracts incidents, audits the
#    extraction, writes extracted_incidents.json and the Postgres history).
# 2. Runs the geo-analysis (geocodes each address, writes
#    incident-geo-analysis/dist/features.geojson + supporting HTML).
#
# Usage:
#   ./scripts/run-weekly.sh
#
# Env vars:
#   SKIP_AGENT=1  Skip step 1 (reuse the existing extracted_incidents.json).
#                 SKIP_CREW=1 still works, for anything already scripted.
#   SKIP_GEO=1    Skip step 2.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AGENT_DIR="$REPO_ROOT/agent-solution/garland_tx_data_analysis"
GEO_DIR="$REPO_ROOT/incident-geo-analysis"
INCIDENTS_JSON="$AGENT_DIR/extracted_incidents.json"

if [[ "${SKIP_AGENT:-${SKIP_CREW:-0}}" != "1" ]]; then
  echo "==> Running deep agent in $AGENT_DIR"
  cd "$AGENT_DIR"
  if [[ -x ".venv/bin/run_agent" ]]; then
    .venv/bin/run_agent
  elif command -v uv >/dev/null 2>&1; then
    uv run run_agent
  else
    echo "Error: .venv/bin/run_agent not found and uv is not on PATH." >&2
    echo "Install with: cd $AGENT_DIR && uv sync" >&2
    exit 1
  fi
  cd "$REPO_ROOT"
fi

if [[ ! -f "$INCIDENTS_JSON" ]]; then
  echo "Error: $INCIDENTS_JSON not found. Run without SKIP_CREW=1." >&2
  exit 1
fi

if [[ "${SKIP_GEO:-0}" != "1" ]]; then
  echo "==> Running geo-analysis in $GEO_DIR"
  cd "$GEO_DIR"
  if [[ ! -d node_modules ]]; then
    echo "==> Installing node deps"
    npm install
  fi
  INCIDENTS_JSON_PATH="$INCIDENTS_JSON" node src/index.js
  cd "$REPO_ROOT"
fi

echo ""
echo "Done."
echo "  Incidents JSON: $INCIDENTS_JSON"
echo "  Map output:     $GEO_DIR/dist/index.html"
echo ""
echo "Serve the map with:"
echo "  cd $GEO_DIR && npm run serve"
