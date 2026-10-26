#!/usr/bin/env bash
# Deploy the inbound parser change (ADMIN / CloudShell). ONLY CloudOpsGuardian-InboundEmailProcessor changes.
#   bash deploy-parser.sh v1.1      parser V1.1 (accepts CG-<12 hex> case ids) + route output to the Orchestrator
#   bash deploy-parser.sh v1.0      roll back: original code AND remove the Orchestrator destination
# Needs: inbound-parser-v1.1.zip, inbound-parser-v1.0-baseline.zip, parser-invoke-orchestrator.json
# PREREQUISITE: the Orchestrator must already be on the entry-dispatcher build (deploy-orchestrator.sh).
set -euo pipefail
export AWS_DEFAULT_REGION=us-east-1
V=${1:-v1.1}
FN=CloudOpsGuardian-InboundEmailProcessor
ROLE=CloudOpsGuardian-InboundEmailProcessorRole
ORCH=arn:aws:lambda:us-east-1:__ACCOUNT_ID__:function:CloudOpsGuardian-Orchestrator

case "$V" in
  v1.1) ZIP=inbound-parser-v1.1.zip ;;
  v1.0) ZIP=inbound-parser-v1.0-baseline.zip ;;
  *) echo "usage: $0 [v1.1|v1.0]" >&2; exit 2 ;;
esac
[ -f "$ZIP" ] || { echo "missing $ZIP" >&2; exit 1; }

if [ "$V" = v1.1 ]; then
  aws lambda get-function --function-name CloudOpsGuardian-Orchestrator >/dev/null   # must exist
  aws iam put-role-policy --role-name "$ROLE" --policy-name CloudOpsGuardian-InvokeOrchestratorPolicy \
    --policy-document file://parser-invoke-orchestrator.json
  echo "waiting for IAM propagation..."; sleep 10
fi

aws lambda update-function-code --function-name "$FN" --zip-file "fileb://$ZIP" >/dev/null
aws lambda wait function-updated --function-name "$FN"

if [ "$V" = v1.1 ]; then
  aws lambda put-function-event-invoke-config --function-name "$FN" --maximum-retry-attempts 0 \
    --destination-config "{\"OnSuccess\":{\"Destination\":\"$ORCH\"}}" >/dev/null
  echo "destination: parser OnSuccess -> Orchestrator"
else
  aws lambda delete-function-event-invoke-config --function-name "$FN" 2>/dev/null || true
  echo "destination: removed"
fi
aws lambda get-function-configuration --function-name "$FN" --query '[LastModified,CodeSize,State,LastUpdateStatus]' --output text
aws lambda get-function-event-invoke-config --function-name "$FN" --query 'DestinationConfig' --output json 2>&1 | head -8
