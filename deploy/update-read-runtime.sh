#!/usr/bin/env bash
# Update the READ MCP Runtime to a package, wait for READY, re-sync the gateway target.
# Run with ADMIN credentials. Carries over every setting in baseline/read_mcp/runtime.json
# (the update call replaces unspecified settings, so all are restated).
#
#   Deploy v7:  ./deploy/update-read-runtime.sh
#   Roll back:  ./deploy/update-read-runtime.sh v6/cloudops_guardian_mcp_read_tools_v6_arm64_mcp1.zip
set -euo pipefail

RID=hackathon_cloudopsguardian_mcp_read_tools-__READ_RUNTIME_SUFFIX__
BUCKET=bedrock-agentcore-runtime-__ACCOUNT_ID__-us-east-1-__RUNTIME_BUCKET_SUFFIX__
PREFIX=${1:-v7/cloudops_guardian_mcp_read_tools_v7_arm64_mcp1.zip}
GW=hackathon-cloudopsguardian-gateway-__READ_GATEWAY_SUFFIX__
TARGET=__READ_TARGET_ID__
export AWS_DEFAULT_REGION=us-east-1

aws s3api head-object --bucket "$BUCKET" --key "$PREFIX" >/dev/null   # fail early if not uploaded

aws bedrock-agentcore-control update-agent-runtime \
  --agent-runtime-id "$RID" \
  --agent-runtime-artifact "{\"codeConfiguration\":{\"code\":{\"s3\":{\"bucket\":\"$BUCKET\",\"prefix\":\"$PREFIX\"}},\"runtime\":\"PYTHON_3_12\",\"entryPoint\":[\"main.py\"]}}" \
  --role-arn arn:aws:iam::__ACCOUNT_ID__:role/CloudOpsGuardian-MCPReadToolsRuntimeRole \
  --network-configuration '{"networkMode":"PUBLIC"}' \
  --protocol-configuration '{"serverProtocol":"MCP"}' \
  --lifecycle-configuration '{"idleRuntimeSessionTimeout":900,"maxLifetime":28800}' \
  --metadata-configuration '{"requireMMDSV2":true}' \
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

aws bedrock-agentcore-control synchronize-gateway-targets \
  --gateway-identifier "$GW" --target-id-list "$TARGET" --query 'targets[].[targetId,status]' --output text

echo "Done. Verify with tools/list (all 7 tools expected on v7)."
