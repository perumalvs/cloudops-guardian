# CloudOps Guardian

*An AI agent that investigates your outage and asks permission by email to resolve it.*

**An incident-response agent that talks to you by email.** When an EC2 alarm fires, an AI agent investigates with governed, read-only tools, emails the on-call operator a diagnosis and a proposed fix, and then keeps the conversation going over Amazon SES until the operator approves, the fix runs, and recovery is verified.

## The problem

On-call alerts are one-way notifications. Someone gets "CPU > 80%", opens a laptop, logs in, hunts for the cause, and decides what to do. Chatbots that try to help are rule-based and cannot investigate, and giving an AI agent write access to production is a risk most teams will not take.

## What it does

1. **Detects.** A CloudWatch alarm on the demo EC2 instance triggers a Step Functions workflow, which opens a case (`CG-…`) in DynamoDB.
2. **Investigates.** An agent on Amazon Bedrock AgentCore decides which read-only tools to call (instance state, CPU history, alarm history, top processes, process details) through an AgentCore Gateway. It cannot change anything. It returns a diagnosis and a ranked list of allowed outcomes: terminate a process, reboot, keep monitoring, or escalate.
3. **Converses over email (Amazon SES).** The operator gets a plain-language email with the evidence, the risk, and the exact commands they can reply with:
   - `DETAILS` for more evidence
   - any free-text question, answered from the case evidence
   - `APPROVE PLAN-… <token>` to run the plan
   - `REJECT`, `PROPOSE <n>` to choose a different ranked option, or `ESCALATE` to hand over to a human

   The conversation can move through several rounds, and the case survives across replies because every message is parsed, matched to its case, and recorded.
4. **Acts, safely.** An approval only works if the sender is on an approver allow-list, the single-use token matches, SES authentication verdicts pass (DMARC, or SPF and DKIM), and a fresh re-check of the live instance still agrees with the plan. The action then goes through a second AgentCore Gateway whose Cedar policy engine permits exactly two tools. Each plan executes at most once.
5. **Verifies.** A Step Functions loop re-reads the instance until recovery is proven. The case is `RESOLVED` only when verification says so, not when the API call returns.

A read-only web dashboard (Lambda Function URL) shows cases, plans, evidence, and a merged timeline. It masks emails and drops tokens, and it has no approve button, because approvals stay on the email channel.

## How we use AWS Communication Developer Services

**Amazon SES is the conversation channel, in both directions.**
- *Outbound:* investigation results, plans, answers to follow-up questions, execution results, and escalations.
- *Inbound:* an SES receipt rule stores each reply in S3, which triggers a parser Lambda that classifies the intent and extracts the case and plan identifiers. The orchestrator then validates the sender and the SES spam, virus and authentication verdicts before acting.

Email is also the control plane: approvals, rejections, and follow-ups all arrive as SES messages, so no separate approval UI is needed.

## How it is built

| Layer | AWS services |
|---|---|
| Detect | CloudWatch alarm, EventBridge, Step Functions |
| Reason | Amazon Bedrock AgentCore Runtime (Strands agent on a Bedrock model) |
| Tools | AgentCore Gateway with a READ MCP server (7 tools) and a separate ACTION MCP server (2 tools) governed by Cedar |
| Orchestrate | Lambda (case state machine, plan builder, approval validator, executor), DynamoDB single table |
| Communicate | Amazon SES (send and receive), S3 |
| Verify | Step Functions loop with fresh READ checks |
| Observe | Read-only dashboard on a Lambda Function URL |

Design choices:
- **Read and write are separated.** The reasoning agent can reach only the READ gateway. Only the orchestrator can reach the ACTION gateway, and only after the approval checks.
- **Plans are immutable and bound to their exact parameters** by a hash, so an approval cannot be reused for a different action.
- **Everything is idempotent.** Inbound mail is delivered asynchronously with retries disabled, execution is claimed with a conditional write before the action call, and duplicate emails cannot repeat an action.
- **A kill switch** (`EXECUTION_ENABLED`) turns approvals into dry-runs instantly.
- **Secrets never reach the audit trail.** Tokens are stored only as hashes and values are redacted before they are logged.

## Challenges

- **Making email safe as a control channel.** Anyone can send mail, and "Re:" threads quote old approvals. We separate who receives incidents from who may approve, require a single-use token, check SES verdicts, and parse only the latest reply.
- **Keeping an LLM agent useful but harmless.** The model chooses which evidence to collect, but code enforces the boundary: allowed outcomes, tool allow-list, and a grounding check that a process to terminate was actually seen running in the evidence.
- **Not trusting a success response.** The action API can succeed while the problem persists, so verification, not the API result, decides the outcome.

## What we are proud of

- A full alarm → investigation → email conversation → approved action → verified resolution loop, run end to end against real SES mail and a real EC2 instance.
- A conservative safety model that an operations team could actually adopt: least privilege, two gateways, Cedar, execute-once, pre-action re-check, kill switch.
- 278 automated tests covering the case state machine, approval validation, executor, parser, reasoning guardrails, and READ tools.

## What we learned

Conversation is a better interface for operations than notification. Operators ask "why?" before they say "yes", and an agent that can answer from evidence, and propose alternatives, earns trust that a one-click button does not.

## What's next

- More channels on the same case model: SMS or RCS through AWS End User Messaging, and WhatsApp through End User Messaging Social, so an operator can approve from a phone.
- More remediation tools and resource types beyond the demo instance.
- Multi-account support and team routing.

## Try it

See the repository README for deployment steps. The dashboard URL and a short walkthrough of the email commands are listed in the submission.
