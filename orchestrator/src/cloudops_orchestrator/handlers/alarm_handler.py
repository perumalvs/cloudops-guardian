"""Invoked by IncidentWorkflow after case creation: investigate -> plan -> first SES email (or ESCALATED)."""
import logging

from ..investigation import run_investigation
from ..models import PLAN_PROPOSED, PLAN_SUPERSEDED
from ..states import CaseState as S
from ..notifier import send_investigation_mail
from ..services import build_services

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_services = None


def handle(event, services):
    """event: IncidentHandler output {caseId, status, sourceEventId}. Returns a small summary."""
    case_id, status = event.get("caseId"), event.get("status")
    if not case_id:
        raise ValueError("event has no caseId")
    if status == "DUPLICATE":
        return {"caseId": case_id, "result": "SKIPPED_DUPLICATE"}

    inv = run_investigation(services.repos, services.cfg, services.reasoning, case_id)
    if inv is None:
        return {"caseId": case_id, "result": "SKIPPED_ALREADY_HANDLED"}
    try:
        sent = send_investigation_mail(services.ses, services.cfg, inv)
    except Exception as error:  # noqa: BLE001
        services.repos.audit.append(case_id, "EMAIL_FAILED", {"error": type(error).__name__})
        if inv.plan:
            # Nobody holds the approval token, so the plan must not stay approvable.
            services.repos.plan.set_status(case_id, inv.plan["planId"], PLAN_SUPERSEDED, [PLAN_PROPOSED])
            services.repos.case.transition(case_id, S.ESCALATED, [S.AWAITING_APPROVAL],
                                           escalationReason="notification failed; plan withdrawn")
        raise
    services.repos.audit.append(case_id, "EMAIL_SENT", {"audiences": [a for a, _ in sent], "sesMessageIds": [m for _, m in sent], "state": inv.state.value})
    return {"caseId": case_id, "result": inv.state.value, "planId": inv.plan["planId"] if inv.plan else None,
            "emailed": [a for a, _ in sent]}


def lambda_handler(event, context):
    global _services
    _services = _services or build_services()
    summary = handle(event, _services)
    logger.info("alarm handled: %s", summary)      # ids and state only: no bodies, no tokens
    return summary
