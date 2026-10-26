import re

import pytest
from conftest import CASE
from fakes import GOOD, INSTANCE, PLAN1, FakeAction, FakeReasoning, FakeRead, approve_msg, cfg, services
from cloudops_orchestrator.handlers.alarm_handler import handle
from cloudops_orchestrator.router import Router
from cloudops_orchestrator.states import CaseState as S

PLAN2 = f"PLAN-{CASE[3:]}V2"
PLAN3 = f"PLAN-{CASE[3:]}V3"
FOUR = {"diagnosis": {"summary": "two yes processes", "root_cause": "runaway yes", "confidence": "HIGH"},
        "evidence": [{"tool": "get_top_cpu_processes", "summary": "yes(pid 4242) 99%, yes(pid 5000) 99%", "data": {}}],
        "outcomes": [
            {"rank": 1, "capability": "terminate_process", "parameters": {"process_id": 4242, "expected_process_name": "yes"}, "rationale": "first yes", "impact": "frees a core", "risk": "low"},
            {"rank": 2, "capability": "terminate_process", "parameters": {"process_id": 5000, "expected_process_name": "yes"}, "rationale": "second yes", "impact": "frees a core", "risk": "low"},
            {"rank": 3, "capability": "reboot_demo_instance", "rationale": "NOT warranted, disruptive", "impact": "downtime", "risk": "high"},
            {"rank": 4, "capability": "ESCALATE", "rationale": "if processes respawn"}],
        "answer": "two yes processes"}
def reranked(*order):
    """FOUR's outcomes with NEW rank values: order lists the original indexes, best first."""
    return {**FOUR, "outcomes": [{**FOUR["outcomes"][i], "rank": r} for r, i in enumerate(order, 1)]}


SHUFFLED = reranked(3, 2, 0, 1)        # the model now puts ESCALATE first and reboot second
EVENT = {"caseId": CASE, "status": "OPEN", "sourceEventId": "e"}


def incident(repos, **kw):
    """The real path: alarm handler investigates and mails; returns (svc, ses, reasoning)."""
    reasoning = FakeReasoning(FOUR)
    svc, ses = services(repos, reasoning=reasoning, **kw)
    assert handle(EVENT, svc)["result"] == "AWAITING_APPROVAL"
    return svc, ses, reasoning


def token_for(ses, plan_id):
    """What an approver would copy out of their mail."""
    for dest, body in reversed(ses.mails()):
        m = re.search(rf"APPROVE {plan_id} (\S+)", body)
        if m and "boss@x.com" in dest:
            return m.group(1)
    raise AssertionError(f"no approval line for {plan_id}")


def cmd(body, sender="op@x.com", mid="<c1>", case=CASE):
    return {"message_id": mid, "from": [sender], "subject": f"Re: [{case}]", "references": [], "body_text": body,
            "classification": {"intent": "CASE_FOLLOWUP", "case_id": case, "plan_id": None, "approval_token_present": False}}


def route(svc, msg, token=None):
    return Router(svc).route(msg, {"verdicts": GOOD, "token": token})


def approve(svc, plan_id, token, sender="boss@x.com", mid="<a1>"):
    m = approve_msg({"planId": plan_id}, sender, mid)
    return route(svc, m, token)


def last_mail_to(ses, who):
    return [b for d, b in ses.mails() if who in d][-1]


# ---------- the fixed option list ----------
def test_first_mail_shows_numbered_options_with_what_each_one_allows(repos):
    svc, ses, _ = incident(repos)
    body = last_mail_to(ses, "boss@x.com")
    assert "1. terminate_process   <== this is the plan below" in body
    assert "2. terminate_process   (reply PROPOSE 2 to get an approvable plan for this)" in body
    assert "3. reboot_demo_instance   (reply PROPOSE 3" in body and "4. ESCALATE   (reply ESCALATE" in body
    assert "source:   proposed by the agent after investigating" in body and "replaces:" not in body


def test_options_are_not_renumbered_by_follow_up_questions(repos):
    svc, ses, reasoning = incident(repos)
    reasoning.raw = SHUFFLED                              # the model now ranks differently
    assert [o["capability"] for o in reasoning.invoke("followup", CASE)["outcomes"]][0] == "ESCALATE"
    route(svc, cmd("why is it hot?"))
    body = last_mail_to(ses, "op@x.com")
    assert "1. terminate_process   <== this is the plan below" in body        # still the original numbering
    assert body.index("3. reboot_demo_instance") < body.index("4. ESCALATE")
    assert repos.plan.list(CASE)[0]["planId"] == PLAN1 and len(repos.plan.list(CASE)) == 1


