import pytest
from conftest import CASE
from fakes import (INSTANCE, PLAN1, FakeAction, FakeRead, FakeSes, approve_msg, cfg, listing, make_plan, recovered, row, services)
from cloudops_orchestrator.handlers.entry import lambda_handler as entry
from cloudops_orchestrator.handlers.verification_handler import handle_abort, handle_probe, handle_verify
from cloudops_orchestrator.router import Router
from cloudops_orchestrator.states import CaseState as S
from cloudops_orchestrator.verification import describe, evaluate, process_state
from fakes import GOOD


def verifying_case(repos, capability="terminate_process", **svc_kw):
    """Run the real approval path so the case, plan, approval log and execution record are all genuine."""
    plan, token = make_plan(repos, capability=capability)
    svc, ses = services(repos, **svc_kw)
    Router(svc).route(approve_msg(plan), {"verdicts": GOOD, "token": token})
    assert repos.case.state(CASE) is S.VERIFYING
    return plan, svc, ses


def verify(svc, plan, attempt=0):
    return handle_verify({"caseId": CASE, "planId": plan["planId"], "attempt": attempt}, svc)


def test_resolved_only_when_every_check_passes_and_everyone_is_mailed_with_a_timeline(repos):
    plan, svc, ses = verifying_case(repos)
    svc.read = recovered()
    out = verify(svc, plan)
    assert out == {"caseId": CASE, "planId": PLAN1, "attempt": 0, "done": True, "state": "RESOLVED"}
    assert repos.case.state(CASE) is S.RESOLVED
    dest, body = ses.mails()[-1]
    assert sorted(dest) == ["boss2@x.com", "boss@x.com", "op@x.com"]
    assert "RESOLVED - recovery verified" in body and "approved by boss@x.com" in body and "process gone: yes" in body
    assert "CPU alarm back to OK: yes" in body and "AFTER (fresh checks)" in body and "TIMELINE" in body
    v = repos.verification.list(CASE, PLAN1)[0]
    assert v["passed"] is True and v["checks"]["blocking"] == []


def test_api_success_alone_never_resolves(repos):
    plan, svc, ses = verifying_case(repos)             # action returned SUCCESS, but the world still looks broken
    out = verify(svc, plan)
    assert out["done"] is False and out["attempt"] == 1 and out["waitSeconds"] == 60
    assert repos.case.state(CASE) is S.VERIFYING and out["blocking"] == ["process_gone", "alarm_ok", "cpu_ok"]


@pytest.mark.parametrize("over,blocked", [
    (dict(process={"status": "FOUND", "output": listing(row(4242, "yes", 99.0))}), "process_gone"),
    (dict(alarm={"stateValue": "ALARM", "threshold": 80.0}), "alarm_ok"),
    (dict(cpu={"datapoints": [{"Timestamp": "t", "Average": 95.0}]}), "cpu_ok"),
    (dict(status={"instanceState": "running", "systemStatus": "ok", "instanceStatus": "initializing"}), "instance_ok"),
    (dict(alarm={"stateValue": "INSUFFICIENT_DATA"}), "alarm_ok"),
    (dict(alarm=RuntimeError("down")), "alarm_ok"),
    (dict(process=RuntimeError("down")), "process_gone"),
    (dict(process={"status": "INCONCLUSIVE"}), "process_gone"),
    (dict(cpu={"datapoints": []}), "cpu_ok"),
    (dict(status=RuntimeError("down")), "instance_ok"),
])
def test_any_failed_or_unknown_check_blocks_resolution(repos, over, blocked):
    plan, svc, _ = verifying_case(repos)
    svc.read = recovered(**over)
    out = verify(svc, plan)
    assert out["done"] is False and blocked in out["blocking"] and repos.case.state(CASE) is S.VERIFYING


def test_pid_reused_by_another_program_counts_as_original_gone():
    assert process_state({"status": "FOUND", "output": listing(row(4242, "postgres", 5.0))}, "yes") == ("GONE", {"pid": 4242, "comm": "postgres", "cpu": 5.0})
    assert process_state({"status": "NOT_FOUND"}, "yes")[0] == "GONE"
    assert process_state({"status": "INCONCLUSIVE"}, "yes")[0] == "UNKNOWN" and process_state(None, "yes")[0] == "UNKNOWN"


def test_a_leftover_hot_process_is_reported_but_does_not_block_when_the_alarm_cleared(repos):
    plan, svc, ses = verifying_case(repos)
    svc.read = recovered(top={"output": listing(row(5000, "yes", 99.0), row(77, "ssm-document-wo", 70.0))})
    assert verify(svc, plan)["state"] == "RESOLVED"
    body = ses.mails()[-1][1]
    assert "still busy: yes(pid 5000) 99.0%" in body and "ssm-document-wo" not in body        # diagnostics are ignored


