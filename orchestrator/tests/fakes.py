"""Shared fakes: every external dependency of the orchestrator, scripted per test."""
import json
from email import message_from_bytes, policy

from cloudops_orchestrator.clients._gateway import GatewayDenied, GatewayError, GatewayTransportError, ToolError
from cloudops_orchestrator.clients.reasoning_runtime import validate_reasoning_response
from cloudops_orchestrator.clients.ses_outbound import SesOutbound
from cloudops_orchestrator.config import Config
from cloudops_orchestrator.plan_builder import build_plan
from cloudops_orchestrator.services import Services
from cloudops_orchestrator.states import CaseState as S

CASE = "CG-52650C02BEB8"
INSTANCE = "__DEMO_INSTANCE_ID__"
PLAN1 = f"PLAN-{CASE[3:]}V1"
GOOD = {"spam": "PASS", "virus": "PASS", "spf": "PASS", "dkim": "PASS", "dmarc": "PASS"}
HEADER = "    PID    PPID USER     COMMAND         %CPU %MEM"


def cfg(**kw):
    base = dict(operator_emails=("op@x.com", "boss@x.com", "boss2@x.com"), approver_emails=("boss@x.com", "boss2@x.com"),
                token_pepper="pepper", demo_instance_id=INSTANCE, inbound_bucket="b", execution_enabled=True,
                verification_workflow_arn="arn:sfn", verification_max_attempts=3)
    base.update(kw)
    return Config(**base)


def row(pid, comm, cpu):
    return f"{pid:7d}    1234 ec2-user {comm:<14}{cpu:5.1f}  0.1"


def listing(*rows):
    return "\n".join([HEADER, *rows])


class FakeRead:
    """Defaults describe the incident still happening: yes pid 4242 hot, alarm ALARM, instance healthy."""
    def __init__(self, **over):
        self.data = dict(
            describe={"instanceId": INSTANCE, "state": "running", "tags": [{"Key": "AgentRemediation", "Value": "Enabled"}]},
            status={"instanceState": "running", "systemStatus": "ok", "instanceStatus": "ok"},
            alarm={"stateValue": "ALARM", "threshold": 80.0},
            cpu={"datapoints": [{"Timestamp": "2026-10-22 15:27:00+00:00", "Average": 95.0}]},
            process={"status": "FOUND", "processId": 4242, "output": listing(row(4242, "yes", 99.0))},
            top={"output": listing(row(4242, "yes", 99.0), row(1, "systemd", 0.0))},
        )
        self.data.update(over)
        self.calls = []

    def _get(self, key, name):
        self.calls.append(name)
        v = self.data[key]
        if isinstance(v, Exception):
            raise v
        return v(self) if callable(v) else v

    def describe_instance(self): return self._get("describe", "describe_instance")
    def instance_status(self): return self._get("status", "instance_status")
    def alarm(self): return self._get("alarm", "alarm")
    def cpu_metrics(self, lookback_hours=1): return self._get("cpu", "cpu_metrics")
    def process_details(self, pid): return self._get("process", f"process_details({pid})")
    def top_processes(self): return self._get("top", "top_processes")


def recovered(**over):
    """The world after a successful kill: process gone, alarm OK, CPU low."""
    d = dict(alarm={"stateValue": "OK", "threshold": 80.0}, cpu={"datapoints": [{"Timestamp": "t", "Average": 3.0}]},
             process={"status": "NOT_FOUND", "processId": 4242}, top={"output": listing(row(1, "systemd", 0.0))})
    d.update(over)
    return FakeRead(**d)


class FakeAction:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.plans = result or {"status": "SUCCESS", "verificationRequired": True}, error, []

    def execute_plan(self, plan):
        self.plans.append(plan)
        if self.error:
            raise self.error
        return self.result


class FakeSfn:
    def __init__(self, error=None):
        self.started, self.error = [], error

    def start_execution(self, **kw):
        if self.error:
            raise self.error
        self.started.append(kw)
        return {"executionArn": "arn:exec"}


class FakeSes:
    def __init__(self, fail_first=False):
        self.sent, self.fail_first = [], fail_first

    def send_raw_email(self, **kw):
        if self.fail_first and not self.sent and not getattr(self, "_failed", False):
            self._failed = True
            raise RuntimeError("ses hiccup")
        self.sent.append(kw)
        return {"MessageId": f"ses-{len(self.sent)}"}

    def mails(self):
        """(destinations, headers + decoded body): decodes quoted-printable wrapping like a real mail client."""
        out = []
        for m in self.sent:
            msg = message_from_bytes(m["RawMessage"]["Data"], policy=policy.default)
            out.append((m["Destinations"], "\n".join(f"{k}: {v}" for k, v in msg.items()) + "\n\n" + msg.get_content()))
        return out


class FakeReasoning:
    def __init__(self, raw=None, error=None):
        self.raw, self.error, self.calls = raw, error, []

    def invoke(self, mode, case_id, **ctx):
        self.calls.append((mode, case_id, ctx))
        if self.error:
            raise self.error
        return validate_reasoning_response(self.raw)


CALM = {"diagnosis": {"summary": "normal", "confidence": "HIGH"}, "evidence": [], "outcomes": [{"rank": 1, "capability": "CONTINUE_MONITORING"}],
        "answer": "calm"}
KILL5000 = {"diagnosis": {"summary": "another yes", "confidence": "HIGH"}, "evidence": [],
            "outcomes": [{"rank": 1, "capability": "terminate_process", "parameters": {"process_id": 5000, "expected_process_name": "yes"}}]}


def services(repos, read=None, action=None, sfn=None, reasoning=None, ses=None, config=None):
    ses = ses or FakeSes()
    svc = Services(config or cfg(), repos, reasoning or FakeReasoning(CALM), SesOutbound(config or cfg(), client=ses),
                   read=read or FakeRead(), action=action or FakeAction(), sfn=sfn or FakeSfn())
    return svc, ses


def make_plan(repos, config=None, capability="terminate_process", pid=4242):
    config = config or cfg()
    for s in (S.INVESTIGATING, S.DIAGNOSED):
        repos.case.transition(CASE, s)
    params = {"process_id": pid, "expected_process_name": "yes"} if capability == "terminate_process" else {}
    plan, token = build_plan(repos, config, CASE, INSTANCE, capability, params, [], "yes is hot", "stops yes", "low", "fresh READ")
    repos.case.transition(CASE, S.AWAITING_APPROVAL)
    return plan, token


def approve_msg(plan, sender="boss@x.com", mid="<a1>"):
    return {"message_id": mid, "from": [sender], "subject": f"Re: [{CASE}]", "references": [], "body_text": "",
            "classification": {"intent": "APPROVAL", "case_id": CASE, "plan_id": plan["planId"], "approval_token_present": True}}