def test_follow_up_mail_states_the_pending_plan_truthfully_without_the_token(repos):
    svc, ses, _ = incident(repos)
    token = token_for(ses, PLAN1)
    route(svc, cmd("what is yes?"))
    body = last_mail_to(ses, "op@x.com")
    assert "no plan is pending" not in body and PLAN1 in body and "expires:" in body
    assert f"REJECT {PLAN1}" in body and token not in body and "APPROVE PLAN" not in body
    assert "PROPOSE <n>" in body and "ESCALATE" in body


# ---------- PROPOSE <n> ----------
def test_propose_voids_the_old_plan_and_only_the_new_plan_can_be_approved(repos):
    svc, ses, _ = incident(repos)
    token1 = token_for(ses, PLAN1)
    out = route(svc, cmd("PROPOSE 2"))
    assert out["result"] == "PLAN_PROPOSED" and out["planId"] == PLAN2 and out["replaces"] == PLAN1
    plans = {p["planId"]: p["planStatus"] for p in repos.plan.list(CASE)}
    assert plans == {PLAN1: "SUPERSEDED", PLAN2: "PROPOSED"}
    assert repos.plan.get(CASE, PLAN2)["parameters"]["process_id"] == 5000          # from the STORED option, not from any text
    assert repos.case.state(CASE) is S.AWAITING_APPROVAL and repos.case.get(CASE)["planId"] == PLAN2
    # the old token is dead
    denied = approve(svc, PLAN1, token1, mid="<a1>")
    assert denied["result"] == "DENIED_PLAN_NOT_LATEST" and svc.action.plans == []
    assert f"A newer plan exists: {PLAN2} (proposed by op@x.com)" in last_mail_to(ses, "boss@x.com")
    # the old token does not work on the new plan either (tokens are bound to the plan id)
    assert approve(svc, PLAN2, token1, mid="<a2>")["result"] == "DENIED_TOKEN_INVALID"
    # the new token works, exactly once
    token2 = token_for(ses, PLAN2)
    assert approve(svc, PLAN2, token2, mid="<a3>")["result"] == "EXECUTING"
    assert [p["parameters"]["process_id"] for p in svc.action.plans] == [5000]
    assert approve(svc, PLAN2, token2, "boss2@x.com", "<a4>")["result"].startswith("DENIED_") and len(svc.action.plans) == 1


def test_revised_plan_mail_says_why_and_whose_proposal_and_what_it_replaces(repos):
    svc, ses, _ = incident(repos)
    route(svc, cmd("PROPOSE 2", sender="op@x.com"))
    approvers, operator = last_mail_to(ses, "boss@x.com"), last_mail_to(ses, "op@x.com")
    subjects = [b for d, b in ses.mails() if "Revised plan" in b]
    assert subjects
    for body in (approvers,):
        assert "proposed by op@x.com" in body and "(reply PROPOSE 2)" in body and "the agent had ranked this option #2" in body
        assert f"replaces: {PLAN1}, which is now void and cannot be approved" in body
        assert f"APPROVE {PLAN2} " in body and "2. terminate_process   <== this is the plan below" in body
    ack = [b for d, b in ses.mails() if d == ["op@x.com"] and "was created from your choice" in b]
    assert ack and "Approvers have been sent the approval command" in ack[0]


def test_token_goes_only_to_approvers_on_a_revised_plan(repos):
    svc, ses, _ = incident(repos)
    route(svc, cmd("PROPOSE 2"))
    token2 = token_for(ses, PLAN2)
    for dest, body in ses.mails():
        if "boss@x.com" not in dest:
            assert token2 not in body and f"APPROVE {PLAN2}" not in body
    assert token2 not in str(repos.plan.store.table.scan()["Items"])


def test_human_can_choose_a_reboot_the_agent_advised_against(repos):
    svc, ses, _ = incident(repos)
    out = route(svc, cmd("PROPOSE 3"))
    plan = repos.plan.get(CASE, out["planId"])
    assert plan["capability"] == "reboot_demo_instance" and plan["proposedVia"] == "operator"
    body = last_mail_to(ses, "boss@x.com")
    assert "NOT warranted, disruptive" in body and "3. reboot_demo_instance   <== this is the plan below" in body
    assert approve(svc, out["planId"], token_for(ses, out["planId"]), mid="<a9>")["result"] == "EXECUTING"     # live pre-check still applies


