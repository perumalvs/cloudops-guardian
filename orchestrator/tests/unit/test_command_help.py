"""Every command an operator may use is explained in the mails that offer it, in the same words."""
import pathlib
import re

import pytest
from conftest import CASE
from cloudops_orchestrator.emails.approval_result import render_ack, render_result
from cloudops_orchestrator.emails.case_details import render_case_details
from cloudops_orchestrator.emails.clarification import render_clarification
from cloudops_orchestrator.emails.escalation import render_escalation
from cloudops_orchestrator.emails.followup import render_followup
from cloudops_orchestrator.emails.incident import render_incident
from cloudops_orchestrator.emails.rejection import render_rejection

PLAN = {"planId": f"PLAN-{CASE[3:]}V1", "planVersion": 1, "capability": "terminate_process", "resource": "i-1",
        "parameters": {"process_id": 4242, "expected_process_name": "yes", "plan_id": "x"}, "impact": "i", "risk": "r",
        "verification": "v", "expiresAt": "2026-10-22T00:30:00+00:00", "planStatus": "PROPOSED", "optionNumber": 1}
OPTS = [{"n": 1, "rank": 1, "capability": "terminate_process", "rationale": "a"},
        {"n": 2, "rank": 2, "capability": "reboot_demo_instance", "rationale": "b"},
        {"n": 3, "rank": 3, "capability": "ESCALATE", "rationale": "c"}]
CASE_D = {"caseId": CASE, "alarmName": "A", "status": "AWAITING_APPROVAL", "createdAt": "t"}
DIAG = {"summary": "s", "root_cause": "r", "confidence": "HIGH"}

COMMAND_LINES = ["DETAILS " + CASE, "PROPOSE <n>        for example:  PROPOSE 2", "ESCALATE [note]    for example:  ESCALATE",
                 "creates a NEW plan with a new approval line", "voids the pending one", "withdraws any pending plan"]


def has_all(body, lines):
    return all(l in body for l in lines)


@pytest.mark.parametrize("name,rendered", [
    ("incident with plan", render_incident(CASE_D, DIAG, [], OPTS, PLAN, "tok")[1]),
    ("incident without token", render_incident(CASE_D, DIAG, [], OPTS, PLAN, None)[1]),
    ("incident without plan", render_incident(CASE_D, DIAG, [], OPTS)[1]),
    ("follow-up answer", render_followup(CASE, "q", "a", [], OPTS, PLAN)[1]),
    ("details", render_case_details(CASE_D, [], [PLAN], options=OPTS)[1]),
    ("rejection", render_rejection(CASE, PLAN["planId"])[1]),
])
def test_mails_that_offer_choices_explain_propose_and_escalate(name, rendered):
    assert has_all(rendered, COMMAND_LINES), name


def test_reject_is_explained_exactly_where_a_plan_is_pending():
    assert f"REJECT {PLAN['planId']}\n      reject the pending plan" in render_incident(CASE_D, DIAG, [], OPTS, PLAN, "tok")[1]
    assert f"REJECT {PLAN['planId']}" in render_followup(CASE, "q", "a", [], OPTS, PLAN)[1]
    assert "REJECT PLAN" not in render_incident(CASE_D, DIAG, [], OPTS)[1]                       # no pending plan, no REJECT
    assert "REJECT PLAN" not in render_rejection(CASE, PLAN["planId"])[1]


@pytest.mark.parametrize("rendered", [
    render_ack(CASE, PLAN)[1],
    render_result(CASE, PLAN, "RESOLVED", "b", "a")[1],
    render_clarification("hello", CASE)[1],
    render_escalation(CASE_D, "r", [])[1],
])
def test_mails_where_choices_make_no_sense_do_not_offer_them(rendered):
    assert "PROPOSE" not in rendered and "REJECT PLAN" not in rendered


def test_the_token_is_never_part_of_the_command_help():
    body = render_incident(CASE_D, DIAG, [], OPTS, PLAN, None)[1]
    assert "APPROVE PLAN" not in body and not re.search(r"APPROVE\s+PLAN-\S+\s+\S+", body)


# ---- execution is on by default in the deploy script, with an explicit opt-out ----
SCRIPT = pathlib.Path(__file__).resolve().parents[3] / "deploy" / "deploy-orchestrator.sh"


def test_deploy_script_enables_execution_by_default_and_supports_opting_out():
    text = SCRIPT.read_text()
    assert '${EXECUTION:-on}' in text and "on) EXEC=true" in text and "off) EXEC=false" in text
    assert "EXECUTION_ENABLED=$EXEC" in text and "EXECUTION_ENABLED=false" not in text
    assert "EXECUTION=off bash deploy-orchestrator.sh" in text           # the opt-out is documented in the script header
