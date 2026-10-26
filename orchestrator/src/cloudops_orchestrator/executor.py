"""Execute-once ACTION invocation via ACTION Gateway only; conditional-write idempotency."""
import json
import logging
from datetime import datetime, timezone

from .clients._gateway import GatewayDenied, GatewayError, GatewayTransportError, ToolError
from .emails.approval_result import render_ack, render_result
from .emails.escalation import render_escalation
from .investigation import Investigation, case_payload, conclude
from .models import PLAN_APPROVED, PLAN_NOT_EXECUTED
from .notifier import send_investigation_mail
from .states import CaseState as S
from .verification import parse_rows

logger = logging.getLogger()

IDLE_CPU = 20.0            # a matching process below this, with the alarm OK, is no longer "the incident"
FIRST_WAIT = {"terminate_process": 45, "reboot_demo_instance": 90}
SKIP_VERIFY = ("DENIED", "FAILED", "TOOL_ERROR")


def precheck(read, cfg, plan):
    """
    Re-read the world before acting. Returns (decision, reason):
    PROCEED | NO_ACTION | STALE | UNKNOWN. Only PROCEED may lead to a WRITE.
    """
    try:
        instance, alarm = read.describe_instance(), read.alarm()
    except Exception as error:  # noqa: BLE001
        return "UNKNOWN", f"pre-action checks could not run ({type(error).__name__})"
    tags = {t.get("Key"): t.get("Value") for t in instance.get("tags") or []}
    if instance.get("instanceId") != plan["resource"] or instance.get("state") != "running" or tags.get("AgentRemediation") != "Enabled":
        return "UNKNOWN", "the instance is not in the expected, remediation-enabled, running state"
    alarm_state = alarm.get("stateValue")
    if alarm_state not in ("ALARM", "OK"):
        return "UNKNOWN", f"the CPU alarm state is {alarm_state}"

    if plan["capability"] == "reboot_demo_instance":
        if alarm_state == "ALARM":
            return "PROCEED", "alarm is still active"
        return "NO_ACTION", "the CPU alarm has returned to OK, so the incident is no longer active"

    pid, name = plan["parameters"]["process_id"], plan["parameters"]["expected_process_name"]
    try:
        details = read.process_details(pid)
    except Exception as error:  # noqa: BLE001
        return "UNKNOWN", f"the process check could not run ({type(error).__name__})"
    status = details.get("status")
    if status == "INCONCLUSIVE" or status not in ("FOUND", "NOT_FOUND"):
        return "UNKNOWN", "the process listing was inconclusive"
    rows = parse_rows(details.get("output")) if status == "FOUND" else []
    if rows and rows[0]["comm"] == name:
        if alarm_state == "ALARM" or rows[0]["cpu"] >= IDLE_CPU:
            return "PROCEED", f"process {name} (pid {pid}) is still running"
        return "NO_ACTION", f"process {name} is idle and the CPU alarm is OK"
    what = "no longer exists" if not rows else f"is now a different program ({rows[0]['comm']})"
    if alarm_state == "ALARM":
        return "STALE", f"pid {pid} {what}, but the CPU alarm is still active, so the plan no longer matches the problem"
    return "NO_ACTION", f"pid {pid} {what} and the CPU alarm has cleared"


