#!/usr/bin/env bash
# Create-or-update the Orchestrator Lambda, its role/policy and the approval pepper. Run as ADMIN (CloudShell).
# First run creates everything; later runs update code, config and policy. Idempotent.
#
# Needs in the working directory (upload via CloudShell "Actions > Upload file"):
#   orchestrator-trust.json  orchestrator-policy.json     (from deploy/iam/)
# and the zip already in S3:  s3://$BUCKET/$KEY
#
# Execution is ON by default (approvals execute). To deploy with writes disabled (dry-run approvals only):
#   EXECUTION=off bash deploy-orchestrator.sh <key>
# The instant kill switch after deploying is:  bash set-execution.sh off
#   ./deploy-orchestrator.sh [S3_KEY] [HANDLER]
#     default key:     orchestrator/v1/cloudops_orchestrator_v1.zip
#     default handler: cloudops_orchestrator.handlers.entry.lambda_handler   (v3+)
#   Roll back to v2:   ./deploy-orchestrator.sh orchestrator/v2/cloudops_orchestrator_v2.zip cloudops_orchestrator.handlers.alarm_handler.lambda_handler
set -euo pipefail
export AWS_DEFAULT_REGION=us-east-1

FN=CloudOpsGuardian-Orchestrator
ROLE=CloudOpsGuardian-OrchestratorRole
BUCKET=bedrock-agentcore-runtime-__ACCOUNT_ID__-us-east-1-__RUNTIME_BUCKET_SUFFIX__
KEY=${1:-orchestrator/v1/cloudops_orchestrator_v1.zip}
HANDLER=${2:-cloudops_orchestrator.handlers.entry.lambda_handler}
PEPPER_PARAM=/cloudops-guardian/approval-token-pepper
ACCOUNT=__ACCOUNT_ID__

# Edit these two lines to change who gets mail / who may approve. Keep the lists separate.
OPERATORS="__APPROVER_EMAIL__,__OPERATOR_EMAIL_2__"
APPROVERS="__APPROVER_EMAIL__"

aws s3api head-object --bucket "$BUCKET" --key "$KEY" >/dev/null     # fail early if the zip is not uploaded
[ -f orchestrator-trust.json ] && [ -f orchestrator-policy.json ] || { echo "Upload the two policy JSON files first" >&2; exit 1; }

# 1. Approval-token pepper: generated here, stored encrypted, never printed. Created once; never overwritten.
if aws ssm get-parameter --name "$PEPPER_PARAM" >/dev/null 2>&1; then
  echo "pepper: exists (kept)"
else
  aws ssm put-parameter --name "$PEPPER_PARAM" --type SecureString --value "$(python3 -c 'import secrets;print(secrets.token_hex(32))')" >/dev/null
  echo "pepper: created"
fi

# 2. Role + policy
if aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then echo "role: exists"; NEW_ROLE=0
else aws iam create-role --role-name "$ROLE" --assume-role-policy-document file://orchestrator-trust.json >/dev/null; echo "role: created"; NEW_ROLE=1; fi
aws iam put-role-policy --role-name "$ROLE" --policy-name CloudOpsGuardian-OrchestratorPolicy --policy-document file://orchestrator-policy.json
[ "$NEW_ROLE" = 1 ] && { echo "waiting for role propagation..."; sleep 15; }

case "${EXECUTION:-on}" in on) EXEC=true ;; off) EXEC=false ;; *) echo "EXECUTION must be on or off" >&2; exit 2 ;; esac

ENV="Variables={CASE_TABLE_NAME=CloudOpsGuardian-CaseState,CLOUDOPS_OPERATOR_EMAILS=\"$OPERATORS\",CLOUDOPS_APPROVER_EMAILS=\"$APPROVERS\",\
APPROVAL_EXPIRY_MINUTES=30,EXECUTION_ENABLED=$EXEC,APPROVAL_TOKEN_PEPPER_PARAM=$PEPPER_PARAM,REQUIRE_SES_AUTH_PASS=true,SES_FROM_ADDRESS=__SES_FROM_ADDRESS__,\
INBOUND_EMAIL_BUCKET=__INBOUND_BUCKET__,DEMO_INSTANCE_ID=__DEMO_INSTANCE_ID__,\
REASONING_RUNTIME_ARN=arn:aws:bedrock-agentcore:us-east-1:$ACCOUNT:runtime/hackathon_cloudopsguardin_runtime_agent-__REASONING_RUNTIME_SUFFIX__,\
READ_GATEWAY_MCP_URL=https://hackathon-cloudopsguardian-gateway-__READ_GATEWAY_SUFFIX__.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp,\
ACTION_GATEWAY_MCP_URL=https://hackathon-cloudopsguardian-action-tools-gateway-__ACTION_GATEWAY_SUFFIX__.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp,\
VERIFICATION_WORKFLOW_ARN=arn:aws:states:us-east-1:$ACCOUNT:stateMachine:CloudOpsGuardian-VerificationWorkflow}"

# 3. Function
if aws lambda get-function --function-name "$FN" >/dev/null 2>&1; then
  aws lambda update-function-code --function-name "$FN" --s3-bucket "$BUCKET" --s3-key "$KEY" >/dev/null
  aws lambda wait function-updated --function-name "$FN"
  aws lambda update-function-configuration --function-name "$FN" --handler "$HANDLER" --timeout 300 --memory-size 512 --environment "$ENV" >/dev/null
  echo "function: updated"
else
  aws lambda create-function --function-name "$FN" --runtime python3.12 --architectures arm64 \
    --role "arn:aws:iam::$ACCOUNT:role/$ROLE" --handler "$HANDLER" \
    --code S3Bucket="$BUCKET",S3Key="$KEY" --timeout 300 --memory-size 512 --environment "$ENV" >/dev/null
  echo "function: created"
fi
# Inbound mail arrives as an ASYNC invoke: never auto-retry (a retry could repeat a state change or a mail).
aws lambda wait function-updated --function-name "$FN" 2>/dev/null || true
aws lambda put-function-event-invoke-config --function-name "$FN" --maximum-retry-attempts 0 >/dev/null
aws lambda wait function-active-v2 --function-name "$FN" 2>/dev/null || aws lambda wait function-updated --function-name "$FN"
aws lambda get-function-configuration --function-name "$FN" --query '[FunctionArn,State,LastUpdateStatus,Runtime,Handler]' --output text
echo "EXECUTION_ENABLED=$(aws lambda get-function-configuration --function-name "$FN" --query Environment.Variables.EXECUTION_ENABLED --output text)"
