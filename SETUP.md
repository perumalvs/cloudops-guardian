# Setup guide

Region: **us-east-1**. Use a sandbox account. Nothing here has been deployed from scratch into a clean account by an automated pipeline: Part A is a manual checklist (the foundation was built in the console and its settings are captured under `foundation/`), and Part B is scripted. A CloudFormation or CDK template for Part A is future work.

## Prerequisites

- AWS CLI v2 with an admin identity (AWS CloudShell works well), Python 3.12, `uv`, `zip`.
- **Amazon SES**: a verified domain you can receive mail on (or verified individual addresses while in the SES sandbox). Request production access if you want to mail unverified recipients.
- **Amazon Bedrock model access** for the model in `reasoning/main.py` (`MODEL_ID`, default `us.anthropic.claude-sonnet-5-5`; override with the `MODEL_ID` environment variable).
- A demo **EC2 instance** (the demo used a t2.medium) with the SSM agent, tagged `AgentRemediation=Enabled`.
- A CloudWatch alarm named `CloudOpsGuardian-EC2CPUAlarm` on that instance (CPUUtilization > 80%, 5-minute average).

## Part A: foundation (one time, manual)

Create these with the names shown, because the scripts and policies refer to them. The captured settings in `foundation/` are references for the exact IAM policies, Cedar statements and documents.

1. **DynamoDB** table `CloudOpsGuardian-CaseState`: partition key `PK` (S), sort key `SK` (S), on-demand. See `foundation/dynamodb/table.json`.
2. **SSM documents**: `CloudOpsGuardian-GetTopCPUProcesses` from `deploy/ssm/` and `CloudOpsGuardian-TerminateProcess` from `foundation/ssm/`.
3. **IAM roles** from `foundation/iam/` (trust policy plus the listed managed and inline policies): `CloudOpsGuardian-WorkflowLambdaRole`, `CloudOpsGuardian-StepFunctionsExecutionRole`, `CloudOpsGuardian-AgentCoreRuntimeRole`, `CloudOpsGuardian-AgentCoreGatewayRole`, and the READ and ACTION runtime roles. The READ policy is `foundation/iam/HostDiagnosticReadPolicy.json`.
4. **IncidentHandler Lambda** `CloudOpsGuardian-IncidentHandler` from `foundation/incident_handler/` (Python 3.12), using the workflow Lambda role.
5. **Step Functions** `CloudOpsGuardian-IncidentWorkflow` from `deploy/stepfunctions/IncidentWorkflow.v1-baseline.asl.json`, and the EventBridge rule `CloudOpsGuardian-IncidentRule` with the pattern in `foundation/events/event_rule.json`, targeting the state machine.
6. **AgentCore** (Bedrock AgentCore console or CLI). First run `python3 render.py` with at least `ACCOUNT_ID` and `DEMO_INSTANCE_ID` set in `config.env` (unset values are reported and can be filled in later), and **package the two MCP servers from the rendered copies**, because their `main.py` holds your instance ID:
   - READ MCP runtime from `rendered/mcp_read/` (arm64, Python 3.12, `mcp<2`), behind a gateway `hackathon-cloudopsguardian-gateway` (IAM auth) with the runtime as a target.
   - ACTION MCP runtime from `rendered/foundation/action_mcp/`, behind a second gateway `hackathon-cloudopsguardian-action-tools-gateway` (IAM auth) with a **policy engine** attached in enforce mode and the two Cedar policies from `foundation/cedar/`.
   - Reasoning runtime from `reasoning/` (arm64, Python 3.12, with `strands-agents` and `bedrock-agentcore`).
7. **Inbound mail**: an S3 bucket (`INBOUND_BUCKET` in your config), an SES receipt rule on your domain that stores mail in it, an S3 event notification to a Lambda `CloudOpsGuardian-InboundEmailProcessor` (code in `inbound_parser/`).
8. Put the IDs AgentCore assigned (gateway, runtime and policy-engine suffixes, runtime bucket suffix, READ target id) into `config.env`.

## Part B: deploy (scripted)

```bash
python3 render.py                 # re-run after filling in the IDs from Part A; writes rendered/ with your values filled in
cd rendered/deploy
```

Upload the files each script lists in its header to CloudShell, then run in this order:

1. `build-orchestrator.sh v1` and upload the zip to the runtime bucket; `bash deploy-orchestrator.sh` (add `EXECUTION=off` for dry-run approvals). Edit the `OPERATORS` and `APPROVERS` lines first.
2. `bash update-workflow.sh v2` (adds the Investigate step).
3. `bash deploy-parser.sh v1.1`.
4. `bash create-verification-workflow.sh`.
5. `bash update-read-runtime.sh` and `bash update-reasoning-runtime.sh` (after uploading the packages).
6. `bash build-dashboard.sh`, then `bash deploy-dashboard.sh`. It prints the dashboard URL. `bash deploy-dashboard.sh off` removes the public URL.

Kill switch at any time: `bash set-execution.sh off`.

## Try it

1. Check what the system can see, without changing anything: `bash test-probe.sh`.
2. Create a controlled case: `bash test-workflow.sh` (sends real email to your operators).
3. For the full loop, put real load on the instance (for example two `yes > /dev/null &` processes on a 2-vCPU instance) until the alarm fires. Email delivery can lag by a few minutes.
4. Reply to the plan email: `DETAILS`, a question, or `APPROVE PLAN-… <token>` as written in the mail. Watch the case move through the states in the dashboard.

## Troubleshooting

- **No mail arrives**: check the SES identity and sandbox status, and the Lambda logs for `ses:SendRawEmail` errors.
- **Approval refused**: the sender must be in `APPROVERS`, the SES verdicts must pass (`REQUIRE_SES_AUTH_PASS`), and the plan must not be expired (`APPROVAL_EXPIRY_MINUTES`, default 30).
- **Nothing executes**: check `EXECUTION_ENABLED` on the Orchestrator and that the instance has the `AgentRemediation=Enabled` tag.
