"""Verification repository: fresh-READ check results."""
import json

from ._base import case_pk, now, sortable_id

PREFIX = "VERIFY#"


class VerificationRepository:
    def __init__(self, store):
        self.store = store

    def add(self, case_id, plan_id, attempt, checks, passed):
        self.store.put_new({
            "PK": case_pk(case_id), "SK": f"{PREFIX}{plan_id}#{sortable_id()}", "type": "VERIFICATION",
            "planId": plan_id, "attempt": attempt, "passed": bool(passed),
            "checks": json.dumps(checks, default=str), "createdAt": now(),
        })

    def list(self, case_id, plan_id=None):
        items = self.store.query_prefix(case_pk(case_id), f"{PREFIX}{plan_id}#" if plan_id else PREFIX)
        return [{**i, "checks": json.loads(i["checks"])} for i in items]
