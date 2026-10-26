"""Renders 'rejection' SES message body."""
from ._common import footer, subject


def render_rejection(case_id, plan_id):
    body = (f"CASE: {case_id}\n\nPlan {plan_id} was rejected. No action was executed and none will be.\n"
            "If you want a different action, reply PROPOSE <n> with an option number (reply DETAILS to see the numbered options),\n"
            "or ESCALATE to hand the case to a person.\n\n" + footer(case_id, options=True))
    return subject(case_id, "Plan rejected"), body
