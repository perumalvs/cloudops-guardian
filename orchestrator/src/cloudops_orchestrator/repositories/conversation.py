"""Conversation mapping: inbound message de-duplication and case linkage."""
import hashlib

from ._base import now


class ConversationRepository:
    def __init__(self, store):
        self.store = store

    def claim_message(self, message_id, case_id=None, intent=None):
        """
        True the first time this email Message-ID is seen, False for any
        duplicate delivery. Call before any side effect.
        """
        key = hashlib.sha256(message_id.encode()).hexdigest()[:32]
        return self.store.put_new({
            "PK": f"MSG#{key}", "SK": "DEDUP", "type": "MESSAGE",
            "caseId": case_id, "intent": intent, "firstSeenAt": now(),
        })
