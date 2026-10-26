import io
import json

import pytest
from conftest import CASE
from cloudops_orchestrator.clients.reasoning_runtime import validate_reasoning_response
from cloudops_orchestrator.clients.ses_outbound import SesOutbound
from cloudops_orchestrator.config import Config
from cloudops_orchestrator.handlers.entry import lambda_handler as entry
from cloudops_orchestrator.handlers.inbound_handler import fetch_raw, handle_envelope
from cloudops_orchestrator.plan_builder import build_plan
from cloudops_orchestrator.router import Router
from cloudops_orchestrator.services import Services
from cloudops_orchestrator.states import CaseState as S

INSTANCE = "__DEMO_INSTANCE_ID__"
BUCKET = "__INBOUND_BUCKET__"
CFG = Config(operator_emails=("op@x.com", "boss@x.com"), approver_emails=("boss@x.com",), token_pepper="pepper",
             demo_instance_id=INSTANCE, inbound_bucket=BUCKET)
GOOD = {"spam": "PASS", "virus": "PASS", "spf": "PASS", "dkim": "PASS", "dmarc": "PASS"}
PLAN1 = f"PLAN-{CASE[3:]}V1"


class FakeReasoning:
    def __init__(self, raw=None, error=None):
        self.raw, self.error, self.calls = raw, error, []

    def invoke(self, mode, case_id, **ctx):
        self.calls.append((mode, case_id, ctx))
        if self.error:
            raise self.error
        return validate_reasoning_response(self.raw)


class FakeSes:
    def __init__(self):
        self.sent = []

    def send_raw_email(self, **kw):
        self.sent.append(kw)
        return {"MessageId": f"ses-{len(self.sent)}"}

    def mails(self):
        return [(m["Destinations"], m["RawMessage"]["Data"].decode()) for m in self.sent]


def setup(repos, raw, error=None):
    ses = FakeSes()
    reasoning = FakeReasoning(raw, error)
    return Services(CFG, repos, reasoning, SesOutbound(CFG, client=ses)), reasoning, ses


CALM = {"diagnosis": {"summary": "normal", "confidence": "HIGH"}, "evidence": [{"tool": "describe_cpu_alarm", "summary": "OK", "data": {}}],
        "outcomes": [{"rank": 1, "capability": "CONTINUE_MONITORING"}], "answer": "All calm."}
KILL = {"diagnosis": {"summary": "yes hot", "confidence": "HIGH"},
        "evidence": [{"tool": "get_top_cpu_processes", "summary": "yes(pid 4242) 99%", "data": {}}],
        "outcomes": [{"rank": 1, "capability": "terminate_process", "parameters": {"process_id": 4242, "expected_process_name": "yes"},
                      "impact": "stops yes", "risk": "low"}], "answer": "yes is the cause."}


def make_plan(repos, pid=4242):
    repos.case.transition(CASE, S.INVESTIGATING)
    repos.case.transition(CASE, S.DIAGNOSED)
    plan, token = build_plan(repos, CFG, CASE, INSTANCE, "terminate_process", {"process_id": pid, "expected_process_name": "yes"},
                             [], "yes hot", "i", "r", "v")
    repos.case.transition(CASE, S.AWAITING_APPROVAL)
    return plan, token


def msg(intent, sender="boss@x.com", case=CASE, plan=None, body="", mid="<m1>"):
    return {"message_id": mid, "from": [sender], "subject": f"Re: [{case}] x", "references": [], "body_text": body,
            "s3_bucket": BUCKET, "s3_key": "k", "classification": {"intent": intent, "case_id": case, "plan_id": plan}}


def raw(token=None, verdicts=GOOD):
    return {"verdicts": verdicts, "token": token}


