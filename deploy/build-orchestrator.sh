#!/usr/bin/env bash
# Build the Orchestrator Lambda package (pure Python; boto3 comes from the Lambda runtime).
# Usage: deploy/build-orchestrator.sh [version]   -> orchestrator/dist/cloudops_orchestrator_<version>.zip
set -euo pipefail
cd "$(dirname "$0")/.."
V=${1:-v1}
OUT="orchestrator/dist/cloudops_orchestrator_${V}.zip"
mkdir -p orchestrator/dist && rm -f "$OUT"
( cd orchestrator/src && find cloudops_orchestrator -name '*.py' -print | sort | zip -q -X "../../$OUT" -@ )
echo "$OUT ($(unzip -Z1 "$OUT" | wc -l) files, $(stat -c %s "$OUT") bytes)"
