#!/usr/bin/env bash
# Update CloudOpsGuardian-IncidentWorkflow (ADMIN / CloudShell).
#   bash update-workflow.sh v2     add the Investigate step (default)
#   bash update-workflow.sh v1     roll back to the original two-state definition
# Needs in the working directory: IncidentWorkflow.v2.asl.json, IncidentWorkflow.v1-baseline.asl.json,
# stepfunctions-invoke-orchestrator.json
set -euo pipefail
export AWS_DEFAULT_REGION=us-east-1
V=${1:-v2}
SM=arn:aws:states:us-east-1:__ACCOUNT_ID__:stateMachine:CloudOpsGuardian-IncidentWorkflow
ROLE=CloudOpsGuardian-StepFunctionsExecutionRole
case "$V" in
  v2) DEF=IncidentWorkflow.v2.asl.json ;;
  v1) DEF=IncidentWorkflow.v1-baseline.asl.json ;;
  *) echo "usage: $0 [v2|v1]" >&2; exit 2 ;;
esac
[ -f "$DEF" ] || { echo "missing $DEF" >&2; exit 1; }

# The role must be able to invoke the Orchestrator BEFORE the new definition can use it (kept on rollback; harmless).
aws iam put-role-policy --role-name "$ROLE" --policy-name CloudOpsGuardian-InvokeOrchestratorPolicy \
  --policy-document file://stepfunctions-invoke-orchestrator.json
[ "$V" = v2 ] && { echo "waiting for IAM propagation..."; sleep 10; }

aws stepfunctions validate-state-machine-definition --type STANDARD --definition "file://$DEF" --query result --output text
aws stepfunctions update-state-machine --state-machine-arn "$SM" --definition "file://$DEF" --query updateDate --output text
aws stepfunctions describe-state-machine --state-machine-arn "$SM" --query 'definition' --output text | python3 -c "
import json,sys; d=json.load(sys.stdin); print('states now:', list(d['States']))"