def test_unknown_sender_and_failed_mail_auth_get_silence(repos):
    svc, reasoning, ses = setup(repos, CALM)
    r = Router(svc)
    assert r.route(msg("CASE_FOLLOWUP", sender="stranger@evil.com", body="why"), raw())["result"] == "DROPPED_UNKNOWN_SENDER"
    assert r.route(msg("CASE_FOLLOWUP", body="why"), raw(verdicts={**GOOD, "dmarc": "FAIL", "spf": "FAIL"}))["result"] == "DROPPED_MAIL_AUTH"
    m = msg("CASE_FOLLOWUP"); m["from"] = ["boss@x.com", "evil@x.com"]
    assert r.route(m, raw())["result"] == "DROPPED_UNKNOWN_SENDER"
    assert ses.sent == [] and reasoning.calls == []


def test_details_replies_to_sender_and_never_leaks_tokens(repos):
    plan, token = make_plan(repos)
    repos.evidence.add(CASE, "READ", "get_top_cpu_processes", {"summary": "yes(pid 4242) 99%"})
    svc, _, ses = setup(repos, CALM)
    out = Router(svc).route(msg("CASE_DETAILS", sender="op@x.com", body=f"DETAILS {CASE}"), raw())
    assert out["result"] == "SENT"
    (dest, body), = ses.mails()
    assert dest == ["op@x.com"] and "AWAITING_APPROVAL" in body and "yes(pid 4242) 99%" in body and PLAN1 in body
    assert token not in body and "tokenHash" not in body and "APPROVE PLAN" not in body


def test_details_unknown_case(repos):
    svc, _, ses = setup(repos, CALM)
    assert Router(svc).route(msg("CASE_DETAILS", case="CG-AAAAAAAAAAAA"), raw())["result"] == "CLARIFIED"
    assert "could not find that case" in ses.mails()[0][1]


def test_followup_same_plan_is_answer_only(repos):
    plan, _ = make_plan(repos)
    svc, reasoning, ses = setup(repos, KILL)
    out = Router(svc).route(msg("CASE_FOLLOWUP", body="what is yes?"), raw())
    assert out["result"] == "ANSWERED" and out["newPlan"] is None and out["state"] == "AWAITING_APPROVAL"
    mode, case_id, ctx = reasoning.calls[0]
    assert mode == "followup" and ctx["question"] == "what is yes?" and PLAN1 in ctx["plan_summary"]
    assert [p["planStatus"] for p in repos.plan.list(CASE)] == ["PROPOSED"]
    assert len(ses.sent) == 1 and "yes is the cause." in ses.mails()[0][1] and "APPROVE" not in ses.mails()[0][1]


def test_followup_that_changes_the_plan_supersedes_and_sends_a_new_token_to_approvers_only(repos):
    plan, old_token = make_plan(repos, pid=4242)
    changed = {**KILL, "outcomes": [{"rank": 1, "capability": "terminate_process",
                                      "parameters": {"process_id": 5000, "expected_process_name": "yes"}}]}
    svc, _, ses = setup(repos, changed)
    out = Router(svc).route(msg("CASE_FOLLOWUP", body="there is another yes"), raw())
    assert out["newPlan"] == f"PLAN-{CASE[3:]}V2" and repos.case.state(CASE) is S.AWAITING_APPROVAL
    statuses = {p["planId"]: p["planStatus"] for p in repos.plan.list(CASE)}
    assert statuses == {PLAN1: "SUPERSEDED", out["newPlan"]: "PROPOSED"}
    approver_mail = [b for d, b in ses.mails() if d == ["boss@x.com"] and "APPROVE PLAN" in b]
    operator_mail = [b for d, b in ses.mails() if d == ["op@x.com"]]
    assert approver_mail and f"APPROVE {out['newPlan']} " in approver_mail[0]
    assert operator_mail and all("APPROVE PLAN" not in b for b in operator_mail) and old_token not in str(ses.mails())


