import json

import pytest
from conftest import CASE
from fakes import (CALM, GOOD, INSTANCE, KILL5000, PLAN1, FakeAction, FakeRead, FakeReasoning, FakeSes, FakeSfn,
                   approve_msg, cfg, listing, make_plan, recovered, row, services)
from cloudops_orchestrator.clients._gateway import GatewayDenied, GatewayError, GatewayTransportError, ToolError
from cloudops_orchestrator.router import Router
from cloudops_orchestrator.states import CaseState as S


def approve(repos, svc, plan, token, sender="boss@x.com", mid="<a1>"):
    return Router(svc).route(approve_msg(plan, sender, mid), {"verdicts": GOOD, "token": token})


def audit_events(repos):
    return [a["event"] for a in repos.audit.list(CASE)]


def test_terminate_happy_path_executes_exactly_the_approved_plan_and_starts_verification(repos):
    plan, token = make_plan(repos)
    svc, ses = services(repos)
    out = approve(repos, svc, plan, token)
    assert out["result"] == "EXECUTING" and out["action"] == "SUCCESS"
    assert [p["parameters"] for p in svc.action.plans] == [plan["parameters"]]       # stored params, nothing else
    assert repos.case.state(CASE) is S.VERIFYING                                       # NOT resolved by API success
    ex = repos.execution.get(CASE, PLAN1)
    assert ex["execStatus"] == "FINISHED" and ex["outcome"] == "SUCCESS"
    started = svc.sfn.started[0]
    assert started["name"] == PLAN1 and json.loads(started["input"])["waitSeconds"] == 45
    assert ses.mails()[0][0] == ["boss@x.com"] and "being executed now" in ses.mails()[0][1]
    assert "PRECHECK" in audit_events(repos) and "ACTION_RESULT" in audit_events(repos)


def test_reboot_path(repos):
    plan, token = make_plan(repos, capability="reboot_demo_instance")
    svc, _ = services(repos, action=FakeAction({"status": "ACTION_ACCEPTED", "verificationRequired": True}))
    out = approve(repos, svc, plan, token)
    assert out["action"] == "ACTION_ACCEPTED" and repos.case.state(CASE) is S.VERIFYING
    assert json.loads(svc.sfn.started[0]["input"])["waitSeconds"] == 90


def test_second_approver_is_refused_and_told_who_decided(repos):
    plan, token = make_plan(repos)
    svc, ses = services(repos)
    approve(repos, svc, plan, token, "boss@x.com", "<a1>")
    out = approve(repos, svc, plan, token, "boss2@x.com", "<a2>")
    assert out["result"].startswith("DENIED_") and len(svc.action.plans) == 1
    refusal = ses.mails()[-1][1]
    assert ses.mails()[-1][0] == ["boss2@x.com"] and "approved by boss@x.com" in refusal


def test_two_approvers_racing_only_one_action(repos):
    plan, token = make_plan(repos)
    svc, _ = services(repos)
    # Both pass validation; simulate the loser by pre-winning the gate between validate and authorize.
    from cloudops_orchestrator.approval_validator import validate_approval
    msg = approve_msg(plan, "boss2@x.com", "<a2>")
    assert validate_approval(repos, svc.cfg, msg, token, GOOD).ok
    approve(repos, svc, plan, token, "boss@x.com", "<a1>")          # the winner
    assert Router(svc).route(msg, {"verdicts": GOOD, "token": token})["result"].startswith("DENIED_")
    assert len(svc.action.plans) == 1


def test_duplicate_delivery_of_the_same_email_is_suppressed(repos):
    plan, token = make_plan(repos)
    svc, _ = services(repos)
    approve(repos, svc, plan, token, mid="<same>")
    assert approve(repos, svc, plan, token, mid="<same>")["result"] == "DUPLICATE_MESSAGE"
    assert len(svc.action.plans) == 1


def test_execute_once_claim_blocks_even_if_the_gate_were_bypassed(repos):
    plan, token = make_plan(repos)
    assert repos.execution.claim(CASE, PLAN1, "terminate_process", plan["parametersHash"])    # someone already claimed
    svc, _ = services(repos)
    assert approve(repos, svc, plan, token)["result"] == "DUPLICATE_EXECUTION_BLOCKED"
    assert svc.action.plans == [] and svc.sfn.started == []


def test_self_recovered_before_approval_means_no_action_and_no_write(repos):
    plan, token = make_plan(repos)
    svc, ses = services(repos, read=recovered())
    out = approve(repos, svc, plan, token)
    assert out["result"] == "NOT_EXECUTED_NO_ACTION" and repos.case.state(CASE) is S.NO_ACTION
    assert svc.action.plans == [] and repos.execution.get(CASE, PLAN1) is None and svc.sfn.started == []
    assert repos.plan.get(CASE, PLAN1)["planStatus"] == "NOT_EXECUTED"
    assert any("did not act" in b for _, b in ses.mails())


