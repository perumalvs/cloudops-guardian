"""Shared plain-text formatting for outbound mail."""

def subject(case_id, text):
    return f"[{case_id}] {text}"


def clip(text, n=400):
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 3] + "..."


def evidence_lines(evidence, limit=8):
    lines = [f"  - {clip(e.get('tool') or e.get('kind'), 60)}: {clip(e.get('summary') or e.get('payload'), 300)}"
             for e in list(evidence)[:limit]]
    if len(evidence) > limit:
        lines.append(f"  ... and {len(evidence) - limit} more (reply DETAILS to see all)")
    return "\n".join(lines) or "  (none recorded)"


WRITE = ("terminate_process", "reboot_demo_instance")


def outcome_lines(outcomes, active=None):
    """Numbered options. Numbers are fixed for the case; each line says what can be done with it."""
    out = []
    for o in outcomes:
        n = o.get("n", o.get("rank"))
        if n == active:
            hint = "   <== this is the plan below"
        elif o["capability"] in WRITE:
            hint = f"   (reply PROPOSE {n} to get an approvable plan for this)"
        elif o["capability"] == "ESCALATE":
            hint = "   (reply ESCALATE to hand the case to a human)"
        else:
            hint = "   (informational; no action)"
        out.append(f"  {n}. {o['capability']}{hint}")
        if o.get("rationale"):
            out.append(f"     why: {clip(o['rationale'], 300)}")
        if o.get("impact"):
            out.append(f"     impact: {clip(o['impact'], 200)}")
        if o.get("risk"):
            out.append(f"     risk: {clip(o['risk'], 200)}")
    return "\n".join(out) or "  (none)"


def provenance_lines(plan):
    """Who or what caused this plan, and what it replaces. Always shown, so a revised plan is never a surprise."""
    via, who, when = plan.get("proposedVia"), plan.get("proposedBy"), str(plan.get("proposedAt") or plan.get("createdAt") or "")[11:19]
    n = plan.get("optionNumber")
    if via == "operator":
        lines = [f"  source:   proposed by {who} at {when} UTC (reply PROPOSE {n}); the agent had ranked this option #{plan.get('agentRank', '?')}"]
    elif via == "stale-replan":
        lines = [f"  source:   re-investigated because the previously approved plan became stale: {clip(plan.get('reason'), 200)}"]
    elif via == "followup":
        lines = [f"  source:   proposed by the agent after a follow-up from {who}, because the evidence changed"]
    else:
        lines = ["  source:   proposed by the agent after investigating the alarm"]
    if plan.get("supersedes"):
        lines.append(f"  replaces: {plan['supersedes']}, which is now void and cannot be approved")
    return lines


def plan_block(plan, token=None):
    """The approval section. The token appears only when explicitly supplied (approvers only)."""
    params = ", ".join(f"{k}={v}" for k, v in sorted(plan["parameters"].items()) if k != "plan_id") or "(none)"
    lines = [
        f"PROPOSED PLAN {plan['planId']} (version {plan['planVersion']})",
        f"  action:   {plan['capability']}",
        f"  target:   {plan['resource']}",
        f"  params:   {params}",
        f"  impact:   {clip(plan.get('impact'), 300)}",
        f"  risk:     {clip(plan.get('risk'), 300)}",
        f"  verify:   {clip(plan.get('verification'), 300)}",
        f"  expires:  {plan['expiresAt']}",
        *provenance_lines(plan),
        "",
    ]
    if token:
        lines += ["To approve, reply with this exact line first:",
                  f"  APPROVE {plan['planId']} {token}",
                  "The token works once, only for this plan, and only from an authorized approver's address.",
                  "A plain 'yes' does nothing."]
    else:
        lines += ["Approval is limited to authorized approvers, who received the approval command separately.",
                  f"An approver who no longer has that line can reply PROPOSE {plan.get('optionNumber', 1)} to re-issue this plan with a new token (the old one then becomes void).",
                  "A plain 'yes' does nothing."]
    return "\n".join(lines)


def footer(case_id, plan=None, pending=False, options=False):
    """
    The standard command list, identical in every mail that offers it.
    REJECT appears only while a plan is pending; PROPOSE and ESCALATE only where the case has numbered options.
    """
    lines = ["Reply commands (put the command on the FIRST line of your reply):",
             f"  DETAILS {case_id}",
             "      full evidence, history and the numbered options"]
    if plan and pending:
        lines += [f"  REJECT {plan['planId']}", "      reject the pending plan (only while it is pending)"]
    if options:
        lines += ["  PROPOSE <n>        for example:  PROPOSE 2",
                  "      choose option n from the numbered options; creates a NEW plan with a new approval line",
                  "      and voids the pending one. Allowed for operators and approvers; approving is still separate.",
                  "  ESCALATE [note]    for example:  ESCALATE parent job needs a look",
                  "      hand the case to a person; withdraws any pending plan; no automated action is taken"]
    lines += ["  <any other text>", "      treated as a question; I will investigate and answer (keep the case id in the subject)"]
    return "\n".join(lines)
