import json

import pytest
from conftest import CASE
from cloudops_orchestrator.clients.reasoning_runtime import validate_reasoning_response
from cloudops_orchestrator.clients.ses_outbound import SesOutbound
from cloudops_orchestrator.config import Config
from cloudops_orchestrator.handlers.alarm_handler import handle
from cloudops_orchestrator.services import Services, load_pepper
from cloudops_orchestrator.states import CaseState as S

INSTANCE = "__DEMO_INSTANCE_ID__"
CFG = Config(operator_emails=("op@x.com", "boss@x.com"), approver_emails=("boss@x.com",), token_pepper="pepper",
             demo_instance_id=INSTANCE)


class FakeReasoning:
    def __init__(self, raw=None, error=None):
        self.raw, self.error, self.calls = raw, error, []

    def invoke(self, mode, case_id, **ctx):
        self.calls.append((mode, case_id, ctx))
        if self.error:
            raise self.error
        return validate_reasoning_response(self.raw)


class FakeSes:
    def __init__(self):
        self.sent = []

    def send_raw_email(self, **kw):
        self.sent.append(kw)
        return {"MessageId": f"ses-{len(self.sent)}"}

    def bodies(self):
        return [(m["Destinations"], m["RawMessage"]["Data"].decode()) for m in self.sent]


def services(repos, reasoning):
    ses_client = FakeSes()
    return Services(CFG, repos, reasoning, SesOutbound(CFG, client=ses_client)), ses_client


YES = {"diagnosis": {"summary": "yes is using 99% CPU", "root_cause": "runaway yes", "confidence": "HIGH",
                     "process_id": 4242, "process_name": "yes"},
       "evidence": [{"tool": "get_top_cpu_processes", "arguments": {}, "summary": "top CPU: yes(pid 4242) 99.0%", "data": {"output": "..."}}],
       "outcomes": [{"rank": 1, "capability": "terminate_process", "parameters": {"process_id": 4242, "expected_process_name": "yes"},
                     "rationale": "culprit", "impact": "stops yes", "risk": "low"},
                    {"rank": 2, "capability": "reboot_demo_instance", "rationale": "fallback", "risk": "disruptive"}]}
CALM = {"diagnosis": {"summary": "CPU is normal", "confidence": "HIGH"},
        "evidence": [{"tool": "describe_cpu_alarm", "summary": "alarm OK", "data": {}}],
        "outcomes": [{"rank": 1, "capability": "CONTINUE_MONITORING"}, {"rank": 2, "capability": "reboot_demo_instance"}]}
EVENT = {"caseId": CASE, "status": "OPEN", "sourceEventId": "evt"}


def test_terminate_flow_creates_plan_and_routes_token_to_approvers_only(repos):
    svc, ses = services(repos, FakeReasoning(YES))
    out = handle(EVENT, svc)
    assert out["result"] == "AWAITING_APPROVAL" and out["planId"] == "PLAN-52650C02BEB8V1"
    assert repos.case.state(CASE) is S.AWAITING_APPROVAL and repos.case.get(CASE)["planId"] == out["planId"]
    plan = repos.plan.get(CASE, out["planId"])
    assert plan["parameters"] == {"process_id": 4242, "expected_process_name": "yes", "plan_id": out["planId"]}
    assert plan["capability"] == "terminate_process" and plan["resource"] == INSTANCE
    (to_app, body_app), (to_op, body_op) = ses.bodies()
    assert to_app == ["boss@x.com"] and to_op == ["op@x.com"]
    assert "APPROVE PLAN-52650C02BEB8V1 " in body_app and "APPROVE PLAN" not in body_op
    token = body_app.split("APPROVE PLAN-52650C02BEB8V1 ")[1].split()[0]
    assert token not in body_op and token not in str(repos.plan.store.table.scan()["Items"])
    assert "NO ACTION HAS BEEN EXECUTED" in body_app and "[CG-52650C02BEB8]" in ses.sent[0]["RawMessage"]["Data"].decode()


def test_evidence_diagnosis_and_audit_are_persisted(repos):
    handle(EVENT, services(repos, FakeReasoning(YES))[0])
    kinds = [e["kind"] for e in repos.evidence.list(CASE)]
    assert kinds == ["READ", "DIAGNOSIS", "OUTCOMES"]
    assert repos.evidence.list(CASE)[0]["payload"]["summary"].startswith("top CPU: yes")
    events = [a["event"] for a in repos.audit.list(CASE)]
    assert events[:2] == ["INVESTIGATION_STARTED", "PLAN_CREATED"] and "EMAIL_SENT" in events


def test_calm_instance_is_no_action_with_informational_mail_and_no_plan(repos):
    svc, ses = services(repos, FakeReasoning(CALM))
    out = handle(EVENT, svc)
    assert out["result"] == "NO_ACTION" and out["planId"] is None
    assert repos.plan.list(CASE) == [] and repos.case.state(CASE) is S.NO_ACTION
    dest, body = ses.bodies()[0]
    assert sorted(dest) == ["boss@x.com", "op@x.com"] and "APPROVE" not in body


def test_rejected_rank2_option_never_becomes_a_plan(repos):
    handle(EVENT, services(repos, FakeReasoning(CALM))[0])     # rank 2 is reboot
    assert repos.plan.list(CASE) == []


