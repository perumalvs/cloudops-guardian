"""SES sender from __SES_FROM_ADDRESS__; recipients from config."""
from email.message import EmailMessage
from email.utils import formataddr

import boto3


class RecipientNotAllowed(Exception):
    pass


class SesOutbound:
    """Plain-text, threaded mail. Only configured operators/approvers can ever be addressed."""

    def __init__(self, cfg, client=None):
        self.cfg = cfg
        self.client = client or boto3.client("ses", region_name=cfg.region)
        self.allowed = set(cfg.operator_emails) | set(cfg.approver_emails)

    def _check(self, recipients):
        clean = sorted({r.strip().lower() for r in recipients if r and r.strip()})
        if not clean:
            raise RecipientNotAllowed("no recipients")
        bad = [r for r in clean if r not in self.allowed]
        if bad:
            raise RecipientNotAllowed(f"{len(bad)} recipient(s) not configured as operator/approver")
        return clean

    def send(self, to, subject, body, in_reply_to=None, references=None):
        """Returns the SES MessageId."""
        to = self._check(to)
        msg = EmailMessage()
        msg["From"] = formataddr(("CloudOps Guardian", self.cfg.ses_from))
        msg["To"] = ", ".join(to)
        msg["Subject"] = " ".join(subject.split())  # no header injection via newlines
        if in_reply_to:
            msg["In-Reply-To"] = " ".join(in_reply_to.split())
            msg["References"] = " ".join(" ".join(references or [in_reply_to]).split())
        msg.set_content(body)
        response = self.client.send_raw_email(
            Source=self.cfg.ses_from, Destinations=to, RawMessage={"Data": msg.as_bytes()})
        return response["MessageId"]

    def send_to_operators(self, subject, body, **kw):
        return self.send(self.cfg.operator_emails, subject, body, **kw)

    def send_to_approvers(self, subject, body, **kw):
        return self.send(self.cfg.approver_emails, subject, body, **kw)
