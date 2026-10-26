"""Post-ACTION verification entrypoint, driven by the VerificationWorkflow state machine."""
import logging
from datetime import datetime, timezone

from ..emails.approval_result import render_result
from ..emails.escalation import render_escalation
from ..executor import precheck
from ..services import build_services
from ..states import CaseState as S
from ..verification import collect, describe, evaluate

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_services = None
WAIT_BETWEEN = 60


def _everyone(cfg):
    return sorted(set(cfg.operator_emails) | set(cfg.approver_emails))


def _timeline(repos, case_id, plan, final_label):
    lines = []
    for a in repos.approval.list(case_id, plan["planId"]):
        if a["decision"] == "APPROVED":
            lines.append(f"{a['createdAt'][11:19]} UTC  approved by {a['sender']}")
    ex = repos.execution.get(case_id, plan["planId"])
    if ex:
        lines.append(f"{ex['claimedAt'][11:19]} UTC  action started ({plan['capability']})")
        if ex.get("finishedAt"):
            lines.append(f"{ex['finishedAt'][11:19]} UTC  action returned: {ex.get('outcome')}")
    lines.append(f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC  {final_label}")
    return lines


def _send(svc, case_id, rendered, event):
    try:
        mid = svc.ses.send(_everyone(svc.cfg), *rendered)
        svc.repos.audit.append(case_id, event, {"sesMessageId": mid})
    except Exception as error:  # noqa: BLE001
        svc.repos.audit.append(case_id, "EMAIL_FAILED", {"event": event, "error": type(error).__name__})


def handle_verify(event, svc):
    """One verification round. Returns the state-machine payload: {done, attempt, waitSeconds, ...}."""
    case_id, plan_id, attempt = event["caseId"], event["planId"], int(event.get("attempt", 0))
    repos, cfg = svc.repos, svc.cfg
    state = repos.case.state(case_id)
    base = {"caseId": case_id, "planId": plan_id, "attempt": attempt}
    if state is not S.VERIFYING:                       # already finished or taken over: nothing to do
        return {**base, "done": True, "state": state.value if state else None}
    plan = repos.plan.get(case_id, plan_id)

    checks, detail = collect(svc.read, plan)
    passed, blocking = evaluate(plan, checks)
    repos.verification.add(case_id, plan_id, attempt + 1, {"checks": checks, "detail": detail, "blocking": blocking}, passed)
    after = describe(checks, detail)
    before = (repos.case.get(case_id) or {}).get("diagnosisSummary", "")

    if passed:
        if repos.case.transition(case_id, S.RESOLVED, [S.VERIFYING]):
            repos.audit.append(case_id, "RESOLVED", {"planId": plan_id, "attempt": attempt + 1})
            ex = repos.execution.get(case_id, plan_id) or {}
            _send(svc, case_id, render_result(case_id, plan, "RESOLVED", before, after, ex.get("outcome"),
                                              timeline=_timeline(repos, case_id, plan, "recovery verified")), "RESULT_SENT")
        return {**base, "done": True, "state": "RESOLVED"}

    if attempt + 1 >= cfg.verification_max_attempts:
        still_there = checks.get("process_gone") is False
        final = S.REMEDIATION_FAILED if still_there else S.ESCALATED
        if repos.case.transition(case_id, final, [S.VERIFYING], escalationReason=f"not verified: {', '.join(blocking)}"[:300]):
            repos.audit.append(case_id, "VERIFICATION_FAILED", {"planId": plan_id, "blocking": blocking, "final": final.value})
            note = ("The process is still running." if still_there else
                    "The action ran, but recovery could not be confirmed in time. Please check the instance.")
            _send(svc, case_id, render_result(case_id, plan, final.value, before, after,
                                              (repos.execution.get(case_id, plan_id) or {}).get("outcome"), note=note,
                                              timeline=_timeline(repos, case_id, plan, "gave up verifying")), "RESULT_SENT")
        return {**base, "done": True, "state": final.value}

    return {**base, "attempt": attempt + 1, "done": False, "waitSeconds": WAIT_BETWEEN, "blocking": blocking}


def handle_abort(event, svc):
    """Verification workflow crashed: do not leave the case in VERIFYING forever."""
    case_id = event["caseId"]
    if svc.repos.case.transition(case_id, S.ESCALATED, [S.VERIFYING], escalationReason="verification workflow failed"):
        svc.repos.audit.append(case_id, "VERIFICATION_ABORTED", {})
        case = svc.repos.case.get(case_id)
        _send(svc, case_id, render_escalation({**case, "caseId": case_id},
              "The action ran but verification could not complete. Please check the instance manually."), "RESULT_SENT")
    return {"caseId": case_id, "done": True, "state": "ESCALATED"}


def handle_probe(event, svc):
    """READ-ONLY dry check of the live path: what the pre-action check and verification would see now."""
    cap = event.get("capability", "reboot_demo_instance")
    plan = {"capability": cap, "resource": svc.cfg.demo_instance_id,
            "parameters": ({"process_id": int(event["processId"]), "expected_process_name": event["processName"]}
                           if cap == "terminate_process" else {})}
    decision, reason = precheck(svc.read, svc.cfg, plan)
    checks, detail = collect(svc.read, plan)
    passed, blocking = evaluate(plan, checks)
    return {"precheck": {"decision": decision, "reason": reason}, "verification": {"passed": passed, "blocking": blocking,
            "checks": checks, "detail": detail}}


def lambda_handler(event, context):
    global _services
    _services = _services or build_services()
    if event.get("probe"):
        return handle_probe(event, _services)
    if event.get("abort"):
        return handle_abort(event, _services)
    out = handle_verify(event, _services)
    logger.info("verify: %s", {k: out.get(k) for k in ("caseId", "attempt", "done", "state")})
    return out
