"""Env config: CLOUDOPS_OPERATOR_EMAILS, CLOUDOPS_APPROVER_EMAILS (separate), ARNs, table name."""
import os
from dataclasses import dataclass, field


def _emails(value):
    return tuple(sorted({e.strip().lower() for e in (value or "").split(",") if e.strip()}))


@dataclass(frozen=True)
class Config:
    region: str = "us-east-1"
    table_name: str = "CloudOpsGuardian-CaseState"
    operator_emails: tuple = field(default_factory=tuple)
    approver_emails: tuple = field(default_factory=tuple)
    approval_expiry_minutes: int = 30
    token_pepper: str = field(default="", repr=False)
    require_ses_auth_pass: bool = True
    ses_from: str = "__SES_FROM_ADDRESS__"
    inbound_bucket: str = ""
    demo_instance_id: str = ""
    reasoning_runtime_arn: str = ""
    read_gateway_url: str = ""
    action_gateway_url: str = ""
    verification_workflow_arn: str = ""
    verification_max_attempts: int = 15
    execution_enabled: bool = False   # C8 switch: approvals only validate (dry-run) until True

    @classmethod
    def from_env(cls, env=None):
        e = os.environ if env is None else env
        return cls(
            region=e.get("AWS_REGION") or e.get("AWS_DEFAULT_REGION") or "us-east-1",
            table_name=e.get("CASE_TABLE_NAME", "CloudOpsGuardian-CaseState"),
            operator_emails=_emails(e.get("CLOUDOPS_OPERATOR_EMAILS")),
            approver_emails=_emails(e.get("CLOUDOPS_APPROVER_EMAILS")),
            approval_expiry_minutes=int(e.get("APPROVAL_EXPIRY_MINUTES", "30")),
            token_pepper=e.get("APPROVAL_TOKEN_PEPPER", ""),
            require_ses_auth_pass=e.get("REQUIRE_SES_AUTH_PASS", "true").lower() == "true",
            ses_from=e.get("SES_FROM_ADDRESS", "__SES_FROM_ADDRESS__"),
            inbound_bucket=e.get("INBOUND_EMAIL_BUCKET", ""),
            demo_instance_id=e.get("DEMO_INSTANCE_ID", ""),
            reasoning_runtime_arn=e.get("REASONING_RUNTIME_ARN", ""),
            read_gateway_url=e.get("READ_GATEWAY_MCP_URL", ""),
            action_gateway_url=e.get("ACTION_GATEWAY_MCP_URL", ""),
            verification_workflow_arn=e.get("VERIFICATION_WORKFLOW_ARN", ""),
            verification_max_attempts=int(e.get("VERIFICATION_MAX_ATTEMPTS", "15")),
            execution_enabled=e.get("EXECUTION_ENABLED", "false").lower() == "true",
        )
