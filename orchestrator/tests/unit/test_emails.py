from cloudops_orchestrator.emails.approval_result import render_result
from cloudops_orchestrator.emails.case_details import render_case_details
from cloudops_orchestrator.emails.clarification import render_clarification
from cloudops_orchestrator.emails.escalation import render_escalation
from cloudops_orchestrator.emails.followup import render_followup
from cloudops_orchestrator.emails.incident import render_incident
from cloudops_orchestrator.emails.rejection import render_rejection

CASE = {"caseId": "CG-52650C02BEB8", "alarmName": "CloudOpsGuardian-EC2CPUAlarm", "resource": "__DEMO_INSTANCE_ID__",
        "status": "AWAITING_APPROVAL", "createdAt": "2026-10-22T00:00:00+00:00"}
PLAN = {"planId": "PLAN-52650C02BEB8V1", "planVersion": 1, "capability": "terminate_process",
        "resource": "__DEMO_INSTANCE_ID__", "parameters": {"process_id": 4242, "expected_process_name": "yes",
                                                           "plan_id": "PLAN-52650C02BEB8V1"},
        "impact": "stops one process", "risk": "low", "verification": "fresh READ", "expiresAt": "2026-10-22T00:30:00+00:00",
        "planStatus": "PROPOSED"}
DIAG = {"summary": "process yes is using 99% CPU", "root_cause": "runaway yes", "confidence": "HIGH"}
EVID = [{"tool": "get_top_cpu_processes", "summary": "yes pid 4242 at 99%"}]
OUTS = [{"rank": 1, "capability": "terminate_process", "rationale": "culprit identified", "impact": "i", "risk": "r"}]
TOKEN = "tok_ABC123-secret"


def test_incident_with_token_has_everything_required():
    subject, body = render_incident(CASE, DIAG, EVID, OUTS, PLAN, TOKEN)
    assert subject.startswith("[CG-52650C02BEB8]")
    for needle in ["CG-52650C02BEB8", "__DEMO_INSTANCE_ID__", "99% CPU", "Confidence: HIGH", "terminate_process",
                   "impact:", "risk:", "PLAN-52650C02BEB8V1", "NO ACTION HAS BEEN EXECUTED", "DETAILS CG-52650C02BEB8",
                   "REJECT PLAN-52650C02BEB8V1", f"APPROVE PLAN-52650C02BEB8V1 {TOKEN}", "A plain 'yes' does nothing"]:
        assert needle in body, needle


def test_operator_variant_never_contains_token():
    subject, body = render_incident(CASE, DIAG, EVID, OUTS, PLAN, token=None)
    assert "APPROVE PLAN" not in body and TOKEN not in body and "authorized approvers" in body


def test_incident_without_plan_has_no_approval_section():
    _, body = render_incident(CASE, DIAG, EVID, [{"rank": 1, "capability": "CONTINUE_MONITORING"}])
    assert "APPROVE" not in body and "PROPOSED PLAN" not in body and "NO ACTION HAS BEEN EXECUTED" in body


def test_details_never_shows_tokens_and_lists_state():
    plans = [{**PLAN, "tokenHash": "deadbeef", "tokenConsumedAt": "x"}]
    _, body = render_case_details(CASE, EVID, plans, [{"planId": PLAN["planId"], "execStatus": "FINISHED", "outcome": "SUCCESS"}],
                                  [{"planId": PLAN["planId"], "attempt": 1, "passed": True, "checks": {"cpu_ok": True}}])
    assert "AWAITING_APPROVAL" in body and "deadbeef" not in body and "SUCCESS" in body and "PASSED" in body


def test_followup_variants():
    _, body = render_followup(CASE["caseId"], "why?", "because yes", EVID, OUTS)
    assert "because yes" in body and "no plan is pending" in body
    _, body = render_followup(CASE["caseId"], "why?", "a", plan=PLAN, token=TOKEN)
    assert f"APPROVE {PLAN['planId']} {TOKEN}" in body


def test_result_states_are_honest():
    _, body = render_result(CASE["caseId"], PLAN, "RESOLVED", "yes at 99%", "CPU 2%, alarm OK", "SUCCESS (SIGTERM sent)")
    assert "RESOLVED - recovery verified" in body and "BEFORE" in body and "AFTER" in body
    assert "not the same as a fixed incident" in body
    s, body = render_result(CASE["caseId"], PLAN, "REMEDIATION_FAILED", "a", "still hot")
    assert "did not fix" in s


def test_rejection_escalation_clarification():
    assert "rejected" in render_rejection(CASE["caseId"], PLAN["planId"])[1]
    s, body = render_escalation(CASE, "READ checks failed", EVID)
    assert "ESCALATED" in body and "No action has been executed" in body
    s, body = render_clarification("Which case do you mean?")
    assert "Nothing was executed" in body and "need more information" in s