def test_proposing_the_same_option_reissues_it_with_a_new_token_for_a_lost_mail(repos):
    svc, ses, _ = incident(repos)
    token1 = token_for(ses, PLAN1)
    assert route(svc, cmd("PROPOSE 1"))["planId"] == PLAN2
    token2 = token_for(ses, PLAN2)
    assert token1 != token2 and repos.plan.get(CASE, PLAN2)["parameters"]["process_id"] == 4242
    assert repos.plan.get(CASE, PLAN1)["planStatus"] == "SUPERSEDED"


@pytest.mark.parametrize("body,fragment", [("PROPOSE 4", "not an action"), ("PROPOSE 9", "no option 9"), ("PROPOSE 0", "no option 0")])
def test_invalid_proposals_are_refused_and_change_nothing(repos, body, fragment):
    svc, ses, _ = incident(repos)
    out = route(svc, cmd(body))
    assert out["result"] == "REFUSED" and fragment in last_mail_to(ses, "op@x.com")
    assert [p["planStatus"] for p in repos.plan.list(CASE)] == ["PROPOSED"] and repos.case.state(CASE) is S.AWAITING_APPROVAL


def test_propose_after_approval_is_refused_and_the_action_is_untouched(repos):
    svc, ses, _ = incident(repos)
    approve(svc, PLAN1, token_for(ses, PLAN1))
    out = route(svc, cmd("PROPOSE 2", mid="<p1>"))
    assert out["result"] == "REFUSED" and "case is VERIFYING" in last_mail_to(ses, "op@x.com")
    assert len(svc.action.plans) == 1 and len(repos.plan.list(CASE)) == 1


def test_propose_wins_the_race_so_the_old_approval_is_refused(repos):
    svc, ses, _ = incident(repos)
    token1 = token_for(ses, PLAN1)
    route(svc, cmd("PROPOSE 2"))
    assert approve(svc, PLAN1, token1, mid="<late>")["result"].startswith("DENIED_") and svc.action.plans == []


def test_propose_can_reopen_a_rejected_case(repos):
    svc, ses, _ = incident(repos)
    route(svc, {**cmd("x"), "message_id": "<r>", "body_text": "", "classification": {"intent": "REJECTION", "case_id": CASE, "plan_id": PLAN1}})
    assert repos.case.state(CASE) is S.REJECTED
    assert route(svc, cmd("PROPOSE 2", mid="<p2>"))["result"] == "PLAN_PROPOSED" and repos.case.state(CASE) is S.AWAITING_APPROVAL


def test_propose_is_exact_so_chatty_text_is_only_a_question(repos):
    svc, ses, _ = incident(repos)
    for text in ("I prefer to go with recommendation 4", "please PROPOSE 2", "PROPOSE 2 now", "propose option 2"):
        assert route(svc, cmd(text, mid=f"<{text}>"))["result"] == "ANSWERED"
    assert len(repos.plan.list(CASE)) == 1 and repos.case.state(CASE) is S.AWAITING_APPROVAL


def test_duplicate_delivery_of_a_propose_does_not_create_two_plans(repos):
    svc, ses, _ = incident(repos)
    route(svc, cmd("PROPOSE 2", mid="<same>"))
    assert route(svc, cmd("PROPOSE 2", mid="<same>"))["result"] == "DUPLICATE_MESSAGE"
    assert len(repos.plan.list(CASE)) == 2


# ---------- ESCALATE ----------
def test_escalate_withdraws_the_plan_and_tells_everyone_who_and_why(repos):
    svc, ses, _ = incident(repos)
    before = len(ses.sent)
    out = route(svc, cmd("ESCALATE needs a human to look at the parent job"))
    assert out == {"intent": "ESCALATE", "result": "ESCALATED", "withdrawnPlan": PLAN1}
    assert repos.case.state(CASE) is S.ESCALATED and repos.plan.get(CASE, PLAN1)["planStatus"] == "WITHDRAWN"
    c = repos.case.get(CASE)
    assert c["escalatedBy"] == "op@x.com" and "parent job" in c["escalationReason"]
    dest, body = ses.mails()[before]
    assert sorted(dest) == ["boss2@x.com", "boss@x.com", "op@x.com"] and len(ses.sent) == before + 1
    assert "ESCALATED by op@x.com" in body and "parent job" in body and f"pending plan {PLAN1} was withdrawn" in body
    assert "No automated action will be taken" in body and "APPROVE PLAN" not in body


