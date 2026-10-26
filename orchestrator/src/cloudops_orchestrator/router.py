"""Routes normalized inbound intents: CASE_DETAILS, CASE_FOLLOWUP, APPROVAL, REJECTION, NEW_OR_AMBIGUOUS_QUERY."""
import hashlib
import json
import logging

from .approval_validator import authorize_approval, authorize_rejection, validate_approval, verdict_ok
from .emails.case_details import render_case_details
from .emails.clarification import render_answer, render_clarification
from .emails.followup import render_followup
from .emails.rejection import render_rejection
from .executor import Executor
from .investigation import Investigation, case_payload, conclude, persist_result
from .emails.escalation import render_escalation
from .investigation import VERIFY
from .models import PLAN_PROPOSED, PLAN_WITHDRAWN, WRITE_CAPABILITIES
from .plan_builder import build_plan
from .repositories._base import now as now_iso
from .notifier import send_investigation_mail
from .states import CaseState as S

logger = logging.getLogger()

REOPENABLE = {S.NO_ACTION, S.ESCALATED, S.REMEDIATION_FAILED, S.EXPIRED, S.REJECTED}
QUESTION_LIMIT = 2000

DENIAL_TEXT = {
    "SENDER_NOT_AUTHORIZED": "You are not an authorized approver for this plan.",
    "MAIL_AUTH_FAILED": "The message could not be authenticated.",
    "PLAN_ID_INVALID": "The plan id in your message is not valid.",
    "CASE_NOT_FOUND": "I could not find that case.",
    "CASE_PLAN_MISMATCH": "That plan does not belong to the case named in your message.",
    "PLAN_NOT_FOUND": "I could not find that plan.",
    "PLAN_INTEGRITY": "That plan failed an integrity check and cannot be used.",
    "PLAN_NOT_LATEST": "That plan has been replaced by a newer version. Use the newest plan email.",
    "PLAN_NOT_PENDING": "That plan is no longer pending (already decided, withdrawn or replaced).",
    "PLAN_EXPIRED": "That plan has expired. Ask me to re-investigate to get a new one.",
    "CASE_STATE_INVALID": "The case is not waiting for approval.",
    "TOKEN_INVALID": "The approval token is missing or incorrect. Copy the exact line from the plan email.",
    "ALREADY_DECIDED": "That plan was already decided.",
}


def _same_plan(plan, outcome):
    if plan["capability"] != outcome["capability"]:
        return False
    if plan["capability"] == "terminate_process":
        p = outcome.get("parameters") or {}
        return (plan["parameters"].get("process_id"), plan["parameters"].get("expected_process_name")) == (
            p.get("process_id"), p.get("expected_process_name"))
    return True


