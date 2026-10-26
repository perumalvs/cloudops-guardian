"""Renders 'followup' SES message body."""
from ._common import clip, evidence_lines, footer, outcome_lines, plan_block, subject


def render_followup(case_id, question, answer, new_evidence=(), outcomes=(), plan=None, token=None):
    """plan: the plan pending approval, if any (shown truthfully, token only if the caller supplies it)."""
    body = [f"CASE: {case_id}", f"Your question: {clip(question, 300)}", "", "ANSWER", f"  {clip(answer, 3000)}", ""]
    if new_evidence:
        body += ["NEW EVIDENCE GATHERED", evidence_lines(new_evidence), ""]
    if outcomes:
        body += ["OPTIONS FOR THIS CASE (numbers are fixed; they are not renumbered by questions)",
                 outcome_lines(outcomes, plan.get("optionNumber") if plan else None), ""]
    if plan:
        body += [plan_block(plan, token), ""]
    else:
        body += ["No action has been executed and no plan is pending approval.", ""]
    body.append(footer(case_id, plan, pending=bool(plan), options=bool(outcomes)))
    return subject(case_id, "Re: your question"), "\n".join(body)