def test_after_escalation_approve_and_reject_are_refused_with_the_reason(repos):
    svc, ses, _ = incident(repos)
    token1 = token_for(ses, PLAN1)
    route(svc, cmd("ESCALATE"))
    assert approve(svc, PLAN1, token1, mid="<a1>")["result"] == "DENIED_PLAN_NOT_PENDING"
    assert "escalated by op@x.com" in last_mail_to(ses, "boss@x.com") and "no automated action will be taken" in last_mail_to(ses, "boss@x.com")
    rej = route(svc, {**cmd(""), "message_id": "<r1>", "classification": {"intent": "REJECTION", "case_id": CASE, "plan_id": PLAN1}})
    assert rej["result"] == "DENIED_PLAN_NOT_PENDING" and "escalated by op@x.com" in last_mail_to(ses, "op@x.com")
    assert svc.action.plans == [] and repos.case.state(CASE) is S.ESCALATED


def test_second_escalate_says_it_is_already_escalated(repos):
    svc, ses, _ = incident(repos)
    route(svc, cmd("ESCALATE", mid="<e1>"))
    assert route(svc, cmd("ESCALATE", sender="boss@x.com", mid="<e2>"))["result"] == "REFUSED"
    assert "already escalated" in last_mail_to(ses, "boss@x.com") and "escalated by op@x.com" in last_mail_to(ses, "boss@x.com")


def test_escalate_is_refused_while_an_action_is_running(repos):
    svc, ses, _ = incident(repos)
    approve(svc, PLAN1, token_for(ses, PLAN1))
    assert route(svc, cmd("ESCALATE"))["result"] == "REFUSED" and repos.case.state(CASE) is S.VERIFYING


def test_a_stated_preference_to_escalate_is_not_a_command(repos):
    svc, ses, _ = incident(repos)
    route(svc, cmd("I prefer to ESCALATE"))
    assert repos.case.state(CASE) is S.AWAITING_APPROVAL and repos.plan.get(CASE, PLAN1)["planStatus"] == "PROPOSED"


def test_a_question_after_escalation_reopens_it_but_needs_a_fresh_approval(repos):
    svc, ses, _ = incident(repos)
    route(svc, cmd("ESCALATE"))
    out = route(svc, cmd("is it still hot?", mid="<q1>"))
    assert out["state"] in ("AWAITING_APPROVAL", "NO_ACTION", "ESCALATED")
    assert repos.plan.get(CASE, PLAN1)["planStatus"] == "WITHDRAWN"                  # the old plan never comes back


# ---------- who may use the commands ----------
def test_unknown_senders_cannot_propose_or_escalate(repos):
    svc, ses, _ = incident(repos)
    n = len(ses.sent)
    for text in ("PROPOSE 2", "ESCALATE"):
        assert route(svc, cmd(text, sender="stranger@evil.com", mid=f"<s{text}>"))["result"] == "DROPPED_UNKNOWN_SENDER"
    assert len(ses.sent) == n and repos.case.state(CASE) is S.AWAITING_APPROVAL


def test_commands_without_a_case_id_ask_for_one(repos):
    svc, ses, _ = incident(repos)
    m = {**cmd("ESCALATE", case=None)}
    m["classification"] = {"intent": "NEW_OR_AMBIGUOUS_QUERY", "case_id": None, "plan_id": None}
    assert route(svc, m)["result"] == "CLARIFIED" and "which case" in last_mail_to(ses, "op@x.com")
    assert repos.case.state(CASE) is S.AWAITING_APPROVAL


def test_agent_driven_plan_change_records_who_asked(repos):
    svc, ses, reasoning = incident(repos)
    reasoning.raw = reranked(1, 0, 2, 3)                  # the agent now prefers the OTHER yes first
    out = route(svc, cmd("is the first yes gone?", sender="op@x.com"))
    assert out["newPlan"] == PLAN2
    body = last_mail_to(ses, "boss@x.com")
    assert "proposed by the agent after a follow-up from op@x.com" in body and f"replaces: {PLAN1}" in body
