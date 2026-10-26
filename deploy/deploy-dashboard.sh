#!/usr/bin/env bash
# Create-or-update the read-only Dashboard Lambda + public Function URL. Run as ADMIN (CloudShell). Idempotent.
#
# Upload to CloudShell first ("Actions > Upload file"):
#   dashboard-trust.json  dashboard-policy.json  (from deploy/iam/)   cloudops_guardian_dashboard.zip (from dashboard/dist/, run deploy/build-dashboard.sh)
#
# The URL is public by design (judges / demo). The role can only Query/Scan the case table; the code never writes,
# masks emails and drops tokens, and concurrency is capped. Take it down any time:  bash deploy-dashboard.sh off
set -euo pipefail
export AWS_DEFAULT_REGION=us-east-1
FN=CloudOpsGuardian-Dashboard
ROLE=CloudOpsGuardian-DashboardRole
ACCOUNT=__ACCOUNT_ID__

if [ "${1:-}" = "off" ]; then
  aws lambda delete-function-url-config --function-name "$FN" && echo "function URL removed (Lambda kept)"; exit 0
fi

[ -f dashboard-trust.json ] && [ -f dashboard-policy.json ] && [ -f cloudops_guardian_dashboard.zip ] || { echo "Upload the two policy JSONs and the zip first" >&2; exit 1; }

if aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then echo "role: exists"; NEW_ROLE=0
else aws iam create-role --role-name "$ROLE" --assume-role-policy-document file://dashboard-trust.json >/dev/null; echo "role: created"; NEW_ROLE=1; fi
aws iam put-role-policy --role-name "$ROLE" --policy-name CloudOpsGuardian-DashboardPolicy --policy-document file://dashboard-policy.json
[ "$NEW_ROLE" = 1 ] && { echo "waiting for role propagation..."; sleep 15; }

ENV="Variables={CASE_TABLE_NAME=CloudOpsGuardian-CaseState}"
if aws lambda get-function --function-name "$FN" >/dev/null 2>&1; then
  aws lambda update-function-code --function-name "$FN" --zip-file fileb://cloudops_guardian_dashboard.zip >/dev/null
  aws lambda wait function-updated --function-name "$FN"
  aws lambda update-function-configuration --function-name "$FN" --timeout 15 --memory-size 256 --environment "$ENV" >/dev/null
  echo "function: updated"
else
  aws lambda create-function --function-name "$FN" --runtime python3.12 --architectures arm64 \
    --role "arn:aws:iam::$ACCOUNT:role/$ROLE" --handler lambda_function.lambda_handler \
    --zip-file fileb://cloudops_guardian_dashboard.zip --timeout 15 --memory-size 256 --environment "$ENV" >/dev/null
  echo "function: created"
fi
aws lambda wait function-updated --function-name "$FN"
aws lambda put-function-concurrency --function-name "$FN" --reserved-concurrent-executions 5 >/dev/null || echo "note: could not reserve concurrency (account limit); continuing"

# Public Function URL (no auth). Both permissions are needed for URLs created after Oct 2025.
if ! aws lambda get-function-url-config --function-name "$FN" >/dev/null 2>&1; then
  aws lambda create-function-url-config --function-name "$FN" --auth-type NONE >/dev/null
  aws lambda add-permission --function-name "$FN" --statement-id FunctionURLPublic --action lambda:InvokeFunctionUrl \
    --principal '*' --function-url-auth-type NONE >/dev/null
  aws lambda add-permission --function-name "$FN" --statement-id FunctionURLPublicInvoke --action lambda:InvokeFunction \
    --principal '*' --invoked-via-function-url >/dev/null
fi
echo "Dashboard URL: $(aws lambda get-function-url-config --function-name "$FN" --query FunctionUrl --output text)"
