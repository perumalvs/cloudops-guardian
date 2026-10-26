#!/usr/bin/env bash
# Controlled C5 test (ADMIN / CloudShell). Creates ONE real case via the existing IncidentHandler with a
# synthetic alarm event, then runs the Orchestrator on it. Sends REAL email to the configured operators.
set -euo pipefail
export AWS_DEFAULT_REGION=us-east-1
EID="$(python3 -c 'import uuid;print(uuid.uuid4())')"   # must be a UUID: the case id is derived from its hex
cat > /tmp/alarm.json <<J
{"id":"$EID","source":"aws.cloudwatch","detail-type":"CloudWatch Alarm State Change",
 "detail":{"alarmName":"CloudOpsGuardian-EC2CPUAlarm","state":{"value":"ALARM"}}}
J
aws lambda invoke --function-name CloudOpsGuardian-IncidentHandler --cli-binary-format raw-in-base64-out \
  --payload file:///tmp/alarm.json /tmp/case.json >/dev/null
echo "IncidentHandler -> $(cat /tmp/case.json)"
aws lambda invoke --function-name CloudOpsGuardian-Orchestrator --cli-binary-format raw-in-base64-out \
  --payload file:///tmp/case.json /tmp/orch.json | grep -E 'StatusCode|FunctionError' || true
echo "Orchestrator   -> $(cat /tmp/orch.json)"
