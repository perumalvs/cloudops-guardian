"""Execution repository: execute-once claim per plan."""
import json

from ._base import case_pk, now

PREFIX = "EXEC#"
CLAIMED, FINISHED = "CLAIMED", "FINISHED"


class ExecutionRepository:
    def __init__(self, store):
        self.store = store

    def claim(self, case_id, plan_id, capability, parameters_hash):
        """
        Must succeed BEFORE the ACTION call. Only the first caller for a
        plan_id gets True; duplicates (retries, repeated emails) get False.
        """
        return self.store.put_new({
            "PK": case_pk(case_id), "SK": f"{PREFIX}{plan_id}", "type": "EXECUTION",
            "planId": plan_id, "capability": capability, "parametersHash": parameters_hash,
            "execStatus": CLAIMED, "claimedAt": now(),
        })

    def finish(self, case_id, plan_id, outcome, result):
        """outcome is the ACTION tool status (e.g. SUCCESS, REFUSED, TIMEOUT)."""
        return self.store.update_if(
            case_pk(case_id), f"{PREFIX}{plan_id}",
            {"execStatus": FINISHED, "outcome": outcome,
             "result": json.dumps(result, default=str), "finishedAt": now()},
            "#es = :claimed", cond_names={"#es": "execStatus"}, cond_values={":claimed": CLAIMED},
        )

    def get(self, case_id, plan_id):
        return self.store.get(case_pk(case_id), f"{PREFIX}{plan_id}")