class Executor:
    def __init__(self, svc):
        self.svc, self.cfg, self.repos = svc, svc.cfg, svc.repos
        self.everyone = sorted(set(self.cfg.operator_emails) | set(self.cfg.approver_emails))

    def _mail(self, to, rendered, case_id, event):
        try:
            message_id = self.svc.ses.send(to, *rendered)
            self.repos.audit.append(case_id, event, {"sesMessageId": message_id, "to": len(to)})
        except Exception as error:  # noqa: BLE001 - a mail failure never changes what was or was not executed
            self.repos.audit.append(case_id, "EMAIL_FAILED", {"event": event, "error": type(error).__name__})

    def _case(self, case_id):
        return self.repos.case.get(case_id)

    def _finish_without_action(self, case_id, plan, final_state, reason, sender):
        self.repos.plan.set_status(case_id, plan["planId"], PLAN_NOT_EXECUTED, [PLAN_APPROVED], notExecutedReason=reason[:300])
        self.repos.case.transition(case_id, final_state, [S.APPROVED], escalationReason=reason[:300])
        self.repos.audit.append(case_id, "NOT_EXECUTED", {"planId": plan["planId"], "state": final_state.value, "reason": reason})
        case = self._case(case_id)
        if final_state is S.ESCALATED:
            rendered = render_escalation({**case, "caseId": case_id}, f"Your approval was accepted but NOT acted on: {reason}")
        else:
            rendered = render_result(case_id, plan, final_state.value, case.get("diagnosisSummary", ""),
                                     reason, note="Your approval was valid, but I re-checked the situation first and did not act.")
        self._mail(self.everyone, rendered, case_id, "RESULT_SENT")
        return {"result": f"NOT_EXECUTED_{final_state.value}", "reason": reason}

    def _replan(self, case_id, plan, reason):
        """The approved plan no longer fits: withdraw it and ask the agent again. A NEW approval is required."""
        self.repos.plan.set_status(case_id, plan["planId"], PLAN_NOT_EXECUTED, [PLAN_APPROVED], notExecutedReason=reason[:300])
        self.repos.case.transition(case_id, S.INVESTIGATING, [S.APPROVED])
        self.repos.audit.append(case_id, "PLAN_STALE", {"planId": plan["planId"], "reason": reason})
        item = self._case(case_id)
        inv = Investigation(case_id, S.INVESTIGATING, case={**item, "resource": self.cfg.demo_instance_id})
        try:
            result = self.svc.reasoning.invoke("investigate", case_id, case=case_payload(self.cfg, item),
                                               plan_summary=f"previous plan {plan['planId']} was approved but became stale: {reason}")
            inv = conclude(self.repos, self.cfg, inv, result, requested_by="stale-plan-replan", via="stale-replan", reason_note=reason)
        except Exception as error:  # noqa: BLE001
            self.repos.case.transition(case_id, S.ESCALATED, [S.INVESTIGATING], escalationReason="re-investigation failed")
            inv.state, inv.reason = S.ESCALATED, f"The approved plan became stale ({reason}) and re-investigation failed ({type(error).__name__})."
        send_investigation_mail(self.svc.ses, self.cfg, inv)
        return {"result": "STALE_REPLANNED", "reason": reason, "state": inv.state.value, "newPlan": inv.plan["planId"] if inv.plan else None}

    def _verification_start(self, case_id, plan):
        try:
            self.svc.sfn.start_execution(
                stateMachineArn=self.cfg.verification_workflow_arn, name=plan["planId"],
                input=json.dumps({"caseId": case_id, "planId": plan["planId"], "attempt": 0, "done": False,
                                  "waitSeconds": FIRST_WAIT[plan["capability"]]}))
            return True
        except Exception as error:  # noqa: BLE001
            if type(error).__name__ == "ExecutionAlreadyExists":
                return True
            self.repos.audit.append(case_id, "VERIFICATION_START_FAILED", {"error": type(error).__name__})
            return False

    def run(self, msg, sender, auth):
        """auth: a successful authorize_approval Result (case APPROVED, token consumed)."""
        case_id, plan = auth.case_id, auth.plan
        decision, reason = precheck(self.svc.read, self.cfg, plan)
        self.repos.audit.append(case_id, "PRECHECK", {"planId": plan["planId"], "decision": decision, "reason": reason})
        if decision == "NO_ACTION":
            return self._finish_without_action(case_id, plan, S.NO_ACTION, reason, sender)
        if decision == "UNKNOWN":
            return self._finish_without_action(case_id, plan, S.ESCALATED, reason, sender)
        if decision == "STALE":
            return self._replan(case_id, plan, reason)

        if not self.repos.execution.claim(case_id, plan["planId"], plan["capability"], plan["parametersHash"]):
            self.repos.audit.append(case_id, "DUPLICATE_EXECUTION_BLOCKED", {"planId": plan["planId"]})
            return {"result": "DUPLICATE_EXECUTION_BLOCKED"}
        self.repos.case.transition(case_id, S.EXECUTING, [S.APPROVED])
        self._mail([sender], render_ack(case_id, plan), case_id, "ACK_SENT")

        outcome, result = "UNKNOWN", {}
        try:
            result = self.svc.action.execute_plan(plan)
            outcome = str(result.get("status", "UNKNOWN")).upper()
        except GatewayDenied as error:
            outcome, result = "DENIED", {"error": str(error)[:200]}
        except GatewayTransportError as error:
            outcome, result = "UNKNOWN", {"error": f"transport: {error}"[:200]}      # may have executed: verification decides
        except (ToolError, GatewayError, ValueError) as error:
            outcome, result = "TOOL_ERROR", {"error": f"{type(error).__name__}: {error}"[:200]}
        self.repos.execution.finish(case_id, plan["planId"], outcome, result)
        self.repos.audit.append(case_id, "ACTION_RESULT", {"planId": plan["planId"], "outcome": outcome})

        if outcome in SKIP_VERIFY:
            state = S.ESCALATED if outcome == "DENIED" else S.REMEDIATION_FAILED
            self.repos.case.transition(case_id, state, [S.EXECUTING], escalationReason=f"action {outcome}")
            self._mail(self.everyone, render_result(
                case_id, plan, state.value, self._case(case_id).get("diagnosisSummary", ""), "no change was made",
                action_outcome=f"{outcome}: {result.get('error') or result.get('details', '')}",
                note="The action did not run, so there is nothing to verify."), case_id, "RESULT_SENT")
            return {"result": f"ACTION_{outcome}", "state": state.value}

        self.repos.case.transition(case_id, S.VERIFYING, [S.EXECUTING])
        if not self._verification_start(case_id, plan):
            self.repos.case.transition(case_id, S.ESCALATED, [S.VERIFYING], escalationReason="verification could not start")
            self._mail(self.everyone, render_escalation({**self._case(case_id), "caseId": case_id},
                       f"The action was executed ({outcome}) but verification could not be started. Check the instance manually."),
                       case_id, "RESULT_SENT")
            return {"result": "VERIFICATION_NOT_STARTED", "state": "ESCALATED"}
        return {"result": "EXECUTING", "action": outcome, "planId": plan["planId"]}
