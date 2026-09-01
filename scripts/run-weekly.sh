#!/usr/bin/env bash
# Refresh the weekly incidents data and rebuild the map.
#
# 1. Runs the deep agent (downloads PDF, parses and reconciles the incidents,
#    labels any new offence code, writes enriched_incidents.json and the
#    Postgres history).
# 2. Runs the geo-analysis (geocodes each address, writes
#    incident-geo-analysis/dist/features.geojson + supporting HTML).
#
# Usage:
#   ./scripts/run-weekly.sh
#
# Env vars:
#   SKIP_AGENT=1  Skip step 1 (reuse the existing enriched_incidents.json).
#   SKIP_NEWS=1   Skip gathering news.
#   SKIP_GEO=1    Skip step 2.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AGENT_DIR="$REPO_ROOT/incident-ingest"
GEO_DIR="$REPO_ROOT/incident-geo-analysis"
# The map is built from the ENRICHED list. extracted_incidents.json carries no
# short_description, so building from it publishes raw offence codes —
# "THEFT-ALL OTHER-$2,500 L/T $30,000" where a reader should see "Theft". This
# script used to point stage 2 at exactly that file.
INCIDENTS_JSON="$AGENT_DIR/work/enriched_incidents.json"
EXTRACTED_JSON="$AGENT_DIR/work/extracted_incidents.json"
PARSE_SUMMARY="$AGENT_DIR/work/extracted_incidents.summary.json"

if [[ "${SKIP_AGENT:-0}" != "1" ]]; then
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
  echo "Error: $INCIDENTS_JSON not found." >&2
  if [[ -f "$EXTRACTED_JSON" ]]; then
    echo "  $(basename "$EXTRACTED_JSON") exists, but it carries no labels." >&2
    echo "  Building from it would put raw offence codes on the public map." >&2
    echo "  The agent writes the enriched file when it stores a week; if it" >&2
    echo "  refused the week, fix that rather than publishing without labels." >&2
  else
    echo "  Run without SKIP_AGENT=1." >&2
  fi
  exit 1
fi

# The agent leaves enriched_incidents.json behind from the previous week when a
# run refuses to store. Building anyway would silently republish the old week as
# though it were this one, which is the failure this pipeline is most prone to.
if [[ -f "$PARSE_SUMMARY" ]]; then
  if ! python3 - "$PARSE_SUMMARY" <<'PYCHECK'
import json, sys
summary = json.load(open(sys.argv[1]))
bad = summary.get("reconciliation", {}).get("discrepancies") or []
if bad:
    districts = ", ".join(str(d["district"]) for d in bad)
    print(f"  District(s) {districts} do not match their declared totals.",
          file=sys.stderr)
    sys.exit(1)
PYCHECK
  then
    echo "Error: the last parse did not reconcile, so nothing was stored." >&2
    echo "  Not rebuilding the map — it would republish the previous week." >&2
    exit 1
  fi

  if [[ "$EXTRACTED_JSON" -nt "$INCIDENTS_JSON" ]]; then
    echo "Error: $(basename "$EXTRACTED_JSON") is newer than the enriched list." >&2
    echo "  The last run parsed a week it never stored. Not rebuilding." >&2
    exit 1
  fi
fi

# News runs whether or not the agent stored a week. A bad PDF and a stale news
# pool are unrelated failures, and coupling them means one silently stops the
# other. Nothing here publishes: it fills a pool to feature from by hand.
if [[ "${SKIP_NEWS:-0}" != "1" ]]; then
  echo "==> Gathering news in $AGENT_DIR"
  cd "$AGENT_DIR"
  NEWS="python -m garland_tx_data_analysis.news_ingest"
  if [[ -x ".venv/bin/python" ]]; then
    .venv/bin/$NEWS --since "$(date -v-14d +%Y-%m-%d 2>/dev/null || date -d '14 days ago' +%Y-%m-%d)" --apply ||       echo "  news ingest failed; continuing" >&2
    .venv/bin/$NEWS --export || echo "  news export failed; continuing" >&2
  elif command -v uv >/dev/null 2>&1; then
    uv run $NEWS --since "$(date -v-14d +%Y-%m-%d 2>/dev/null || date -d '14 days ago' +%Y-%m-%d)" --apply ||       echo "  news ingest failed; continuing" >&2
    uv run $NEWS --export || echo "  news export failed; continuing" >&2
  fi
  cd "$REPO_ROOT"
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
