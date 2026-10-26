"""DynamoDB repositories over CloudOpsGuardian-CaseState (authoritative)."""
import boto3

from ._base import Store
from .approval import ApprovalRepository
from .audit import AuditRepository
from .case import CaseRepository
from .conversation import ConversationRepository
from .evidence import EvidenceRepository
from .execution import ExecutionRepository
from .plan import PlanRepository
from .verification import VerificationRepository


class Repositories:
    """All repositories over one table."""

    def __init__(self, table):
        store = Store(table)
        self.case = CaseRepository(store)
        self.evidence = EvidenceRepository(store)
        self.plan = PlanRepository(store)
        self.approval = ApprovalRepository(store)
        self.execution = ExecutionRepository(store)
        self.verification = VerificationRepository(store)
        self.audit = AuditRepository(store)
        self.conversation = ConversationRepository(store)

    @classmethod
    def from_config(cls, cfg):
        return cls(boto3.resource("dynamodb", region_name=cfg.region).Table(cfg.table_name))
