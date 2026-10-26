#!/usr/bin/env bash
# Create-or-update CloudOpsGuardian-VerificationWorkflow and its role (ADMIN / CloudShell). Idempotent. NEW resources only.
# Needs: VerificationWorkflow.asl.json, verification-workflow-trust.json, verification-workflow-policy.json
set -euo pipefail
export AWS_DEFAULT_REGION=us-east-1
ROLE=CloudOpsGuardian-VerificationWorkflowRole
NAME=CloudOpsGuardian-VerificationWorkflow
for f in VerificationWorkflow.asl.json verification-workflow-trust.json verification-workflow-policy.json; do
  [ -f "$f" ] || { echo "missing $f" >&2; exit 1; }
done
aws lambda get-function --function-name CloudOpsGuardian-Orchestrator >/dev/null     # target must exist

if aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then echo "role: exists"; NEW=0
else aws iam create-role --role-name "$ROLE" --assume-role-policy-document file://verification-workflow-trust.json >/dev/null; echo "role: created"; NEW=1; fi
aws iam put-role-policy --role-name "$ROLE" --policy-name CloudOpsGuardian-InvokeOrchestratorPolicy --policy-document file://verification-workflow-policy.json
[ "$NEW" = 1 ] && { echo "waiting for IAM propagation..."; sleep 15; }

ARN=$(aws stepfunctions list-state-machines --query "stateMachines[?name=='$NAME'].stateMachineArn" --output text)
if [ -n "$ARN" ]; then
  aws stepfunctions update-state-machine --state-machine-arn "$ARN" --definition file://VerificationWorkflow.asl.json >/dev/null; echo "state machine: updated"
else
  ARN=$(aws stepfunctions create-state-machine --name "$NAME" --type STANDARD --definition file://VerificationWorkflow.asl.json \
        --role-arn "arn:aws:iam::__ACCOUNT_ID__:role/$ROLE" --query stateMachineArn --output text); echo "state machine: created"
fi
echo "$ARN"
