"""Redacted audit event writer: append-only, sensitive values never stored."""
import json

from ..redaction import redact
from ._base import case_pk, now, sortable_id

PREFIX = "AUDIT#"


class AuditRepository:
    def __init__(self, store):
        self.store = store

    def append(self, case_id, event_type, summary=None):
        self.store.put_new({
            "PK": case_pk(case_id), "SK": f"{PREFIX}{sortable_id()}", "type": "AUDIT",
            "event": event_type, "summary": json.dumps(redact(summary or {}), default=str),
            "createdAt": now(),
        })

    def list(self, case_id):
        return [{**i, "summary": json.loads(i["summary"])} for i in self.store.query_prefix(case_pk(case_id), PREFIX)]