def test_followup_reopens_a_closed_case(repos):
    repos.case.transition(CASE, S.INVESTIGATING); repos.case.transition(CASE, S.NO_ACTION)
    svc, _, ses = setup(repos, KILL)
    out = Router(svc).route(msg("CASE_FOLLOWUP", body="check again, it is hot now"), raw())
    assert out["state"] == "AWAITING_APPROVAL" and out["newPlan"] == PLAN1


@pytest.mark.parametrize("state", [S.EXECUTING, S.VERIFYING, S.RESOLVED])
def test_followup_in_active_or_final_states_never_changes_state_or_plans(repos, state):
    path = {S.EXECUTING: [S.INVESTIGATING, S.DIAGNOSED, S.AWAITING_APPROVAL, S.APPROVED, S.EXECUTING],
            S.VERIFYING: [S.INVESTIGATING, S.DIAGNOSED, S.AWAITING_APPROVAL, S.APPROVED, S.EXECUTING, S.VERIFYING],
            S.RESOLVED: [S.INVESTIGATING, S.DIAGNOSED, S.AWAITING_APPROVAL, S.APPROVED, S.EXECUTING, S.VERIFYING, S.RESOLVED]}[state]
    for s in path:
        assert repos.case.transition(CASE, s)
    svc, _, ses = setup(repos, KILL)
    out = Router(svc).route(msg("CASE_FOLLOWUP", body="status?"), raw())
    assert out["state"] == state.value and repos.plan.list(CASE) == [] and len(ses.sent) == 1


def test_followup_reasoning_failure_is_reported_not_silent(repos):
    make_plan(repos)
    svc, _, ses = setup(repos, None, error=RuntimeError("internal secret"))
    assert Router(svc).route(msg("CASE_FOLLOWUP", body="why"), raw())["result"] == "REASONING_FAILED"
    assert "RuntimeError" in ses.mails()[0][1] and "internal secret" not in ses.mails()[0][1]
    assert repos.case.state(CASE) is S.AWAITING_APPROVAL


def test_ambiguous_query_answers_read_only_and_never_creates_a_plan_or_case(repos):
    svc, reasoning, ses = setup(repos, KILL)
    out = Router(svc).route(msg("NEW_OR_AMBIGUOUS_QUERY", case=None, body="is anything wrong with the server?"), raw())
    assert out["result"] == "ANSWERED" and reasoning.calls[0][0] == "query" and reasoning.calls[0][1].startswith("QUERY-")
    assert repos.plan.list(CASE) == [] and repos.case.state(CASE) is S.DETECTED
    assert "read-only" in ses.mails()[0][1] and "APPROVE" not in ses.mails()[0][1]
    assert Router(svc).route(msg("NEW_OR_AMBIGUOUS_QUERY", case=None, body="  "), raw())["result"] == "CLARIFIED"


def test_generic_yes_on_a_pending_case_does_nothing(repos):
    plan, token = make_plan(repos)
    svc, _, ses = setup(repos, CALM)
    Router(svc).route(msg("CASE_FOLLOWUP", body="YES"), raw())          # parser classifies a bare YES as a follow-up
    assert repos.case.state(CASE) is S.AWAITING_APPROVAL and repos.plan.get(CASE, plan["planId"])["planStatus"] == "PROPOSED"
    assert repos.execution.get(CASE, plan["planId"]) is None


def test_rejection_by_approver_and_duplicate_delivery(repos):
    make_plan(repos)
    svc, _, ses = setup(repos, CALM)
    r = Router(svc)
    assert r.route(msg("REJECTION", plan=PLAN1, mid="<r1>"), raw())["result"] == "REJECTED"
    assert repos.case.state(CASE) is S.REJECTED and "rejected" in ses.mails()[0][1]
    assert r.route(msg("REJECTION", plan=PLAN1, mid="<r1>"), raw())["result"] == "DUPLICATE_MESSAGE"
    assert len(ses.sent) == 1


