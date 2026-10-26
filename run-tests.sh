#!/usr/bin/env bash
# Run every test suite. No AWS account or credentials needed: all AWS calls are mocked.
# Needs: uv (https://docs.astral.sh/uv/) and Python 3.12.   Usage: ./run-tests.sh
set -euo pipefail
cd "$(dirname "$0")"
run() {  # run <dir> [extra uv args...]
  local d=$1; shift
  echo "== $d"
  ( cd "$d" && uv run --project "$ROOT/orchestrator" --extra dev "$@" python -m pytest -q tests )
}
ROOT=$PWD
run orchestrator
run inbound_parser
run dashboard
run reasoning --with strands-agents --with bedrock-agentcore
run mcp_read --with "mcp<2"
echo "All suites passed."
