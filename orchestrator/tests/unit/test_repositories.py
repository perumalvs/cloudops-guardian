import pytest
from conftest import CASE
from cloudops_orchestrator.states import CaseState as S

PLAN = f"PLAN-{CASE[3:]}V1"
PARAMS = {"process_id": 4242, "expected_process_name": "yes", "plan_id": PLAN}


def new_plan(repos, version=1):
    return repos.plan.create(
        CASE, version, "__DEMO_INSTANCE_ID__", "terminate_process", PARAMS, ["EVIDENCE#1"],
        "yes consuming CPU", "stops yes", "low", "fresh READ", ["a@x.com"], "hash", "2999-01-01T00:00:00+00:00")


def test_legacy_open_case_is_detected(repos):
    assert repos.case.state(CASE) is S.DETECTED


def test_transition_chain_and_guard(repos):
    assert repos.case.transition(CASE, S.INVESTIGATING)
    assert not repos.case.transition(CASE, S.EXECUTING)       # illegal jump refused by DB condition
    assert not repos.case.transition(CASE, S.RESOLVED)
    assert repos.case.state(CASE) is S.INVESTIGATING


def test_concurrent_transition_exactly_one_wins(repos):
    assert repos.case.transition(CASE, S.INVESTIGATING, expected_from=[S.DETECTED])
    assert not repos.case.transition(CASE, S.INVESTIGATING, expected_from=[S.DETECTED])


def test_transition_bumps_version_and_sets_fields(repos):
    repos.case.transition(CASE, S.INVESTIGATING, diagnosisRef="E1")
    item = repos.case.get(CASE)
    assert item["stateVersion"] == 1 and item["diagnosisRef"] == "E1"


def test_transition_on_unknown_case_is_false_not_created(repos):
    assert not repos.case.transition("CG-AAAAAAAAAAAA", S.INVESTIGATING)
    assert repos.case.get("CG-AAAAAAAAAAAA") is None


def test_evidence_roundtrip_with_floats(repos):
    repos.evidence.add(CASE, "READ", "get_demo_instance_cpu_metrics", {"avg": 97.5})
    repos.evidence.add(CASE, "READ", "get_top_cpu_processes", {"pid": 4242})
    ev = repos.evidence.list(CASE)
    assert [e["tool"] for e in ev] == ["get_demo_instance_cpu_metrics", "get_top_cpu_processes"]
    assert ev[0]["payload"]["avg"] == 97.5


def test_plan_create_once_and_binds_params(repos):
    p = new_plan(repos)
    assert p["planId"] == PLAN and new_plan(repos) is None     # immutable: cannot recreate same version
    got = repos.plan.get(CASE, PLAN)
    assert got["parameters"] == PARAMS and got["planStatus"] == "PROPOSED"
    assert repos.plan.next_version(CASE) == 2


def test_token_single_use(repos):
    new_plan(repos)
    assert repos.plan.consume_token(CASE, PLAN) is True
    assert repos.plan.consume_token(CASE, PLAN) is False       # replay
    assert repos.plan.get(CASE, PLAN)["planStatus"] == "APPROVED"


def test_token_cannot_be_consumed_after_rejection(repos):
    new_plan(repos)
    assert repos.plan.set_status(CASE, PLAN, "REJECTED", ["PROPOSED"])
    assert repos.plan.consume_token(CASE, PLAN) is False


def test_plan_status_conditional(repos):
    new_plan(repos)
    assert not repos.plan.set_status(CASE, PLAN, "EXPIRED", ["APPROVED"])
    assert repos.plan.set_status(CASE, PLAN, "SUPERSEDED", ["PROPOSED"])


def test_execute_once(repos):
    assert repos.execution.claim(CASE, PLAN, "terminate_process", "h") is True
    assert repos.execution.claim(CASE, PLAN, "terminate_process", "h") is False
    assert repos.execution.finish(CASE, PLAN, "SUCCESS", {"x": 1})
    assert not repos.execution.finish(CASE, PLAN, "SUCCESS", {"x": 2})
    assert repos.execution.get(CASE, PLAN)["outcome"] == "SUCCESS"


def test_message_dedup(repos):
    assert repos.conversation.claim_message("<abc@mail>", CASE, "APPROVAL") is True
    assert repos.conversation.claim_message("<abc@mail>", CASE, "APPROVAL") is False
    assert repos.conversation.claim_message("<other@mail>") is True


def test_audit_is_redacted(repos):
    repos.audit.append(CASE, "APPROVAL_ATTEMPT", {"body": "APPROVE PLAN-52650C02BEB8V1 topsecret", "approvalToken": "topsecret"})
    s = repos.audit.list(CASE)[0]["summary"]
    assert "topsecret" not in str(s)


def test_verification_and_approval_logs(repos):
    repos.verification.add(CASE, PLAN, 1, {"pid_absent": True, "cpu_ok": False}, False)
    repos.approval.record(CASE, PLAN, "DENIED", "SENDER_NOT_AUTHORIZED", "x@y.com", "<m1>")
    assert repos.verification.list(CASE, PLAN)[0]["checks"]["pid_absent"] is True
    assert repos.approval.list(CASE, PLAN)[0]["reason"] == "SENDER_NOT_AUTHORIZED"
