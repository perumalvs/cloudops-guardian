from datetime import datetime, timedelta, timezone

import pytest
from conftest import CASE
from cloudops_orchestrator.approval_validator import (authorize_approval, authorize_rejection,
                                                      validate_approval, verdict_ok)
from cloudops_orchestrator.config import Config
from cloudops_orchestrator.plan_builder import PlanError, build_plan
from cloudops_orchestrator.states import CaseState as S

INSTANCE = "__DEMO_INSTANCE_ID__"
GOOD = {"spam": "PASS", "virus": "PASS", "spf": "PASS", "dkim": "PASS", "dmarc": "PASS"}
CFG = Config(operator_emails=("op@x.com",), approver_emails=("boss@x.com",), token_pepper="pepper",
             demo_instance_id=INSTANCE, approval_expiry_minutes=30)


def make(repos, version_bump=False, capability="terminate_process"):
    repos.case.transition(CASE, S.INVESTIGATING)
    repos.case.transition(CASE, S.DIAGNOSED)
    params = {"process_id": 4242, "expected_process_name": "yes"} if capability == "terminate_process" else {}
    plan, token = build_plan(repos, CFG, CASE, INSTANCE, capability, params, ["E1"],
                             "yes is hot", "stops yes", "low", "fresh READ")
    repos.case.transition(CASE, S.AWAITING_APPROVAL)
    return plan, token


def msg(plan, sender="boss@x.com", intent="APPROVAL", case=CASE, mid="<m1>"):
    return {"message_id": mid, "from": [sender],
            "classification": {"intent": intent, "case_id": case, "plan_id": plan["planId"], "approval_token_present": True}}


def check(repos, plan, token, **kw):
    return validate_approval(repos, CFG, msg(plan, **kw), token, GOOD)


def test_happy_path_and_state(repos):
    plan, token = make(repos)
    r = authorize_approval(repos, CFG, msg(plan), token, GOOD)
    assert r.ok and r.reason == "OK"
    assert repos.case.state(CASE) is S.APPROVED
    assert repos.plan.get(CASE, plan["planId"])["planStatus"] == "APPROVED"


def test_duplicate_delivery_cannot_approve_twice(repos):
    plan, token = make(repos)
    assert authorize_approval(repos, CFG, msg(plan), token, GOOD).ok
    second = authorize_approval(repos, CFG, msg(plan, mid="<m2>"), token, GOOD)
    assert not second.ok and second.reason in ("PLAN_NOT_PENDING", "CASE_STATE_INVALID")


def test_wrong_token(repos):
    plan, token = make(repos)
    assert check(repos, plan, "wrong").reason == "TOKEN_INVALID"
    assert check(repos, plan, None).reason == "TOKEN_INVALID"
    assert check(repos, plan, token).ok


def test_token_for_other_plan_rejected(repos):
    plan, token = make(repos)
    other, other_token = build_plan(repos, CFG, CASE, INSTANCE, "terminate_process",
                                    {"process_id": 99, "expected_process_name": "yes"}, [], "d", "i", "r", "v")
    assert check(repos, other, token).reason == "TOKEN_INVALID"   # v1's token on v2
    assert check(repos, other, other_token).ok


def test_unauthorized_sender_and_operator_cannot_approve(repos):
    plan, token = make(repos)
    assert check(repos, plan, token, sender="stranger@evil.com").reason == "SENDER_NOT_AUTHORIZED"
    assert check(repos, plan, token, sender="op@x.com").reason == "SENDER_NOT_AUTHORIZED"
    m = msg(plan)
    m["from"] = ["boss@x.com", "evil@x.com"]
    assert validate_approval(repos, CFG, m, token, GOOD).reason == "SENDER_NOT_AUTHORIZED"


def test_sender_must_be_on_the_plan_snapshot(repos):
    plan, token = make(repos)
    later = Config(**{**CFG.__dict__, "approver_emails": ("boss@x.com", "newbie@x.com")})
    m = msg(plan, sender="newbie@x.com")
    assert validate_approval(repos, later, m, token, GOOD).reason == "SENDER_NOT_AUTHORIZED"


@pytest.mark.parametrize("v", [
    {**GOOD, "dmarc": "FAIL", "spf": "FAIL"}, {**GOOD, "spam": "FAIL"}, {**GOOD, "virus": "FAIL"}, None, {},
])
def test_mail_auth_fails_closed(repos, v):
    plan, token = make(repos)
    assert validate_approval(repos, CFG, msg(plan), token, v).reason == "MAIL_AUTH_FAILED"


def test_verdict_alternatives():
    assert verdict_ok({**GOOD, "dmarc": "GRAY"})                      # SPF + DKIM both pass
    assert not verdict_ok({**GOOD, "dmarc": "GRAY", "dkim": "FAIL"})


def test_expired(repos):
    plan, token = make(repos)
    later = datetime.now(timezone.utc) + timedelta(minutes=31)
    assert validate_approval(repos, CFG, msg(plan), token, GOOD, now=later).reason == "PLAN_EXPIRED"


def test_superseded_old_version_cannot_be_approved(repos):
    plan1, token1 = make(repos)
    build_plan(repos, CFG, CASE, INSTANCE, "terminate_process",
               {"process_id": 77, "expected_process_name": "yes"}, [], "d", "i", "r", "v")
    assert check(repos, plan1, token1).reason == "PLAN_NOT_LATEST"
    assert repos.plan.get(CASE, plan1["planId"])["planStatus"] == "SUPERSEDED"


