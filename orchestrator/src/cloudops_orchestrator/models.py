"""Identifier formats and record constants shared across the orchestrator."""
import hashlib
import json
import re

CASE_ID_RE = re.compile(r"^CG-[A-F0-9]{12}$")
PLAN_ID_RE = re.compile(r"^PLAN-([A-F0-9]{12})V([1-9][0-9]{0,2})$")

# Allowed P0 outcomes.
OUTCOMES = ("terminate_process", "reboot_demo_instance", "CONTINUE_MONITORING", "ESCALATE")
WRITE_CAPABILITIES = ("terminate_process", "reboot_demo_instance")

# Plan record status (separate from the case state).
PLAN_PROPOSED = "PROPOSED"
PLAN_APPROVED = "APPROVED"
PLAN_REJECTED = "REJECTED"
PLAN_EXPIRED = "EXPIRED"
PLAN_SUPERSEDED = "SUPERSEDED"
PLAN_WITHDRAWN = "WITHDRAWN"           # a human escalated the case; the pending plan can no longer be approved
PLAN_NOT_EXECUTED = "NOT_EXECUTED"   # approved, but the pre-action re-check refused to act


def make_plan_id(case_id, version):
    if not CASE_ID_RE.match(case_id):
        raise ValueError("invalid case id")
    if not 1 <= int(version) <= 999:
        raise ValueError("invalid plan version")
    return f"PLAN-{case_id[3:]}V{int(version)}"


def parse_plan_id(plan_id):
    """Return (case_id, version) or raise ValueError."""
    m = PLAN_ID_RE.match((plan_id or "").upper())
    if not m:
        raise ValueError("invalid plan id")
    return f"CG-{m.group(1)}", int(m.group(2))


def canonical_json(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def params_hash(parameters):
    """Stable hash binding a plan to its exact parameters."""
    return hashlib.sha256(canonical_json(parameters).encode()).hexdigest()