def test_reboot_plan(repos):
    raw = {**YES, "outcomes": [{"rank": 1, "capability": "reboot_demo_instance", "parameters": {"junk": 1}, "risk": "disruptive"}]}
    out = handle(EVENT, services(repos, FakeReasoning(raw))[0])
    assert repos.plan.get(CASE, out["planId"])["parameters"] == {"plan_id": out["planId"]}


@pytest.mark.parametrize("params", [{}, {"process_id": 1, "expected_process_name": "yes"},
                                    {"process_id": 7, "expected_process_name": "x; reboot"}, {"process_id": "4242"}])
def test_invalid_parameters_from_the_model_escalate(repos, params):
    raw = {**YES, "outcomes": [{"rank": 1, "capability": "terminate_process", "parameters": params}]}
    svc, ses = services(repos, FakeReasoning(raw))
    assert handle(EVENT, svc)["result"] == "ESCALATED"
    assert repos.plan.list(CASE) == [] and "ESCALATED" in ses.bodies()[0][1] and "APPROVE" not in ses.bodies()[0][1]


def test_reasoning_failure_escalates_with_mail_not_silence(repos):
    svc, ses = services(repos, FakeReasoning(error=RuntimeError("secret internal detail")))
    out = handle(EVENT, svc)
    assert out["result"] == "ESCALATED" and repos.case.state(CASE) is S.ESCALATED
    assert len(ses.sent) == 1 and "secret internal detail" not in ses.bodies()[0][1]
    assert "RuntimeError" in ses.bodies()[0][1]


def test_explicit_escalate_outcome(repos):
    raw = {**CALM, "outcomes": [{"rank": 1, "capability": "ESCALATE", "rationale": "cannot tell"}]}
    out = handle(EVENT, services(repos, FakeReasoning(raw))[0])
    assert out["result"] == "ESCALATED" and repos.case.state(CASE) is S.ESCALATED


def test_duplicate_trigger_does_no_second_investigation_or_mail(repos):
    reasoning = FakeReasoning(YES)
    svc, ses = services(repos, reasoning)
    handle(EVENT, svc)
    assert handle(EVENT, svc)["result"] == "SKIPPED_ALREADY_HANDLED"
    assert len(reasoning.calls) == 1 and len(ses.sent) == 2        # approvers + operators, once
    assert len(repos.plan.list(CASE)) == 1


def test_workflow_duplicate_status_and_bad_events(repos):
    reasoning = FakeReasoning(YES)
    svc, ses = services(repos, reasoning)
    assert handle({"caseId": CASE, "status": "DUPLICATE"}, svc)["result"] == "SKIPPED_DUPLICATE"
    assert reasoning.calls == [] and ses.sent == []
    with pytest.raises(ValueError):
        handle({}, svc)
    with pytest.raises(LookupError):
        handle({"caseId": "CG-AAAAAAAAAAAA", "status": "OPEN"}, svc)


def test_mail_failure_is_recorded_and_raised(repos):
    svc, ses = services(repos, FakeReasoning(CALM))
    ses.send_raw_email = lambda **kw: (_ for _ in ()).throw(RuntimeError("ses down"))
    with pytest.raises(RuntimeError):
        handle(EVENT, svc)
    assert "EMAIL_FAILED" in [a["event"] for a in repos.audit.list(CASE)]
    assert repos.case.state(CASE) is S.NO_ACTION          # state is authoritative; mail is best-effort


def test_reasoning_sees_only_case_facts(repos):
    reasoning = FakeReasoning(CALM)
    handle(EVENT, services(repos, reasoning)[0])
    mode, case_id, ctx = reasoning.calls[0]
    assert mode == "investigate" and ctx["case"]["resource"] == INSTANCE and set(ctx["case"]) == {
        "caseId", "alarmName", "resource", "status", "createdAt"}


def test_pepper_loaded_from_ssm_not_env():
    class Ssm:
        def get_parameter(self, Name, WithDecryption):
            assert WithDecryption and Name == "/cloudops-guardian/approval-token-pepper"
            return {"Parameter": {"Value": "from-ssm"}}
    env = {"APPROVAL_TOKEN_PEPPER_PARAM": "/cloudops-guardian/approval-token-pepper"}
    assert load_pepper(CFG, env, Ssm()) == "from-ssm"
    assert load_pepper(CFG, {}, None) == "pepper"


def test_malformed_case_id_escalates_instead_of_crashing(repos):
    repos.case.store.table.put_item(Item={"PK": "CASE#CG-C5TEST123456", "SK": "METADATA", "caseId": "CG-C5TEST123456",
                                         "status": "OPEN", "alarmName": "a", "createdAt": "x"})
    svc, ses = services(repos, FakeReasoning(YES))
    out = handle({"caseId": "CG-C5TEST123456", "status": "OPEN"}, svc)
    assert out["result"] == "ESCALATED" and repos.plan.list("CG-C5TEST123456") == []


def test_mail_failure_after_plan_withdraws_the_plan(repos):
    svc, ses = services(repos, FakeReasoning(YES))
    ses.send_raw_email = lambda **kw: (_ for _ in ()).throw(RuntimeError("ses down"))
    with pytest.raises(RuntimeError):
        handle(EVENT, svc)
    plan = repos.plan.list(CASE)[0]
    assert plan["planStatus"] == "SUPERSEDED" and repos.case.state(CASE) is S.ESCALATED
    assert repos.plan.consume_token(CASE, plan["planId"]) is False       # cannot be approved