def test_case_state_must_be_awaiting_approval(repos):
    plan, token = make(repos)
    repos.case.transition(CASE, S.NO_ACTION)
    assert check(repos, plan, token).reason == "CASE_STATE_INVALID"


def test_case_and_plan_mismatch_and_unknowns(repos):
    plan, token = make(repos)
    assert check(repos, plan, token, case="CG-AAAAAAAAAAAA").reason == "CASE_PLAN_MISMATCH"
    bad = msg(plan)
    bad["classification"]["plan_id"] = "PLAN-52650C02BEB8V9"
    assert validate_approval(repos, CFG, bad, token, GOOD).reason == "PLAN_NOT_FOUND"
    bad["classification"]["plan_id"] = None
    assert validate_approval(repos, CFG, bad, token, GOOD).reason == "PLAN_ID_INVALID"


def test_generic_yes_is_not_approval(repos):
    plan, token = make(repos)
    m = msg(plan, intent="CASE_FOLLOWUP")
    assert validate_approval(repos, CFG, m, "YES", GOOD).reason == "NOT_AN_APPROVAL"
    assert repos.case.state(CASE) is S.AWAITING_APPROVAL


def test_tampered_parameters_detected(repos):
    plan, token = make(repos)
    repos.plan.store.table.update_item(
        Key={"PK": f"CASE#{CASE}", "SK": f"PLAN#{plan['planId']}"},
        UpdateExpression="SET parametersJson = :p",
        ExpressionAttributeValues={":p": '{"expected_process_name":"sshd","plan_id":"%s","process_id":4242}' % plan["planId"]})
    assert check(repos, plan, token).reason == "PLAN_INTEGRITY"


def test_resource_is_rechecked_against_allowlist(repos):
    plan, token = make(repos)
    other = Config(**{**CFG.__dict__, "demo_instance_id": "i-0000000000000000"})
    assert validate_approval(repos, other, msg(plan), token, GOOD).reason == "PLAN_INTEGRITY"


def test_reject_then_approve_race_has_one_winner(repos):
    plan, token = make(repos)
    rej = authorize_rejection(repos, CFG, msg(plan, sender="op@x.com", intent="REJECTION"), GOOD)
    assert rej.ok and repos.case.state(CASE) is S.REJECTED
    assert not authorize_approval(repos, CFG, msg(plan), token, GOOD).ok
    assert repos.plan.get(CASE, plan["planId"])["planStatus"] == "REJECTED"


def test_reject_needs_known_sender_and_clean_mail(repos):
    plan, token = make(repos)
    assert authorize_rejection(repos, CFG, msg(plan, sender="x@evil.com", intent="REJECTION"), GOOD).reason == "SENDER_NOT_AUTHORIZED"
    assert authorize_rejection(repos, CFG, msg(plan, sender="op@x.com", intent="REJECTION"), {}).reason == "MAIL_AUTH_FAILED"
    assert repos.case.state(CASE) is S.AWAITING_APPROVAL


def test_plaintext_token_never_persisted(repos):
    plan, token = make(repos)
    authorize_approval(repos, CFG, msg(plan), token, GOOD)
    authorize_approval(repos, CFG, msg(plan, mid="<m2>"), "wrong-guess-token", GOOD)
    dump = str(repos.plan.store.table.scan()["Items"])
    assert token not in dump and "wrong-guess-token" not in dump


def test_denied_attempts_are_recorded(repos):
    plan, token = make(repos)
    authorize_approval(repos, CFG, msg(plan), "bad", GOOD)
    rec = repos.approval.list(CASE, plan["planId"])
    assert rec[0]["decision"] == "DENIED" and rec[0]["reason"] == "TOKEN_INVALID"


def test_reboot_plan_roundtrip(repos):
    plan, token = make(repos, capability="reboot_demo_instance")
    assert plan["parameters"] == {"plan_id": plan["planId"]}
    assert authorize_approval(repos, CFG, msg(plan), token, GOOD).ok


@pytest.mark.parametrize("cap,params", [
    ("run_shell_command", {}),
    ("terminate_process", {"process_id": 1, "expected_process_name": "yes"}),
    ("terminate_process", {"process_id": 5, "expected_process_name": "yes; reboot"}),
    ("terminate_process", {"process_id": True, "expected_process_name": "yes"}),
    ("terminate_process", {"process_id": 5}),
    ("terminate_process", {"process_id": 5, "expected_process_name": "yes", "extra": 1}),
    ("reboot_demo_instance", {"instance_id": "i-1"}),
])
def test_plan_builder_rejects_non_contract_parameters(repos, cap, params):
    with pytest.raises(PlanError):
        build_plan(repos, CFG, CASE, INSTANCE, cap, params, [], "d", "i", "r", "v")


def test_plan_builder_rejects_other_resource_and_no_approvers(repos):
    with pytest.raises(PlanError):
        build_plan(repos, CFG, CASE, "i-other", "reboot_demo_instance", {}, [], "d", "i", "r", "v")
    with pytest.raises(PlanError):
        build_plan(repos, Config(**{**CFG.__dict__, "approver_emails": ()}), CASE, INSTANCE,
                   "reboot_demo_instance", {}, [], "d", "i", "r", "v")