def test_rejection_denials_are_reported_without_changing_anything(repos):
    make_plan(repos)
    svc, _, ses = setup(repos, CALM)
    assert Router(svc).route(msg("REJECTION", plan="PLAN-52650C02BEB8V9", mid="<r2>"), raw())["result"] == "DENIED_PLAN_NOT_FOUND"
    assert repos.case.state(CASE) is S.AWAITING_APPROVAL


def test_approval_is_a_dry_run_in_this_build(repos):
    plan, token = make_plan(repos)
    svc, _, ses = setup(repos, CALM)
    out = Router(svc).route(msg("APPROVAL", plan=PLAN1), raw(token))
    assert out["result"] == "DRY_RUN_OK" and "VALIDATION ONLY" in ses.mails()[0][1] and "nothing was executed" in ses.mails()[0][1]
    assert repos.case.state(CASE) is S.AWAITING_APPROVAL
    stored = repos.plan.get(CASE, PLAN1)
    assert stored["planStatus"] == "PROPOSED" and "tokenConsumedAt" not in stored       # nothing consumed
    assert repos.execution.get(CASE, PLAN1) is None
    assert token not in str(ses.mails()) and token not in str(repos.plan.store.table.scan()["Items"])


@pytest.mark.parametrize("token,sender,reason", [
    ("wrong", "boss@x.com", "TOKEN_INVALID"), (None, "boss@x.com", "TOKEN_INVALID"),
    ("ignored", "op@x.com", "SENDER_NOT_AUTHORIZED")])
def test_approval_denials(repos, token, sender, reason):
    plan, real = make_plan(repos)
    svc, _, ses = setup(repos, CALM)
    out = Router(svc).route(msg("APPROVAL", sender=sender, plan=PLAN1), raw(token))
    assert out["result"] == f"DENIED_{reason}" and len(ses.sent) == 1
    assert repos.case.state(CASE) is S.AWAITING_APPROVAL and real not in str(ses.mails())


# ---- inbound handler / dispatcher ----
class FakeS3:
    def __init__(self, objects):
        self.objects, self.gets = objects, []

    def get_object(self, Bucket, Key):
        self.gets.append((Bucket, Key))
        return {"Body": io.BytesIO(self.objects[Key])}


MIME = (b"X-SES-Spam-Verdict: PASS\r\nX-SES-Virus-Verdict: PASS\r\nAuthentication-Results: amazonses.com; spf=pass; dkim=pass; dmarc=pass\r\n"
        b"From: boss@x.com\r\nSubject: Re: x\r\nContent-Type: text/plain\r\n\r\nAPPROVE PLAN-52650C02BEB8V1 realtoken\r\n")


def test_envelope_reads_raw_mime_and_isolates_failures(repos):
    plan, token = make_plan(repos)
    svc, _, ses = setup(repos, CALM)
    svc.s3 = FakeS3({"good": MIME})
    bad_bucket = {**msg("CASE_FOLLOWUP"), "s3_bucket": "someone-elses-bucket"}
    bad_key = {**msg("CASE_FOLLOWUP", mid="<m3>"), "s3_key": "missing"}
    ok = {**msg("CASE_DETAILS", mid="<m4>", body=f"DETAILS {CASE}"), "s3_key": "good"}
    out = handle_envelope({"responsePayload": {"messages": [bad_bucket, bad_key, ok]}}, svc)
    assert out == {"messages": 3, "results": ["SENT"], "errors": 2}
    assert svc.s3.gets == [(BUCKET, "missing"), (BUCKET, "good")]       # the foreign bucket was never read


def test_fetch_raw_extracts_the_token_the_parser_redacted(repos):
    svc, _, _ = setup(repos, CALM)
    svc.s3 = FakeS3({"k": MIME})
    r = fetch_raw(svc, msg("APPROVAL", plan=PLAN1))
    assert r["token"] == "realtoken" and r["verdicts"]["dmarc"] == "PASS"


def test_entry_dispatch(repos):
    with pytest.raises(ValueError):
        entry({"something": "else"}, None)
