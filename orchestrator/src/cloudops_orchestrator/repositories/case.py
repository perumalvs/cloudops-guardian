"""Case repository: authoritative case state with conditional transitions."""
from ..states import CaseState, normalize, sources
from ._base import case_pk, now

METADATA = "METADATA"


class CaseRepository:
    def __init__(self, store):
        self.store = store

    def get(self, case_id):
        return self.store.get(case_pk(case_id), METADATA)

    def state(self, case_id):
        item = self.get(case_id)
        return normalize(item["status"]) if item else None

    def transition(self, case_id, to, expected_from=None, **fields):
        """
        Move the case to `to` only if its current status is a legal source
        state (and, if given, one of `expected_from`). Returns True if applied.
        Concurrent/duplicate callers: exactly one wins.
        """
        to = normalize(to)
        allowed = sources(to)
        if expected_from is not None:
            allowed &= {normalize(s).value for s in expected_from} | (
                {"OPEN"} if CaseState.DETECTED in {normalize(s) for s in expected_from} else set()
            )
        if not allowed:
            return False
        names = {"#st": "status"}
        values = {f":f{i}": s for i, s in enumerate(sorted(allowed))}
        condition = "#st IN (" + ", ".join(values) + ")"
        sets = {"status": to.value, "updatedAt": now(), **fields}
        return self.store.update_if(
            case_pk(case_id), METADATA, sets, condition,
            cond_names=names, cond_values=values, adds={"stateVersion": 1},
        )

    def set_fields(self, case_id, **fields):
        """Update non-state attributes (e.g. diagnosis summary reference)."""
        return self.store.update_if(
            case_pk(case_id), METADATA, {"updatedAt": now(), **fields}, "attribute_exists(PK)",
        )
