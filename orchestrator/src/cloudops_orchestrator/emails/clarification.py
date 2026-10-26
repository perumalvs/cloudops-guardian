"""Renders 'clarification' SES message body."""
from ._common import footer


def render_clarification(message, case_id=None):
    body = [message.strip(), "", "I cannot act on this message. Nothing was executed.", "",
            footer(case_id or "CG-<case id>")]
    return (f"[{case_id}] Need more information" if case_id else "CloudOps Guardian: need more information"), "\n".join(body)


def render_answer(question, answer):
    """Reply to a message that is not tied to a case: read-only, nothing executed."""
    body = [f"Your message: {' '.join(str(question).split())[:300]}", "", "ANSWER", f"  {' '.join(str(answer).split())[:3000]}", "",
            "This was a read-only answer. It is not tied to a case, so no plan exists and nothing was executed.", "",
            footer("CG-<case id>")]
    return "CloudOps Guardian: answer", "\n".join(body)
