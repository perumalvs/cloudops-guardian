"""Evidence repository: READ-tool results and diagnoses, append-only."""
import json

from ._base import case_pk, now, sortable_id

PREFIX = "EVIDENCE#"


class EvidenceRepository:
    def __init__(self, store):
        self.store = store

    def add(self, case_id, kind, tool, payload, requested_by="reasoning"):
        """payload is stored as a JSON string (DynamoDB rejects floats)."""
        sk = f"{PREFIX}{sortable_id()}"
        self.store.put_new({
            "PK": case_pk(case_id), "SK": sk, "type": "EVIDENCE",
            "kind": kind, "tool": tool, "requestedBy": requested_by,
            "payload": json.dumps(payload, default=str), "createdAt": now(),
        })
        return sk

    def list(self, case_id):
        return [
            {**i, "payload": json.loads(i["payload"])}
            for i in self.store.query_prefix(case_pk(case_id), PREFIX)
        ]
