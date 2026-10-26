"""Renders 'approval_result' SES message body."""
from ._common import clip, footer, subject

HEADLINES = {
    "RESOLVED": "RESOLVED - recovery verified",
    "REMEDIATION_FAILED": "Remediation did not fix the incident",
    "ESCALATED": "Escalated - needs a human",
    "NO_ACTION": "No action taken - no longer needed",
}


def render_ack(case_id, plan):
    """Confirmation to the approver who acted: the signal that their command took effect."""
    body = [f"CASE: {case_id}", "",
            f"Your approval of {plan['planId']} ({plan['capability']}) was accepted and the action is being executed now.",
            "I will verify recovery with fresh checks and mail everyone the result.", "",
            "Other approvers who reply to this plan will be told it was already decided.", "", footer(case_id, plan)]
    return subject(case_id, "Approved - executing"), "\n".join(body)


def render_result(case_id, plan, final_state, before, after, action_outcome=None, note="", timeline=()):
    lines = [f"CASE: {case_id}", f"Plan: {plan['planId']} ({plan['capability']})", "",
             HEADLINES.get(final_state, final_state), ""]
    if timeline:
        lines += ["TIMELINE"] + [f"  {t}" for t in timeline] + [""]
    if action_outcome:
        lines += [f"Action result: {clip(action_outcome, 300)}",
                  "(An accepted action is not the same as a fixed incident; recovery is checked separately.)", ""]
    lines += ["BEFORE", f"  {clip(before, 600) or '(not recorded)'}", "", "AFTER (fresh checks)",
              f"  {clip(after, 800) or '(not available)'}", ""]
    if note:
        lines += [clip(note, 800), ""]
    lines.append(footer(case_id, plan))
    return subject(case_id, HEADLINES.get(final_state, final_state)), "\n".join(lines)