def test_idle_matching_process_with_alarm_ok_is_no_action(repos):
    plan, token = make_plan(repos)
    read = FakeRead(alarm={"stateValue": "OK"}, process={"status": "FOUND", "output": listing(row(4242, "yes", 0.5))})
    svc, _ = services(repos, read=read)
    assert approve(repos, svc, plan, token)["result"] == "NOT_EXECUTED_NO_ACTION" and svc.action.plans == []


def test_stale_plan_is_withdrawn_and_a_new_plan_needs_a_new_approval(repos):
    plan, token = make_plan(repos)
    read = FakeRead(process={"status": "NOT_FOUND"}, alarm={"stateValue": "ALARM"})        # yes 4242 died, other load remains
    svc, ses = services(repos, read=read, reasoning=FakeReasoning(KILL5000))
    out = approve(repos, svc, plan, token)
    assert out["result"] == "STALE_REPLANNED" and out["newPlan"] == f"PLAN-{CASE[3:]}V2"
    assert svc.action.plans == [] and repos.case.state(CASE) is S.AWAITING_APPROVAL
    assert repos.plan.get(CASE, PLAN1)["planStatus"] == "NOT_EXECUTED"
    assert repos.plan.get(CASE, out["newPlan"])["planStatus"] == "PROPOSED"
    assert any("APPROVE PLAN-" in b and out["newPlan"] in b for d, b in ses.mails() if sorted(d) == ["boss2@x.com", "boss@x.com"])
    # the old approval cannot be replayed against the new plan
    assert approve(repos, svc, plan, token, mid="<a9>")["result"].startswith("DENIED_")


def test_stale_with_reasoning_down_escalates(repos):
    plan, token = make_plan(repos)
    svc, ses = services(repos, read=FakeRead(process={"status": "NOT_FOUND"}), reasoning=FakeReasoning(error=RuntimeError("x")))
    out = approve(repos, svc, plan, token)
    assert out["state"] == "ESCALATED" and repos.case.state(CASE) is S.ESCALATED and svc.action.plans == []


def test_pid_reused_by_a_different_program_is_never_killed(repos):
    plan, token = make_plan(repos)
    read = FakeRead(process={"status": "FOUND", "output": listing(row(4242, "postgres", 60.0))})
    svc, _ = services(repos, read=read, reasoning=FakeReasoning(CALM))
    out = approve(repos, svc, plan, token)
    assert out["result"] == "STALE_REPLANNED" and svc.action.plans == []


@pytest.mark.parametrize("read_over", [
    dict(alarm=RuntimeError("boom")),
    dict(describe=RuntimeError("boom")),
    dict(process=RuntimeError("boom")),
    dict(process={"status": "INCONCLUSIVE"}),
    dict(alarm={"stateValue": "INSUFFICIENT_DATA"}),
    dict(describe={"instanceId": INSTANCE, "state": "stopped", "tags": [{"Key": "AgentRemediation", "Value": "Enabled"}]}),
    dict(describe={"instanceId": INSTANCE, "state": "running", "tags": []}),
    dict(describe={"instanceId": "i-other", "state": "running", "tags": [{"Key": "AgentRemediation", "Value": "Enabled"}]}),
])
def test_when_the_world_cannot_be_confirmed_nothing_is_executed(repos, read_over):
    plan, token = make_plan(repos)
    svc, ses = services(repos, read=FakeRead(**read_over))
    out = approve(repos, svc, plan, token)
    assert out["result"] == "NOT_EXECUTED_ESCALATED" and repos.case.state(CASE) is S.ESCALATED
    assert svc.action.plans == [] and svc.sfn.started == []


def test_reboot_precheck_alarm_ok_is_no_action(repos):
    plan, token = make_plan(repos, capability="reboot_demo_instance")
    svc, _ = services(repos, read=FakeRead(alarm={"stateValue": "OK"}))
    assert approve(repos, svc, plan, token)["result"] == "NOT_EXECUTED_NO_ACTION" and svc.action.plans == []


def test_policy_denial_escalates_and_never_verifies(repos):
    plan, token = make_plan(repos)
    svc, ses = services(repos, action=FakeAction(error=GatewayDenied("policy")))
    out = approve(repos, svc, plan, token)
    assert out["state"] == "ESCALATED" and repos.execution.get(CASE, PLAN1)["outcome"] == "DENIED"
    assert svc.sfn.started == [] and "did not run" in ses.mails()[-1][1]


def test_tool_error_is_a_failed_remediation_without_verification(repos):
    plan, token = make_plan(repos)
    svc, _ = services(repos, action=FakeAction(error=ToolError("bad")))
    out = approve(repos, svc, plan, token)
    assert out["state"] == "REMEDIATION_FAILED" and svc.sfn.started == []


def test_action_failed_status_skips_verification(repos):
    plan, token = make_plan(repos)
    svc, _ = services(repos, action=FakeAction({"status": "FAILED", "details": {"error": "ssm"}}))
    assert approve(repos, svc, plan, token)["state"] == "REMEDIATION_FAILED" and svc.sfn.started == []


