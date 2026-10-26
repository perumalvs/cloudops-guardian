"""Renders 'case_details' SES message body."""
from ._common import clip, evidence_lines, footer, outcome_lines, subject


def render_case_details(case, evidence, plans, executions=(), verifications=(), options=()):
    case_id = case["caseId"]
    body = [f"CASE: {case_id}", f"State: {case.get('status')}   Created: {case.get('createdAt')}",
            f"Alarm: {case.get('alarmName', '-')}", "", "EVIDENCE", evidence_lines(evidence, limit=25), "", "PLANS"]
    for p in plans:
        body.append(f"  {p['planId']}: {p['capability']} [{p['planStatus']}] expires {p['expiresAt']}")
    if not plans:
        body.append("  (none)")
    if options:
        active = next((p.get("optionNumber") for p in plans if p.get("planStatus") == "PROPOSED"), None)
        body += ["", "OPTIONS (numbers are fixed for this case)", outcome_lines(options, active)]
    if executions:
        body += ["", "EXECUTION"] + [f"  {e['planId']}: {e.get('execStatus')} / {e.get('outcome', '-')}" for e in executions]
    if verifications:
        body += ["", "VERIFICATION"] + [
            f"  {v['planId']} attempt {v['attempt']}: {'PASSED' if v['passed'] else 'not yet'} - {clip(v['checks'], 200)}"
            for v in verifications]
    pending = next((p for p in plans if p.get("planStatus") == "PROPOSED"), None)
    body += ["", "No approval tokens are ever shown here.", "", footer(case_id, pending, pending=bool(pending), options=bool(options))]
    return subject(case_id, "Case details"), "\n".join(body)