def test_gives_up_with_remediation_failed_when_the_process_is_still_there(repos):
    plan, svc, ses = verifying_case(repos)
    svc.read = FakeRead()                                                    # incident unchanged
    assert verify(svc, plan, attempt=0)["done"] is False
    assert verify(svc, plan, attempt=1)["done"] is False
    out = verify(svc, plan, attempt=2)                                       # max_attempts=3
    assert out["state"] == "REMEDIATION_FAILED" and repos.case.state(CASE) is S.REMEDIATION_FAILED
    assert "did not fix" in ses.mails()[-1][1] and "still running" in ses.mails()[-1][1]


def test_gives_up_with_escalated_when_action_ran_but_recovery_is_unconfirmed(repos):
    plan, svc, ses = verifying_case(repos)
    svc.read = recovered(alarm={"stateValue": "ALARM", "threshold": 80.0})   # process gone, alarm never cleared
    out = verify(svc, plan, attempt=2)
    assert out["state"] == "ESCALATED" and "could not be confirmed" in ses.mails()[-1][1]


def test_reboot_requires_instance_alarm_and_cpu(repos):
    plan, svc, ses = verifying_case(repos, capability="reboot_demo_instance", action=FakeAction({"status": "ACTION_ACCEPTED"}))
    svc.read = recovered(status={"instanceState": "running", "systemStatus": "ok", "instanceStatus": "initializing"})
    assert verify(svc, plan)["done"] is False                                # mid-reboot: not yet
    svc.read = recovered()
    assert verify(svc, plan, attempt=1)["state"] == "RESOLVED"
    assert "process gone" not in ses.mails()[-1][1]


def test_verification_is_idempotent_and_never_regresses_a_finished_case(repos):
    plan, svc, ses = verifying_case(repos)
    svc.read = recovered()
    verify(svc, plan)
    n = len(ses.sent)
    again = verify(svc, plan)
    assert again["done"] is True and again["state"] == "RESOLVED" and len(ses.sent) == n and repos.case.state(CASE) is S.RESOLVED


def test_results_mail_failure_does_not_undo_resolution(repos):
    plan, svc, ses = verifying_case(repos)
    svc.read = recovered()
    ses.send_raw_email = lambda **kw: (_ for _ in ()).throw(RuntimeError("ses down"))
    assert verify(svc, plan)["state"] == "RESOLVED" and repos.case.state(CASE) is S.RESOLVED
    assert "EMAIL_FAILED" in [a["event"] for a in repos.audit.list(CASE)]


def test_abort_escalates_a_stuck_verification(repos):
    plan, svc, ses = verifying_case(repos)
    assert handle_abort({"caseId": CASE}, svc)["state"] == "ESCALATED"
    assert repos.case.state(CASE) is S.ESCALATED and "check the instance manually" in ses.mails()[-1][1]
    assert handle_abort({"caseId": CASE}, svc)["state"] == "ESCALATED"          # second call changes nothing


def test_probe_is_read_only_and_reports_what_the_live_path_would_do(repos):
    svc, ses = services(repos, read=FakeRead())
    out = handle_probe({"probe": True, "capability": "terminate_process", "processId": 4242, "processName": "yes"}, svc)
    assert out["precheck"]["decision"] == "PROCEED" and out["verification"]["passed"] is False
    assert repos.case.state(CASE) is S.DETECTED and ses.sent == [] and repos.plan.list(CASE) == []      # touched nothing
    out = handle_probe({"probe": True, "capability": "reboot_demo_instance"}, services(repos, read=recovered())[0])
    assert out["precheck"]["decision"] == "NO_ACTION" and out["verification"]["passed"] is True


def test_entry_routes_verification_events(repos, monkeypatch):
    import cloudops_orchestrator.handlers.verification_handler as vh
    vh._services = services(repos, read=FakeRead())[0]
    assert entry({"probe": True, "capability": "reboot_demo_instance"}, None)["precheck"]["decision"] == "PROCEED"
    assert entry({"abort": True, "caseId": CASE}, None)["done"] is True


def test_describe_and_evaluate_are_strict():
    plan = {"capability": "terminate_process"}
    ok, blocking = evaluate(plan, {"process_gone": True, "instance_ok": True, "alarm_ok": True, "cpu_ok": None})
    assert not ok and blocking == ["cpu_ok"]
    assert "unknown" in describe({"process_gone": None, "instance_ok": True, "alarm_ok": False, "cpu_ok": True}, {"alarm": "ALARM"})
