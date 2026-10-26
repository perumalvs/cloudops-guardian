"""Chooses recipients and variants for outbound mail. The approval token goes only to approvers."""
from .emails.incident import render_incident
from .emails.escalation import render_escalation
from .states import CaseState as S


def send_investigation_mail(ses, cfg, inv):
    """Returns list of (audience, ses_message_id)."""
    sent = []
    approvers = set(cfg.approver_emails)
    others = sorted(set(cfg.operator_emails) - approvers)

    if inv.state is S.ESCALATED:
        subject, body = render_escalation(inv.case, inv.reason, inv.evidence)
        sent.append(("all", ses.send(sorted(approvers | set(cfg.operator_emails)), subject, body)))
        return sent

    if inv.plan:
        subject, body = render_incident(inv.case, inv.diagnosis, inv.evidence, inv.outcomes, inv.plan, inv.token)
        sent.append(("approvers", ses.send(sorted(approvers), subject, body)))
        if others:
            subject, body = render_incident(inv.case, inv.diagnosis, inv.evidence, inv.outcomes, inv.plan, None)
            sent.append(("operators", ses.send(others, subject, body)))
        return sent

    subject, body = render_incident(inv.case, inv.diagnosis, inv.evidence, inv.outcomes)
    sent.append(("all", ses.send(sorted(approvers | set(cfg.operator_emails)), subject, body)))
    return sent
