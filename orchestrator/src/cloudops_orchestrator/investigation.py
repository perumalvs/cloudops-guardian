"""Calls reasoning Runtime; persists evidence/diagnosis; ranks allowlisted outcomes only."""
import json
from dataclasses import dataclass, field

from .models import WRITE_CAPABILITIES
from .repositories._base import now as now_iso
from .plan_builder import build_plan
from .states import CaseState as S

VERIFY = {
    "terminate_process": "Fresh READ: get_process_details shows the PID is gone, CPU metrics and top processes are normal, "
                         "EC2 status checks are ok, and the CPU alarm is back to OK.",
    "reboot_demo_instance": "Fresh READ after restart: instance running with ok status checks, CPU metrics normal, "
                            "top processes normal, and the CPU alarm is back to OK.",
}


@dataclass
class Investigation:
    case_id: str
    state: S
    case: dict = field(default_factory=dict)
    diagnosis: dict = field(default_factory=dict)
    evidence: list = field(default_factory=list)
    outcomes: list = field(default_factory=list)
    plan: dict = None
    token: str = None          # plaintext, in memory only, for the approvers' email
    reason: str = ""
    answer: str = ""


def case_payload(cfg, item):
    return {"caseId": item["caseId"], "alarmName": item.get("alarmName"), "resource": cfg.demo_instance_id,
            "status": "INVESTIGATING", "createdAt": item.get("createdAt")}


def _escalate(repos, inv, reason, frm):
    repos.case.transition(inv.case_id, S.ESCALATED, [frm], escalationReason=reason[:300])
    repos.audit.append(inv.case_id, "ESCALATED", {"reason": reason})
    inv.state, inv.reason = S.ESCALATED, reason
    return inv


def run_investigation(repos, cfg, reasoning, case_id):
    """
    Idempotent entry for a new case. Only the caller that wins
    DETECTED -> INVESTIGATING does any work; duplicates return None.
    """
    item = repos.case.get(case_id)
    if item is None:
        raise LookupError(f"case not found: {case_id}")
    if not repos.case.transition(case_id, S.INVESTIGATING, [S.DETECTED]):
        repos.audit.append(case_id, "INVESTIGATION_SKIPPED", {"state": str(repos.case.state(case_id))})
        return None
    repos.audit.append(case_id, "INVESTIGATION_STARTED", {})
    inv = Investigation(case_id, S.INVESTIGATING, case={**item, "resource": cfg.demo_instance_id})

    try:
        result = reasoning.invoke("investigate", case_id, case=case_payload(cfg, item))
    except Exception as error:  # noqa: BLE001 - never leak model/transport detail into state or mail
        return _escalate(repos, inv, f"Investigation could not be completed ({type(error).__name__}).", S.INVESTIGATING)

    return conclude(repos, cfg, inv, result)


def persist_result(repos, case_id, result, requested_by="reasoning"):
    """Store the READ evidence, diagnosis and outcomes of one reasoning run. Returns evidence refs."""
    refs = [repos.evidence.add(case_id, "READ", e["tool"],
                               {"arguments": e["arguments"], "summary": e["summary"], "data": e["data"]},
                               requested_by=requested_by) for e in result["evidence"]]
    refs.append(repos.evidence.add(case_id, "DIAGNOSIS", "reasoning", result["diagnosis"]))
    repos.evidence.add(case_id, "OUTCOMES", "reasoning", result["outcomes"])
    for dropped in result["dropped"]:
        repos.audit.append(case_id, "OUTCOME_DROPPED", {"capability": dropped})
    return refs


def conclude(repos, cfg, inv, result, requested_by="reasoning", proposer="agent", via="investigation", reason_note=None):
    """
    Turn a validated reasoning result into case state. The case must be INVESTIGATING.
    Only a rank-1 write outcome ever becomes a plan.
    """
    case_id = inv.case_id
    refs = persist_result(repos, case_id, result, requested_by)
    # The numbered options are a FIXED contract for this plan: stored once, never renumbered by follow-ups.
    options = [{**o, "n": i, "rank": i} for i, o in enumerate(result["outcomes"], 1)]
    inv.diagnosis, inv.evidence, inv.outcomes = result["diagnosis"], result["evidence"], options
    inv.answer = result.get("answer", "")
    repos.case.set_fields(case_id, optionsJson=json.dumps(options, default=str), diagnosisJson=json.dumps(result["diagnosis"], default=str))

    top = result["outcomes"][0]
    repos.case.transition(case_id, S.DIAGNOSED, [S.INVESTIGATING], diagnosisSummary=result["diagnosis"]["summary"][:500])
    inv.state = S.DIAGNOSED

    if top["capability"] == "CONTINUE_MONITORING":
        repos.case.transition(case_id, S.NO_ACTION, [S.DIAGNOSED])
        repos.audit.append(case_id, "NO_ACTION", {"confidence": result["diagnosis"]["confidence"]})
        inv.state = S.NO_ACTION
        return inv
    if top["capability"] not in WRITE_CAPABILITIES:     # ESCALATE
        return _escalate(repos, inv, top.get("rationale") or "No safe automated resolution.", S.DIAGNOSED)

    # Only a rank-1 write outcome ever becomes a plan.
    params = top["parameters"] if top["capability"] == "terminate_process" else {}
    try:
        plan, token = build_plan(
            repos, cfg, case_id, cfg.demo_instance_id, top["capability"],
            {k: params.get(k) for k in ("process_id", "expected_process_name")} if params else {},
            refs, result["diagnosis"]["summary"], top["impact"] or "See plan.", top["risk"] or "See plan.",
            VERIFY[top["capability"]],
            meta={"proposedBy": proposer, "proposedVia": via, "proposedAt": now_iso(), "optionNumber": 1, "agentRank": 1,
                  "reason": reason_note})
    except ValueError as error:      # PlanError is a ValueError; also covers a malformed case id
        return _escalate(repos, inv, f"Proposed action was not a valid governed plan: {error}", S.DIAGNOSED)

    if not repos.case.transition(case_id, S.AWAITING_APPROVAL, [S.DIAGNOSED], planId=plan["planId"]):
        repos.plan.set_status(case_id, plan["planId"], "SUPERSEDED", ["PROPOSED"])
        return _escalate(repos, inv, "Could not move the case to awaiting approval.", S.DIAGNOSED)
    inv.plan, inv.token, inv.state = plan, token, S.AWAITING_APPROVAL
    return inv
