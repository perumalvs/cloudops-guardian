*An AI agent that investigates your outage and asks permission by email to resolve it.*

## Inspiration

The hackathon asked for conversational experiences that go beyond one-time passwords, notifications and rigid rule-based chatbots. We looked at the place where that gap is widest for us: **on-call operations**.

An alarm is a one-way message. "CPU > 80%" lands in an inbox, and a person has to open a laptop, log in, find the cause, decide what to do, and do it. A chatbot that follows rules can't investigate, and an AI agent with write access to production, without a human in the loop (HIL), is something most teams consider too risky.

We wanted the opposite: an agent that does the investigating, and a human who keeps the decision. The operator should get a **root-cause analysis with recommended resolution options**, not just an alert. They should not have to accept the agent's first suggestion. If the investigation isn't enough, they can ask for more detail (`DETAILS`) or ask a question in plain language. They can also choose a different recommended option (`PROPOSE <n>`) before they `APPROVE` anything. Email is the one channel every operator already has on every device, which made **Amazon SES** the natural place to hold that conversation.

## What it does

1. **Detects.** A CloudWatch alarm on an EC2 instance starts a Step Functions workflow that opens a case in DynamoDB.
2. **Investigates.** An agent on **Amazon Bedrock AgentCore** chooses which read-only tools to call, such as instance state, CPU history, alarm history and top processes. It cannot change anything. It returns a diagnosis and a ranked list of allowed outcomes.
3. **Converses over email.** The operator gets a plain-language email with the evidence, the risk and the commands they can reply with: `DETAILS`, any free-text question, `APPROVE PLAN-…`, `REJECT`, `PROPOSE <n>` or `ESCALATE`. The thread can go several rounds before anything happens.
4. **Keeps a human in the loop (HIL), then acts.** Nothing changes in production until the operator replies `APPROVE`. Approval triggers one governed action, and the operator can instead reject, pick a different option, or escalate to a person.
5. **Verifies.** A workflow re-reads the instance until recovery is proven. The case is resolved only when verification says so.

A read-only dashboard shows every case, plan, piece of evidence and a merged timeline. It deliberately has no approve button, because approval belongs on the email channel.

## How we built it

**AWS Communication Developer Services is the conversation channel in both directions.** Outbound SES carries investigations, plans, answers and results. Inbound SES receives replies into S3, where a parser Lambda classifies the intent and extracts the case and plan identifiers. The orchestrator then validates the sender and the SES spam, virus and authentication verdicts before it acts.

| Layer | What we used |
|---|---|
| Detect | CloudWatch alarm, EventBridge, Step Functions |
| Reason | Bedrock AgentCore Runtime running a Strands agent |
| Tools | Two AgentCore Gateways: a READ MCP server (7 tools) and a separate ACTION MCP server (2 tools) governed by a Cedar policy engine |
| Orchestrate | Lambda with a 14-state case machine, DynamoDB single-table design |
| Communicate | Amazon SES (send and receive), S3 |
| Verify | Step Functions loop with fresh READ checks |
| Observe | Read-only dashboard on a Lambda Function URL |

The safety model is the heart of the design. An approval executes only when every condition holds:

$$
\text{execute} \iff \underbrace{A}_{\text{approver on allow-list}} \wedge \underbrace{T}_{\text{single-use token valid}} \wedge \underbrace{S}_{\text{SES verdicts pass}} \wedge \underbrace{R}_{\text{live re-check agrees}} \wedge \underbrace{C}_{\text{Cedar permits the tool}}
$$

and the plan has not expired (30 minutes by default) and has not run before. Each plan is bound to its exact parameters by a hash, so an approval for one action can never authorise another. The executor claims each plan with a conditional write before calling the ACTION gateway, so a duplicate or retried email cannot run an action twice. A kill switch turns approvals into dry runs instantly.

## Challenges we ran into

- **Making email safe as a control channel.** Anyone can send mail, and replies quote old messages, including old approval commands. We keep the list of people who receive incidents separate from the list who may approve, require a single-use token, read the SES authentication verdicts, and parse only the newest reply.
- **Keeping an LLM useful but harmless.** The model decides what evidence to gather, but code sets the boundary: a fixed set of allowed outcomes, a tool allow-list, and a grounding check that a process proposed for termination was actually seen running in the evidence.
- **Not trusting a success response.** An API call can succeed while the problem remains. We made verification, not the action's return value, decide whether a case is resolved.
- **Duplicate and asynchronous delivery.** Inbound mail arrives as an asynchronous invoke, so a retry could repeat a state change or an email. We turned retries off for that path and made every write conditional.
- **Choosing the channel.** We wanted more than one way to reach an operator. RCS wasn't available for India, and SMS has character limits that can't carry an investigation summary plus a plan with its reply commands. Email can, and SES lets us receive replies as well as send, which is what turns a notification into a conversation.
- **Real mail is slow.** Delivery to a corporate mailbox can lag by many minutes, which shaped how we test: we verify each stage independently instead of waiting on a single end-to-end run.

## Accomplishments that we're proud of

- A complete loop run against real SES mail and a real EC2 instance: alarm, investigation, email conversation, approved action, verified resolution.
- A conservative design an operations team could adopt: separate read and write paths, Cedar-governed tools, execute-once, a pre-action re-check and a kill switch.
- 278 automated tests across the case state machine, approval validation, executor, parser, reasoning guardrails and READ tools.

## What we learned

- **Conversation beats notification for operations.** Operators ask "why?" before they say "yes". An agent that can answer from evidence, and offer alternatives, earns trust that a one-click button does not.
- **Constrain the model with code, not with prompts.** The agent is free to investigate, and the system, not the model, decides what it is allowed to do.
- **Treat every external signal as untrusted** until it is authenticated, matched to a case and checked against live state.
- **Idempotency is a feature, not a detail,** once email retries and asynchronous invokes are in the picture.

## What's next for CloudOps Guardian

Today one agent looks after one demo instance. The case model, the governed tools and the approval flow don't depend on that, so the next step is **fleet management for many kinds of infrastructure issues**:

- **From one instance to a fleet.** Cover many instances, accounts and regions, selected by tags and guarded by per-resource Cedar policies, with a fleet view in the dashboard.
- **Beyond CPU.** Add read and action tools for the issues operators actually get paged for: full disks, memory pressure, failed health checks, unhealthy load balancer targets, stuck deployments and expiring certificates. Each new tool joins the same READ or ACTION gateway and the same approval rules.
- **Fewer, better cases.** Correlate related alarms into one case, so a single failing dependency produces one conversation instead of twenty.
- **Team routing.** Send the conversation to the right on-call person and escalate on the same thread when nobody answers.

**More channels, later.** We looked at other End User Messaging channels and hit real limits. RCS isn't available for India yet, and we'd like to ask AWS to add it. SMS can't carry an investigation summary and a plan within its character limits. **WhatsApp** through AWS End User Messaging Social is the channel we want to explore next, so an operator can approve from a phone.
