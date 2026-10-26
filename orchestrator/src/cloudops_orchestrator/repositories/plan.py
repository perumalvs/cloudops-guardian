"""Plan repository: immutable plan versions bound to exact parameters."""
import json

from ..models import PLAN_APPROVED, PLAN_PROPOSED, canonical_json, make_plan_id, params_hash
from ._base import case_pk, now

PREFIX = "PLAN#"
IMMUTABLE = ("caseId", "planId", "planVersion", "resource", "capability", "parametersJson", "parametersHash")


class PlanRepository:
    def __init__(self, store):
        self.store = store

    def create(self, case_id, version, resource, capability, parameters, evidence_refs,
               diagnosis, impact, risk, verification, approver_emails, token_hash, expires_at, extra=None):
        """Returns the plan item, or None if this plan_id already exists."""
        plan_id = make_plan_id(case_id, version)
        item = {
            "PK": case_pk(case_id), "SK": f"{PREFIX}{plan_id}", "type": "PLAN",
            "caseId": case_id, "planId": plan_id, "planVersion": version,
            "resource": resource, "capability": capability,
            "parametersJson": canonical_json(parameters), "parametersHash": params_hash(parameters),
            "evidenceRefs": list(evidence_refs), "diagnosis": diagnosis,
            "impact": impact, "risk": risk, "verification": verification,
            "approverEmails": list(approver_emails), "tokenHash": token_hash,
            "planStatus": PLAN_PROPOSED, "expiresAt": expires_at, "createdAt": now(),
        }
        item.update({k: v for k, v in (extra or {}).items() if v is not None})
        # Same shape as get(): parsed parameters alongside the stored JSON.
        return {**item, "parameters": parameters} if self.store.put_new(item) else None

    def get(self, case_id, plan_id):
        item = self.store.get(case_pk(case_id), f"{PREFIX}{plan_id}")
        if item:
            item["parameters"] = json.loads(item["parametersJson"])
        return item

    def list(self, case_id):
        return self.store.query_prefix(case_pk(case_id), PREFIX)

    def next_version(self, case_id):
        return max([p["planVersion"] for p in self.list(case_id)] or [0]) + 1

    def set_status(self, case_id, plan_id, to, expected_from, **fields):
        """Conditional plan status change. True if applied."""
        values = {f":f{i}": s for i, s in enumerate(expected_from)}
        return self.store.update_if(
            case_pk(case_id), f"{PREFIX}{plan_id}",
            {"planStatus": to, "updatedAt": now(), **fields},
            "#ps IN (" + ", ".join(values) + ")",
            cond_names={"#ps": "planStatus"}, cond_values=values,
        )

    def consume_token(self, case_id, plan_id):
        """
        Single-use approval: atomically mark the plan APPROVED and the token
        consumed, only while it is PROPOSED and unconsumed. True for exactly
        one caller.
        """
        return self.store.update_if(
            case_pk(case_id), f"{PREFIX}{plan_id}",
            {"planStatus": PLAN_APPROVED, "tokenConsumedAt": now(), "updatedAt": now()},
            "#ps = :proposed AND attribute_not_exists(tokenConsumedAt)",
            cond_names={"#ps": "planStatus"}, cond_values={":proposed": PLAN_PROPOSED},
        )
