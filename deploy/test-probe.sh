#!/usr/bin/env bash
# READ-ONLY live check (ADMIN / CloudShell): what the pre-action check and verification see RIGHT NOW. Changes nothing.
#   bash test-probe.sh                       reboot-style probe
#   bash test-probe.sh <pid> <name>          terminate-style probe for a process, e.g.  bash test-probe.sh 1 systemd
set -euo pipefail
export AWS_DEFAULT_REGION=us-east-1
if [ -n "${1:-}" ]; then P="{\"probe\":true,\"capability\":\"terminate_process\",\"processId\":$1,\"processName\":\"$2\"}"
else P='{"probe":true,"capability":"reboot_demo_instance"}'; fi
aws lambda invoke --function-name CloudOpsGuardian-Orchestrator --cli-binary-format raw-in-base64-out --payload "$P" /tmp/probe.json >/dev/null
python3 -m json.tool /tmp/probe.json
