"""Renders 'incident' SES message body."""
from ._common import clip, evidence_lines, footer, outcome_lines, plan_block, subject


def render_incident(case, diagnosis, evidence, outcomes, plan=None, token=None):
    case_id = case["caseId"]
    head = ("Revised plan - approval needed" if plan and plan.get("supersedes") else "Plan proposed - approval needed") if plan else "Incident investigated"
    body = [
        f"CASE: {case_id}",
        f"Alarm: {case.get('alarmName', '-')}   Resource: {case.get('resource', '-')}",
        "",
        "NO ACTION HAS BEEN EXECUTED. Nothing changes until an exact plan is approved.",
        "",
        "FINDING",
        f"  {clip(diagnosis.get('summary'), 800)}",
        f"  Likely cause: {clip(diagnosis.get('root_cause'), 400)}",
        f"  Confidence: {diagnosis.get('confidence', 'LOW')}",
        "",
        "EVIDENCE",
        evidence_lines(evidence),
        "",
        "OPTIONS (governed capabilities only; the numbers are fixed for this case)",
        outcome_lines(outcomes, plan.get("optionNumber") if plan else None),
        "",
    ]
    if plan:
        body += [plan_block(plan, token), ""]
    body.append(footer(case_id, plan, pending=bool(plan), options=bool(outcomes)))
    return subject(case_id, head), "\n".join(body)
