#!/usr/bin/env bash
# Update the REASONING Runtime to a package, wait for READY. Run with ADMIN credentials.
# Restates every setting from baseline/reasoning_runtime/runtime.json (update replaces unspecified ones).
#
#   Deploy v2:  ./deploy/update-reasoning-runtime.sh
#   Roll back:  ./deploy/update-reasoning-runtime.sh 1792486486865-nt5ibw-cloudops_guardian_bootstrap.zip
set -euo pipefail

RID=hackathon_cloudopsguardin_runtime_agent-__REASONING_RUNTIME_SUFFIX__        # sic: deployed spelling
BUCKET=bedrock-agentcore-runtime-__ACCOUNT_ID__-us-east-1-__RUNTIME_BUCKET_SUFFIX__
PREFIX=${1:-reasoning/v2/cloudops_guardian_reasoning_v2_arm64.zip}
READ_GW_URL=https://hackathon-cloudopsguardian-gateway-__READ_GATEWAY_SUFFIX__.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp
export AWS_DEFAULT_REGION=us-east-1

aws s3api head-object --bucket "$BUCKET" --key "$PREFIX" >/dev/null   # fail early if not uploaded

aws bedrock-agentcore-control update-agent-runtime \
  --agent-runtime-id "$RID" \
  --agent-runtime-artifact "{\"codeConfiguration\":{\"code\":{\"s3\":{\"bucket\":\"$BUCKET\",\"prefix\":\"$PREFIX\"}},\"runtime\":\"PYTHON_3_12\",\"entryPoint\":[\"main.py\"]}}" \
  --role-arn arn:aws:iam::__ACCOUNT_ID__:role/CloudOpsGuardian-AgentCoreRuntimeRole \
  --network-configuration '{"networkMode":"PUBLIC"}' \
  --protocol-configuration '{"serverProtocol":"HTTP"}' \
  --lifecycle-configuration '{"idleRuntimeSessionTimeout":900,"maxLifetime":28800}' \
  --metadata-configuration '{"requireMMDSV2":true}' \
  --environment-variables "{\"READ_GATEWAY_MCP_URL\":\"$READ_GW_URL\",\"MODEL_ID\":\"us.anthropic.claude-sonnet-5-5\",\"MAX_TOOL_CALLS\":\"12\"}" \
  --query '[agentRuntimeVersion,status]' --output text

echo "Waiting for READY..."
for i in $(seq 1 60); do
  S=$(aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id "$RID" --query status --output text)
  echo "  $S"
  [ "$S" = READY ] && break
  case "$S" in *FAILED*) echo "Runtime update failed" >&2; exit 1;; esac
  sleep 10
done
[ "$S" = READY ] || { echo "Timed out waiting for READY" >&2; exit 1; }
echo "Done."
