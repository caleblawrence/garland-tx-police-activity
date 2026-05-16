#!/usr/bin/env bash
# Refresh the weekly incidents data and rebuild the map.
#
# 1. Runs the agent crew (downloads PDF, extracts incidents, writes
#    extracted_incidents.json and TinyDB).
# 2. Runs the geo-analysis (geocodes each address, writes
#    incident-geo-analysis/dist/features.geojson + supporting HTML).
#
# Usage:
#   ./scripts/run-weekly.sh
#
# Env vars:
#   SKIP_CREW=1   Skip step 1 (reuse the existing extracted_incidents.json).
#   SKIP_GEO=1    Skip step 2.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AGENT_DIR="$REPO_ROOT/agent-solution/garland_tx_data_analysis"
GEO_DIR="$REPO_ROOT/incident-geo-analysis"
INCIDENTS_JSON="$AGENT_DIR/extracted_incidents.json"

if [[ "${SKIP_CREW:-0}" != "1" ]]; then
  echo "==> Running agent crew in $AGENT_DIR"
  cd "$AGENT_DIR"
  if ! command -v crewai >/dev/null 2>&1; then
    if [[ -x ".venv/bin/crewai" ]]; then
      .venv/bin/crewai run
    else
      echo "Error: crewai not on PATH and .venv/bin/crewai not found." >&2
      echo "Install with: cd $AGENT_DIR && uv sync" >&2
      exit 1
    fi
  else
    crewai run
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
