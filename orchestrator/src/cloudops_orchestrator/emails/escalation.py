"""Renders 'escalation' SES message body."""
from ._common import clip, evidence_lines, footer, subject


def render_escalation(case, reason, evidence=(), requested_by=None, withdrawn_plan=None):
    case_id = case["caseId"]
    if requested_by:
        head = [f"ESCALATED by {requested_by} - this case is now with a person.", f"Note: {clip(reason, 600)}"]
        tail = ["No automated action will be taken on this case."]
        if withdrawn_plan:
            tail.insert(0, f"The pending plan {withdrawn_plan} was withdrawn. Approving or rejecting it will be refused.")
    else:
        head = ["ESCALATED - I could not establish a safe automated resolution.", f"Reason: {clip(reason, 600)}"]
        tail = ["No action has been executed."]
    body = [f"CASE: {case_id}", f"Alarm: {case.get('alarmName', '-')}", "", *head, "", *tail, "",
            "WHAT I COULD GATHER", evidence_lines(evidence), "", footer(case_id)]
    return subject(case_id, "Escalated - needs a human"), "\n".join(body)