class Router:
    def __init__(self, services):
        self.svc, self.cfg, self.repos = services, services.cfg, services.repos
        self.known = set(self.cfg.operator_emails) | set(self.cfg.approver_emails)

    # ---------- entry ----------
    def route(self, msg, raw):
        """msg: normalized parser output. raw: {"verdicts": {...}, "token": str|None}. Returns a small summary."""
        cls = msg.get("classification") or {}
        intent = cls.get("intent")
        senders = [a.lower() for a in msg.get("from") or []]
        sender = senders[0] if len(senders) == 1 else None
        if sender not in self.known:           # unknown/ambiguous sender: stay silent (no backscatter)
            logger.warning("inbound dropped: reason=unknown_sender intent=%s", intent)
            return {"intent": intent, "result": "DROPPED_UNKNOWN_SENDER"}
        if self.cfg.require_ses_auth_pass and not verdict_ok(raw.get("verdicts")):
            logger.warning("inbound dropped: reason=mail_auth intent=%s", intent)
            self._audit(cls, "INBOUND_DROPPED", {"reason": "MAIL_AUTH_FAILED", "intent": intent})
            return {"intent": intent, "result": "DROPPED_MAIL_AUTH"}

        command = self._command(msg) if intent in ("CASE_FOLLOWUP", "NEW_OR_AMBIGUOUS_QUERY") else None
        if command and intent != "CASE_FOLLOWUP":
            return {"intent": intent, **self._clarify(
                msg, sender, "I need to know which case. Put the case id in the subject (for example [CG-…]) and resend the command.")}
        if intent in ("APPROVAL", "REJECTION") or command:
            key = msg.get("message_id") or f"{msg.get('s3_bucket')}/{msg.get('s3_key')}"
            if not self.repos.conversation.claim_message(key, cls.get("case_id"), intent):
                return {"intent": intent, "result": "DUPLICATE_MESSAGE"}

        if command:
            name, arg = command
            return (self._propose(msg, sender, cls, arg) if name == "PROPOSE" else self._escalate_cmd(msg, sender, cls, arg))

        handler = {"CASE_DETAILS": self._details, "CASE_FOLLOWUP": self._followup, "APPROVAL": self._approval,
                   "REJECTION": self._rejection}.get(intent, self._query)
        return handler(msg, raw, sender, cls)

    # ---------- helpers ----------
    def _audit(self, cls, event, summary):
        case_id = cls.get("case_id")
        if case_id and self.repos.case.get(case_id):
            self.repos.audit.append(case_id, event, summary)

    def _reply(self, msg, sender, rendered):
        subject, body = rendered
        refs = list(msg.get("references") or []) + ([msg["message_id"]] if msg.get("message_id") else [])
        if msg.get("subject") and "[CG-" not in subject:
            subject = msg["subject"] if msg["subject"].lower().startswith("re:") else f"Re: {msg['subject']}"
        self.svc.ses.send([sender], subject, body, in_reply_to=msg.get("message_id"), references=refs or None)

    def _clarify(self, msg, sender, text, case_id=None):
        self._reply(msg, sender, render_clarification(text, case_id))
        return {"result": "CLARIFIED"}

    def _read_evidence(self, case_id):
        out = []
        for e in self.repos.evidence.list(case_id):
            p = e["payload"]
            if e["kind"] == "READ":
                out.append({"tool": e["tool"], "summary": p.get("summary")})
            elif e["kind"] == "DIAGNOSIS":
                out.append({"tool": "diagnosis", "summary": p.get("summary")})
        return out

    def _pending_plan(self, case_id):
        plans = sorted(self.repos.plan.list(case_id), key=lambda p: p["planVersion"])
        plan = plans[-1] if plans else None
        return self.repos.plan.get(case_id, plan["planId"]) if plan and plan["planStatus"] == PLAN_PROPOSED else None

    # ---------- intents ----------
    def _details(self, msg, raw, sender, cls):
        case_id = cls.get("case_id")
        case = self.repos.case.get(case_id) if case_id else None
        if not case:
            return {"intent": "CASE_DETAILS", **self._clarify(msg, sender, "I could not find that case.")}
        plans = self.repos.plan.list(case_id)
        execs = [e for e in (self.repos.execution.get(case_id, p["planId"]) for p in plans) if e]
        self._reply(msg, sender, render_case_details(
            {**case, "status": self.repos.case.state(case_id).value}, self._read_evidence(case_id), plans, execs,
            self.repos.verification.list(case_id), self._options(case)))
        self.repos.audit.append(case_id, "DETAILS_SENT", {"to": sender})
        return {"intent": "CASE_DETAILS", "result": "SENT", "caseId": case_id}

    def _followup(self, msg, raw, sender, cls):
        case_id = cls.get("case_id")
        item = self.repos.case.get(case_id) if case_id else None
        if not item:
            return {"intent": "CASE_FOLLOWUP", **self._clarify(msg, sender, "I could not find that case.")}
        state, question = self.repos.case.state(case_id), (msg.get("body_text") or "")[:QUESTION_LIMIT]
        pending = self._pending_plan(case_id)
        self.repos.audit.append(case_id, "FOLLOWUP_RECEIVED", {"from": sender, "state": state.value, "question": question[:300]})
        try:
            result = self.svc.reasoning.invoke(
                "followup", case_id, case=case_payload(self.cfg, item), question=question,
                known_evidence=self._read_evidence(case_id)[-12:],
                plan_summary=(f"{pending['planId']}: {pending['capability']} {pending['parameters']}" if pending else None))
        except Exception as error:  # noqa: BLE001
            self._reply(msg, sender, render_followup(case_id, question, f"I could not complete the investigation ({type(error).__name__}). "
                                                     "Nothing was changed; please try again."))
            return {"intent": "CASE_FOLLOWUP", "result": "REASONING_FAILED"}

        top = result["outcomes"][0]
        new_plan_wanted = (state is S.AWAITING_APPROVAL and pending and top["capability"] in WRITE_CAPABILITIES
                           and not _same_plan(pending, top))
        reopen = state in REOPENABLE
        inv = None
        if reopen or new_plan_wanted:
            frm = S.AWAITING_APPROVAL if new_plan_wanted else state
            if self.repos.case.transition(case_id, S.INVESTIGATING, [frm]):
                inv = Investigation(case_id, S.INVESTIGATING, case={**item, "resource": self.cfg.demo_instance_id})
                inv = conclude(self.repos, self.cfg, inv, result, requested_by="operator-followup", proposer=sender, via="followup")
        if inv is None:
            persist_result(self.repos, case_id, result, requested_by="operator-followup")
        # Options are a fixed contract: show the stored list (or the freshly stored one after a new investigation),
        # never the model's re-ranking from this question. The pending plan is shown truthfully, without its token.
        shown = inv.outcomes if inv is not None else self._options(item)
        shown_plan = inv.plan if inv is not None and inv.plan else (self._pending_plan(case_id) if inv is None else None)
        self._reply(msg, sender, render_followup(case_id, question, result["answer"], result["evidence"], shown, shown_plan))
        if inv is not None and (inv.plan or inv.state is S.ESCALATED):
            send_investigation_mail(self.svc.ses, self.cfg, inv)
        self.repos.audit.append(case_id, "FOLLOWUP_ANSWERED", {"state": (inv.state if inv else state).value})
        return {"intent": "CASE_FOLLOWUP", "result": "ANSWERED", "caseId": case_id,
                "state": (inv.state if inv else state).value, "newPlan": inv.plan["planId"] if inv and inv.plan else None}

    def _query(self, msg, raw, sender, cls):
        """No known case: answer from READ evidence only. Outcomes are ignored; this path can never WRITE."""
        question = (msg.get("body_text") or "")[:QUESTION_LIMIT].strip()
        if not question:
            return {"intent": cls.get("intent"), **self._clarify(msg, sender, "Your message was empty. Tell me what you would like to know.")}
        key = hashlib.sha256((msg.get("message_id") or question).encode()).hexdigest()[:12].upper()
        try:
            result = self.svc.reasoning.invoke("query", f"QUERY-{key}", question=question)
        except Exception as error:  # noqa: BLE001
            return {"intent": cls.get("intent"), **self._clarify(msg, sender, f"I could not complete that read-only check ({type(error).__name__}).")}
        self._reply(msg, sender, render_answer(question, result["answer"]))
        logger.info("query answered: tools=%d", len(result["evidence"]))
        return {"intent": cls.get("intent"), "result": "ANSWERED"}

    def _rejection(self, msg, raw, sender, cls):
        result = authorize_rejection(self.repos, self.cfg, msg, raw.get("verdicts"))
        if result.ok:
            self._reply(msg, sender, render_rejection(result.case_id, result.plan["planId"]))
        else:
            note = DENIAL_TEXT.get(result.reason, "That request could not be processed.")
            if result.reason in ("PLAN_NOT_PENDING", "CASE_STATE_INVALID", "PLAN_NOT_LATEST", "PLAN_EXPIRED"):
                note += self._decision_note(result.case_id, cls.get("plan_id"))
            self._clarify(msg, sender, note, result.case_id)
        return {"intent": "REJECTION", "result": "REJECTED" if result.ok else f"DENIED_{result.reason}"}

    def _approval(self, msg, raw, sender, cls):
        if self.cfg.execution_enabled:
            return self._approve_and_execute(msg, raw, sender, cls)
        # Dry-run: every approval check runs, nothing is consumed or executed.
        result = validate_approval(self.repos, self.cfg, msg, raw.get("token"), raw.get("verdicts"))
        case_id = result.case_id
        if case_id and self.repos.case.get(case_id):
            self.repos.audit.append(case_id, "APPROVAL_DRY_RUN", {"plan": cls.get("plan_id"), "outcome": result.reason, "sender": sender})
        if result.ok:
            text = (f"VALIDATION ONLY: your approval of {result.plan['planId']} passed every check "
                    f"(sender, mail authentication, case, plan version, token, expiry, state, parameters).\n"
                    "Execution is not enabled in this environment yet, so nothing was executed and the plan is still pending.")
        else:
            text = DENIAL_TEXT.get(result.reason, "That approval could not be processed.")
        self._clarify(msg, sender, text, case_id)
        return {"intent": "APPROVAL", "result": "DRY_RUN_OK" if result.ok else f"DENIED_{result.reason}"}

    def _decision_note(self, case_id, plan_id):
        """What actually happened to a plan, so a late sender sees the facts and not just a refusal."""
        if not case_id:
            return ""
        case = self.repos.case.get(case_id) or {}
        state = self.repos.case.state(case_id) if case else None
        if state is S.ESCALATED and case.get("escalatedBy"):
            return (f"\nThis case was escalated by {case['escalatedBy']} at {str(case.get('escalatedAt', ''))[11:19]} UTC; "
                    "the pending plan was withdrawn and no automated action will be taken.")
        for a in (self.repos.approval.list(case_id, plan_id) if plan_id else []):
            if a["decision"] in ("APPROVED", "REJECTED"):
                return (f"\nThis plan was {a['decision'].lower()} by {a['sender']} at {a['createdAt'][11:19]} UTC."
                        f"\nThe case is now {state.value if state else 'unknown'}; nothing has been cancelled or changed by your message.")
        newer = sorted((p for p in self.repos.plan.list(case_id) if plan_id and p["planId"] > plan_id), key=lambda p: p["planVersion"])
        if newer:
            n = newer[-1]
            who = f" (proposed by {n['proposedBy']})" if n.get("proposedVia") == "operator" else ""
            return f"\nA newer plan exists: {n['planId']}{who}. Use the approval line from that plan's email."
        return ""

    def _approve_and_execute(self, msg, raw, sender, cls):
        result = authorize_approval(self.repos, self.cfg, msg, raw.get("token"), raw.get("verdicts"))
        if not result.ok:
            note = DENIAL_TEXT.get(result.reason, "That approval could not be processed.")
            if result.reason in ("PLAN_NOT_PENDING", "CASE_STATE_INVALID", "ALREADY_DECIDED", "PLAN_NOT_LATEST", "PLAN_EXPIRED"):
                note += self._decision_note(result.case_id, cls.get("plan_id"))
            self._clarify(msg, sender, note, result.case_id)
            return {"intent": "APPROVAL", "result": f"DENIED_{result.reason}"}
        try:
            outcome = Executor(self.svc).run(msg, sender, result)
        except Exception as error:  # noqa: BLE001 - never leave a case half-way; the claim already prevents a second execution
            self._fail_safe(result.case_id, type(error).__name__)
            return {"intent": "APPROVAL", "result": "EXECUTION_ERROR", "error": type(error).__name__}
        return {"intent": "APPROVAL", **outcome}

    def _fail_safe(self, case_id, error_type):
        self.repos.case.transition(case_id, S.ESCALATED, [S.APPROVED, S.EXECUTING, S.VERIFYING],
                                   escalationReason=f"internal error ({error_type})")
        self.repos.audit.append(case_id, "EXECUTION_ERROR", {"error": error_type})
        case = self.repos.case.get(case_id) or {}
        from .emails.escalation import render_escalation
        try:
            self.svc.ses.send(sorted(self.known), *render_escalation({**case, "caseId": case_id},
                              f"An internal error occurred while handling the approval ({error_type}). Check the instance and the case state."))
        except Exception:  # noqa: BLE001
            pass

    # ---------- commands that act on the fixed option list ----------
    @staticmethod
    def _command(msg):
        """Exact commands on the first line: 'PROPOSE <n>' or 'ESCALATE [note]'. Anything else is just a question."""
        first = next((l.strip() for l in (msg.get("body_text") or "").splitlines() if l.strip()), "")
        tokens = first.split()
        if len(tokens) == 2 and tokens[0].upper() == "PROPOSE" and tokens[1].isdigit():
            return "PROPOSE", int(tokens[1])
        if tokens and tokens[0].upper() == "ESCALATE":
            return "ESCALATE", " ".join(tokens[1:])[:200]
        return None

    def _options(self, case):
        return json.loads(case.get("optionsJson") or "[]")

    def _refuse(self, msg, sender, case_id, text):
        self._clarify(msg, sender, text, case_id)
        return {"result": "REFUSED", "reason": text[:80]}

    def _propose(self, msg, sender, cls, n):
        """A human chooses option n. Builds a NEW plan version (old pending plan becomes void) with a fresh token."""
        case_id = cls.get("case_id")
        item = self.repos.case.get(case_id) if case_id else None
        if not item:
            return {"intent": "PROPOSE", **self._clarify(msg, sender, "I could not find that case.")}
        state = self.repos.case.state(case_id)
        if state is not S.AWAITING_APPROVAL and state not in REOPENABLE:
            return {"intent": "PROPOSE", **self._refuse(msg, sender, case_id,
                    f"The case is {state.value}, so a new plan cannot be proposed now." + self._decision_note(case_id, item.get("planId")))}
        options = self._options(item)
        opt = next((o for o in options if o["n"] == n), None)
        if opt is None:
            return {"intent": "PROPOSE", **self._refuse(msg, sender, case_id,
                    f"There is no option {n}. This case has options 1 to {len(options)}." if options else "This case has no options to choose from.")}
        if opt["capability"] not in WRITE_CAPABILITIES:
            return {"intent": "PROPOSE", **self._refuse(msg, sender, case_id,
                    f"Option {n} ({opt['capability']}) is not an action, so there is nothing to approve. Reply ESCALATE to hand the case to a person.")}

        pending = self._pending_plan(case_id)
        if not self.repos.case.transition(case_id, S.INVESTIGATING, [state]):        # atomic: loses to a concurrent approval
            return {"intent": "PROPOSE", **self._refuse(msg, sender, case_id, "The case changed while I was handling your request. Please check its state and retry.")}
        self.repos.case.transition(case_id, S.DIAGNOSED, [S.INVESTIGATING])
        params = opt.get("parameters") or {}
        diagnosis = json.loads(item.get("diagnosisJson") or "null") or {"summary": item.get("diagnosisSummary", ""), "confidence": "LOW"}
        try:
            plan, token = build_plan(
                self.repos, self.cfg, case_id, self.cfg.demo_instance_id, opt["capability"],
                {k: params.get(k) for k in ("process_id", "expected_process_name")} if opt["capability"] == "terminate_process" else {},
                (pending or {}).get("evidenceRefs", []), diagnosis.get("summary", ""), opt.get("impact") or "See plan.",
                opt.get("risk") or "See plan.", VERIFY[opt["capability"]],
                meta={"proposedBy": sender, "proposedVia": "operator", "proposedAt": now_iso(),
                      "optionNumber": n, "agentRank": opt.get("rank", n)})
        except ValueError as error:
            self.repos.case.transition(case_id, S.ESCALATED, [S.DIAGNOSED], escalationReason="chosen option was not a valid plan")
            return {"intent": "PROPOSE", **self._refuse(msg, sender, case_id, f"That option could not be turned into a valid governed plan ({error}). The case was escalated.")}
        self.repos.case.transition(case_id, S.AWAITING_APPROVAL, [S.DIAGNOSED], planId=plan["planId"])
        self.repos.audit.append(case_id, "PLAN_PROPOSED_BY_OPERATOR", {
            "by": sender, "option": n, "planId": plan["planId"], "replaces": plan.get("supersedes")})
        inv = Investigation(case_id, S.AWAITING_APPROVAL, case={**item, "resource": self.cfg.demo_instance_id}, diagnosis=diagnosis,
                            evidence=self._read_evidence(case_id), outcomes=options, plan=plan, token=token)
        send_investigation_mail(self.svc.ses, self.cfg, inv)
        self._clarify(msg, sender, f"Plan {plan['planId']} was created from your choice of option {n}"
                      + (f" and replaces {plan['supersedes']}, which is now void" if plan.get("supersedes") else "")
                      + ". Approvers have been sent the approval command; nothing has been executed.", case_id)
        return {"intent": "PROPOSE", "result": "PLAN_PROPOSED", "planId": plan["planId"], "replaces": plan.get("supersedes")}

    def _escalate_cmd(self, msg, sender, cls, note):
        """A human takes the case over. Any pending plan is withdrawn so it can never be approved."""
        case_id = cls.get("case_id")
        item = self.repos.case.get(case_id) if case_id else None
        if not item:
            return {"intent": "ESCALATE", **self._clarify(msg, sender, "I could not find that case.")}
        state = self.repos.case.state(case_id)
        if state is S.ESCALATED:
            return {"intent": "ESCALATE", **self._refuse(msg, sender, case_id, "This case is already escalated." + self._decision_note(case_id, item.get("planId")))}
        if state is not S.AWAITING_APPROVAL and state not in REOPENABLE:
            return {"intent": "ESCALATE", **self._refuse(msg, sender, case_id, f"The case is {state.value}; it cannot be escalated from that state.")}
        reason = note or "no reason given"
        pending = self._pending_plan(case_id)
        if not self.repos.case.transition(case_id, S.ESCALATED, [state], escalatedBy=sender, escalatedAt=now_iso(),
                                          escalationReason=f"escalated by {sender}: {reason}"[:300]):
            return {"intent": "ESCALATE", **self._refuse(msg, sender, case_id, "The case changed while I was handling your request. Please check its state.")}
        if pending:
            self.repos.plan.set_status(case_id, pending["planId"], PLAN_WITHDRAWN, [PLAN_PROPOSED])
        self.repos.audit.append(case_id, "ESCALATED_BY_OPERATOR", {"by": sender, "withdrawnPlan": pending["planId"] if pending else None})
        everyone = sorted(self.known)
        self.svc.ses.send(everyone, *render_escalation({**item, "caseId": case_id}, reason, self._read_evidence(case_id),
                                                       requested_by=sender, withdrawn_plan=pending["planId"] if pending else None))
        return {"intent": "ESCALATE", "result": "ESCALATED", "withdrawnPlan": pending["planId"] if pending else None}
