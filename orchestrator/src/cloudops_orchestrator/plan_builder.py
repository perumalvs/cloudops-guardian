"""Creates immutable PLAN-ID/version bound to resource, capability, exact params, expiry."""
import re
from datetime import datetime, timedelta, timezone

from .models import PLAN_PROPOSED, PLAN_SUPERSEDED, WRITE_CAPABILITIES, make_plan_id
from .tokens import generate_token, hash_token

PROCESS_NAME_RE = re.compile(r"^[A-Za-z0-9._+-]{1,15}$")  # ps comm is at most 15 chars


class PlanError(ValueError):
    pass


def validate_parameters(capability, parameters, plan_id):
    """Exact parameter contract of the ACTION tools; raises PlanError."""
    if capability == "terminate_process":
        if set(parameters) != {"process_id", "expected_process_name", "plan_id"}:
            raise PlanError("terminate_process parameters must be exactly process_id, expected_process_name, plan_id")
        pid = parameters["process_id"]
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 1:
            raise PlanError("process_id must be an integer greater than 1")
        if not PROCESS_NAME_RE.match(str(parameters["expected_process_name"])):
            raise PlanError("expected_process_name is invalid")
    elif capability == "reboot_demo_instance":
        if set(parameters) != {"plan_id"}:
            raise PlanError("reboot_demo_instance parameters must be exactly plan_id")
    else:
        raise PlanError(f"capability not allowed for a plan: {capability}")
    if parameters["plan_id"] != plan_id:
        raise PlanError("parameters.plan_id must equal the plan id")


def build_plan(repos, cfg, case_id, resource, capability, action_params, evidence_refs,
               diagnosis, impact, risk, verification, now=None, meta=None):
    """
    Create the next plan version for a case and supersede older pending ones.
    Returns (plan_item, plaintext_token). The token is returned once and
    only its keyed hash is persisted.
    """
    if capability not in WRITE_CAPABILITIES:
        raise PlanError(f"capability not allowed for a plan: {capability}")
    if not cfg.demo_instance_id or resource != cfg.demo_instance_id:
        raise PlanError("resource is not the allowlisted instance")
    if not cfg.approver_emails:
        raise PlanError("no authorized approvers configured")

    now = now or datetime.now(timezone.utc)
    expires_at = (now + timedelta(minutes=cfg.approval_expiry_minutes)).isoformat()

    for _ in range(3):  # a concurrent builder may take the version first
        pending = [p for p in repos.plan.list(case_id) if p["planStatus"] == PLAN_PROPOSED]
        supersedes = max(pending, key=lambda p: p["planVersion"])["planId"] if pending else None
        version = repos.plan.next_version(case_id)
        plan_id = make_plan_id(case_id, version)
        parameters = {**action_params, "plan_id": plan_id}
        validate_parameters(capability, parameters, plan_id)
        token = generate_token()
        plan = repos.plan.create(
            case_id, version, resource, capability, parameters, evidence_refs, diagnosis,
            impact, risk, verification, cfg.approver_emails,
            hash_token(token, plan_id, cfg.token_pepper), expires_at,
            extra={**(meta or {}), "supersedes": supersedes},
        )
        if plan:
            for older in repos.plan.list(case_id):
                if older["planVersion"] < version:
                    repos.plan.set_status(case_id, older["planId"], PLAN_SUPERSEDED, [PLAN_PROPOSED])
            repos.audit.append(case_id, "PLAN_CREATED", {
                "planId": plan_id, "capability": capability, "parameters": parameters, "expiresAt": expires_at})
            return plan, token
    raise PlanError("could not allocate a plan version")
