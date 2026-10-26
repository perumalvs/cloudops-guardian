"""Independent approval checks: approver, SES verdict, case/plan/version, token hash, expiry, state, params."""
from dataclasses import dataclass
from datetime import datetime, timezone

from .models import (PLAN_APPROVED, PLAN_PROPOSED, PLAN_REJECTED, WRITE_CAPABILITIES,
                     params_hash, parse_plan_id)
from .plan_builder import PlanError, validate_parameters
from .states import CaseState
from .tokens import verify_token


@dataclass
class Result:
    ok: bool
    reason: str = "OK"
    case_id: str = None
    plan: dict = None


def _fail(reason, case_id=None, plan=None):
    return Result(False, reason, case_id, plan)


def verdict_ok(verdicts):
    """
    SES verdicts must show the mail is clean and authentically from its From
    domain: spam and virus PASS, and DMARC PASS (aligned) or both SPF and DKIM
    PASS. A missing verdict fails closed.
    """
    v = {k: str((verdicts or {}).get(k, "")).upper() for k in ("spam", "virus", "spf", "dkim", "dmarc")}
    clean = v["spam"] == "PASS" and v["virus"] == "PASS"
    authentic = v["dmarc"] == "PASS" or (v["spf"] == "PASS" and v["dkim"] == "PASS")
    return clean and authentic


def _sender(msg):
    senders = [a.lower() for a in msg.get("from") or []]
    return senders[0] if len(senders) == 1 else None  # ambiguous From is never trusted


def _expired(plan, now):
    return now >= datetime.fromisoformat(plan["expiresAt"])


def _load(repos, msg):
    """Common lookups. Returns (error_reason, case_id, plan)."""
    cls = msg.get("classification") or {}
    try:
        case_id, version = parse_plan_id(cls.get("plan_id"))
    except ValueError:
        return "PLAN_ID_INVALID", None, None
    if cls.get("case_id") and cls["case_id"].upper() != case_id:
        return "CASE_PLAN_MISMATCH", case_id, None
    if not repos.case.get(case_id):
        return "CASE_NOT_FOUND", case_id, None
    plan = repos.plan.get(case_id, cls["plan_id"].upper())
    if not plan:
        return "PLAN_NOT_FOUND", case_id, None
    if plan["caseId"] != case_id or plan["planVersion"] != version:
        return "PLAN_INTEGRITY", case_id, plan
    if repos.plan.next_version(case_id) - 1 != version:
        return "PLAN_NOT_LATEST", case_id, plan
    return None, case_id, plan


def validate_approval(repos, cfg, msg, token, verdicts, now=None):
    """Pure check: no state is changed. Returns Result."""
    now = now or datetime.now(timezone.utc)
    if (msg.get("classification") or {}).get("intent") != "APPROVAL":
        return _fail("NOT_AN_APPROVAL")
    sender = _sender(msg)
    if sender is None or sender not in cfg.approver_emails:
        return _fail("SENDER_NOT_AUTHORIZED")
    if cfg.require_ses_auth_pass and not verdict_ok(verdicts):
        return _fail("MAIL_AUTH_FAILED")
    error, case_id, plan = _load(repos, msg)
    if error:
        return _fail(error, case_id, plan)
    if sender not in plan["approverEmails"]:
        return _fail("SENDER_NOT_AUTHORIZED", case_id, plan)
    if plan["planStatus"] != PLAN_PROPOSED or plan.get("tokenConsumedAt"):
        return _fail("PLAN_NOT_PENDING", case_id, plan)
    if _expired(plan, now):
        return _fail("PLAN_EXPIRED", case_id, plan)
    if repos.case.state(case_id) is not CaseState.AWAITING_APPROVAL:
        return _fail("CASE_STATE_INVALID", case_id, plan)
    if not verify_token(token, plan["planId"], cfg.token_pepper, plan["tokenHash"]):
        return _fail("TOKEN_INVALID", case_id, plan)
    try:
        if plan["capability"] not in WRITE_CAPABILITIES or plan["resource"] != cfg.demo_instance_id:
            raise PlanError("capability or resource")
        validate_parameters(plan["capability"], plan["parameters"], plan["planId"])
        if params_hash(plan["parameters"]) != plan["parametersHash"]:
            raise PlanError("parameters hash")
    except PlanError:
        return _fail("PLAN_INTEGRITY", case_id, plan)
    return Result(True, "OK", case_id, plan)


def authorize_approval(repos, cfg, msg, token, verdicts, now=None):
    """
    Validate, then win the single-use gate: case AWAITING_APPROVAL -> APPROVED
    (atomic, one winner), then consume the token. Records the outcome.
    The caller may invoke ACTION only if the returned Result.ok is True.
    """
    result = validate_approval(repos, cfg, msg, token, verdicts, now)
    message_id, sender = msg.get("message_id"), _sender(msg)
    plan_id = (msg.get("classification") or {}).get("plan_id")
    if result.ok:
        if not repos.case.transition(result.case_id, CaseState.APPROVED, [CaseState.AWAITING_APPROVAL],
                                     approvedPlanId=result.plan["planId"]):
            result = _fail("ALREADY_DECIDED", result.case_id, result.plan)
        elif not repos.plan.consume_token(result.case_id, result.plan["planId"]):
            repos.case.transition(result.case_id, CaseState.ESCALATED, [CaseState.APPROVED])
            result = _fail("TOKEN_CONSUME_FAILED", result.case_id, result.plan)
    if result.case_id:
        repos.approval.record(result.case_id, plan_id or "UNKNOWN", "APPROVED" if result.ok else "DENIED",
                              result.reason, sender, message_id)
        repos.audit.append(result.case_id, "APPROVAL_ATTEMPT", {
            "planId": plan_id, "outcome": result.reason, "sender": sender, "messageId": message_id})
    return result


def authorize_rejection(repos, cfg, msg, verdicts, now=None):
    """Rejection lowers risk, so approvers and operators may reject; mail auth still applies."""
    now = now or datetime.now(timezone.utc)
    if (msg.get("classification") or {}).get("intent") != "REJECTION":
        return _fail("NOT_A_REJECTION")
    sender = _sender(msg)
    if sender is None or sender not in set(cfg.approver_emails) | set(cfg.operator_emails):
        return _fail("SENDER_NOT_AUTHORIZED")
    if cfg.require_ses_auth_pass and not verdict_ok(verdicts):
        return _fail("MAIL_AUTH_FAILED")
    error, case_id, plan = _load(repos, msg)
    if error:
        return _fail(error, case_id, plan)
    if plan["planStatus"] != PLAN_PROPOSED:
        return _fail("PLAN_NOT_PENDING", case_id, plan)
    result = Result(True, "OK", case_id, plan)
    if not repos.case.transition(case_id, CaseState.REJECTED, [CaseState.AWAITING_APPROVAL]):
        result = _fail("CASE_STATE_INVALID", case_id, plan)
    elif not repos.plan.set_status(case_id, plan["planId"], PLAN_REJECTED, [PLAN_PROPOSED]):
        result = _fail("PLAN_NOT_PENDING", case_id, plan)
    repos.approval.record(case_id, plan["planId"], "REJECTED" if result.ok else "DENIED",
                          result.reason, sender, msg.get("message_id"))
    repos.audit.append(case_id, "REJECTION_ATTEMPT", {"planId": plan["planId"], "outcome": result.reason, "sender": sender})
    return result
