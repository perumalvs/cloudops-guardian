"""Case state enum and allowed-transition table (DETECTED..EXPIRED)."""
from enum import Enum


class CaseState(str, Enum):
    DETECTED = "DETECTED"
    INVESTIGATING = "INVESTIGATING"
    DIAGNOSED = "DIAGNOSED"
    AWAITING_USER = "AWAITING_USER"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    RESOLVED = "RESOLVED"
    NO_ACTION = "NO_ACTION"
    REJECTED = "REJECTED"
    REMEDIATION_FAILED = "REMEDIATION_FAILED"
    ESCALATED = "ESCALATED"
    EXPIRED = "EXPIRED"


S = CaseState

# IncidentHandler writes status "OPEN" for a freshly created case.
LEGACY_ALIASES = {"OPEN": S.DETECTED}

# A follow-up question may reopen a case that ended without being resolved.
_REOPEN = [S.INVESTIGATING]

TRANSITIONS = {
    S.DETECTED: {S.INVESTIGATING, S.ESCALATED},
    S.INVESTIGATING: {S.DIAGNOSED, S.NO_ACTION, S.ESCALATED, S.AWAITING_USER},
    S.DIAGNOSED: {S.AWAITING_APPROVAL, S.AWAITING_USER, S.NO_ACTION, S.ESCALATED},
    S.AWAITING_USER: {S.INVESTIGATING, S.AWAITING_APPROVAL, S.NO_ACTION, S.ESCALATED},
    S.AWAITING_APPROVAL: {
        S.APPROVED, S.REJECTED, S.EXPIRED, S.INVESTIGATING,
        S.AWAITING_USER, S.NO_ACTION, S.ESCALATED,
    },
    # APPROVED: pre-action check may find the plan stale or the incident gone.
    S.APPROVED: {S.EXECUTING, S.NO_ACTION, S.INVESTIGATING, S.AWAITING_APPROVAL, S.ESCALATED},
    S.EXECUTING: {S.VERIFYING, S.REMEDIATION_FAILED, S.ESCALATED},
    S.VERIFYING: {S.RESOLVED, S.REMEDIATION_FAILED, S.ESCALATED},
    S.RESOLVED: set(),
    S.NO_ACTION: set(_REOPEN),
    S.REJECTED: set(_REOPEN),
    S.REMEDIATION_FAILED: set(_REOPEN),
    S.ESCALATED: set(_REOPEN),
    S.EXPIRED: set(_REOPEN),
}

TERMINAL = {S.RESOLVED}


def normalize(status):
    """Map a stored status string to a CaseState (handles the legacy OPEN)."""
    if isinstance(status, CaseState):
        return status
    if status in LEGACY_ALIASES:
        return LEGACY_ALIASES[status]
    return CaseState(status)


def can_transition(current, target):
    return normalize(target) in TRANSITIONS[normalize(current)]


def sources(target):
    """Stored status strings from which `target` may be entered."""
    target = normalize(target)
    out = {s.value for s, to in TRANSITIONS.items() if target in to}
    if S.DETECTED in {s for s, to in TRANSITIONS.items() if target in to}:
        out.add("OPEN")
    return out
