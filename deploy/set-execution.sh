#!/usr/bin/env bash
# Turn WRITE execution on or off for the Orchestrator (ADMIN / CloudShell). Changes ONE variable; keeps all others.
#   bash set-execution.sh on     approvals now execute through the ACTION gateway
#   bash set-execution.sh off    approvals are validated only (dry-run). This is the kill switch.
set -euo pipefail
export AWS_DEFAULT_REGION=us-east-1
FN=CloudOpsGuardian-Orchestrator
case "${1:-}" in on) V=true ;; off) V=false ;; *) echo "usage: $0 on|off" >&2; exit 2 ;; esac
aws lambda get-function-configuration --function-name "$FN" --query Environment.Variables --output json > /tmp/env.json
python3 - "$V" <<'P'
import json, sys
env = json.load(open("/tmp/env.json"))
env["EXECUTION_ENABLED"] = sys.argv[1]
json.dump({"Variables": env}, open("/tmp/env.json", "w"))
P
aws lambda update-function-configuration --function-name "$FN" --environment file:///tmp/env.json >/dev/null
rm -f /tmp/env.json
aws lambda wait function-updated --function-name "$FN"
echo "EXECUTION_ENABLED=$(aws lambda get-function-configuration --function-name $FN --query Environment.Variables.EXECUTION_ENABLED --output text)"
