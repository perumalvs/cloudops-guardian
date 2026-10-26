#!/usr/bin/env bash
# C6 test (ADMIN / CloudShell): push a synthetic alarm event through the REAL workflow, wait, show the result.
# Creates one real case and sends REAL email to the configured operators.
set -euo pipefail
export AWS_DEFAULT_REGION=us-east-1
SM=arn:aws:states:us-east-1:__ACCOUNT_ID__:stateMachine:CloudOpsGuardian-IncidentWorkflow
EID="$(python3 -c 'import uuid;print(uuid.uuid4())')"
INPUT=$(cat <<J
{"id":"$EID","source":"aws.cloudwatch","detail-type":"CloudWatch Alarm State Change",
 "detail":{"alarmName":"CloudOpsGuardian-EC2CPUAlarm","state":{"value":"ALARM"}}}
J
)
EX=$(aws stepfunctions start-execution --state-machine-arn "$SM" --input "$INPUT" --query executionArn --output text)
echo "started: $EX"
for i in $(seq 1 60); do
  S=$(aws stepfunctions describe-execution --execution-arn "$EX" --query status --output text)
  echo "  $S"; [ "$S" != RUNNING ] && break; sleep 5
done
aws stepfunctions describe-execution --execution-arn "$EX" --query '[status,output,error,cause]' --output text
