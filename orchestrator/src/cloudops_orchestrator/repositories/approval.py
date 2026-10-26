"""Approval attempts log (never stores plaintext tokens)."""
from ._base import case_pk, now, sortable_id

PREFIX = "APPROVAL#"


class ApprovalRepository:
    def __init__(self, store):
        self.store = store

    def record(self, case_id, plan_id, decision, reason, sender, message_id):
        """decision: APPROVED | REJECTED | DENIED. reason: short machine code."""
        self.store.put_new({
            "PK": case_pk(case_id), "SK": f"{PREFIX}{plan_id}#{sortable_id()}", "type": "APPROVAL",
            "planId": plan_id, "decision": decision, "reason": reason,
            "sender": sender, "messageId": message_id, "createdAt": now(),
        })

    def list(self, case_id, plan_id=None):
        return self.store.query_prefix(case_pk(case_id), f"{PREFIX}{plan_id}#" if plan_id else PREFIX)