@pytest.mark.parametrize("action", [
    FakeAction(error=GatewayTransportError("timeout")),                 # outcome unknown: it may have run
    FakeAction({"status": "TIMEOUT"}),
    FakeAction({"status": "REFUSED", "details": {"output": "REFUSED: process no longer exists"}}),
])
def test_unknown_or_refused_outcomes_go_to_verification_not_to_a_guess(repos, action):
    plan, token = make_plan(repos)
    svc, _ = services(repos, action=action)
    assert approve(repos, svc, plan, token)["result"] == "EXECUTING"
    assert repos.case.state(CASE) is S.VERIFYING and len(svc.sfn.started) == 1 and len(action.plans) == 1   # never retried


def test_ack_mail_failure_does_not_stop_the_action(repos):
    plan, token = make_plan(repos)
    svc, ses = services(repos, ses=FakeSes(fail_first=True))
    assert approve(repos, svc, plan, token)["result"] == "EXECUTING" and len(svc.action.plans) == 1
    assert "EMAIL_FAILED" in audit_events(repos)


def test_verification_cannot_start_escalates_with_mail(repos):
    plan, token = make_plan(repos)
    svc, ses = services(repos, sfn=FakeSfn(error=RuntimeError("sfn down")))
    out = approve(repos, svc, plan, token)
    assert out["result"] == "VERIFICATION_NOT_STARTED" and repos.case.state(CASE) is S.ESCALATED
    assert "verification could not be started" in ses.mails()[-1][1]


def test_unexpected_exception_after_claim_fails_safe_and_blocks_a_retry(repos):
    plan, token = make_plan(repos)
    svc, ses = services(repos, action=FakeAction(error=KeyError("surprise")))
    out = approve(repos, svc, plan, token)
    assert out["result"] == "EXECUTION_ERROR" and repos.case.state(CASE) is S.ESCALATED
    assert repos.execution.get(CASE, PLAN1)["execStatus"] == "CLAIMED"       # claim stays: no second execution possible
    assert repos.execution.claim(CASE, PLAN1, "terminate_process", "h") is False


def test_wrong_token_never_reaches_execution(repos):
    plan, token = make_plan(repos)
    svc, _ = services(repos)
    assert approve(repos, svc, plan, "wrong")["result"] == "DENIED_TOKEN_INVALID"
    assert svc.action.plans == [] and svc.read.calls == []             # not even a READ was made


def test_execution_disabled_flag_keeps_the_dry_run(repos):
    plan, token = make_plan(repos)
    svc, ses = services(repos, config=cfg(execution_enabled=False))
    assert approve(repos, svc, plan, token)["result"] == "DRY_RUN_OK"
    assert svc.action.plans == [] and repos.case.state(CASE) is S.AWAITING_APPROVAL


def test_reject_after_approval_is_refused_cancels_nothing_and_says_what_happened(repos):
    plan, token = make_plan(repos)
    svc, ses = services(repos)
    approve(repos, svc, plan, token)                                           # boss approved; case is now VERIFYING
    out = Router(svc).route({"message_id": "<r1>", "from": ["op@x.com"], "subject": "Re: [x]", "references": [], "body_text": "",
                             "classification": {"intent": "REJECTION", "case_id": CASE, "plan_id": PLAN1}},
                            {"verdicts": GOOD, "token": None})
    assert out["result"] == "DENIED_PLAN_NOT_PENDING"
    body = ses.mails()[-1][1]
    assert ses.mails()[-1][0] == ["op@x.com"] and "approved by boss@x.com" in body and "case is now VERIFYING" in body
    assert "nothing has been cancelled" in body and repos.case.state(CASE) is S.VERIFYING
    assert len(svc.action.plans) == 1                                          # the executed action is untouched


def test_reject_is_only_offered_while_a_plan_is_pending(repos):
    from cloudops_orchestrator.emails.incident import render_incident
    from cloudops_orchestrator.emails.approval_result import render_ack, render_result
    plan, token = make_plan(repos)
    case = {"caseId": CASE}
    assert f"REJECT {PLAN1}" in render_incident(case, {"summary": "s", "root_cause": "r", "confidence": "HIGH"}, [], [], plan, token)[1]
    assert "REJECT" not in render_ack(CASE, plan)[1]
    assert "REJECT" not in render_result(CASE, plan, "RESOLVED", "b", "a")[1]
    svc, ses = services(repos)
    approve(repos, svc, plan, token)
    from cloudops_orchestrator.emails.case_details import render_case_details
    assert "REJECT" not in render_case_details({"caseId": CASE, "status": "VERIFYING"}, [], repos.plan.list(CASE))[1]
    assert f"REJECT {PLAN1}" in render_case_details({"caseId": CASE, "status": "AWAITING_APPROVAL"}, [], [{**plan, "planStatus": "PROPOSED"}])[1]
