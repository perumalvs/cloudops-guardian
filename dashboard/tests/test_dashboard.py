import json
import sys
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lambda_function as d  # noqa: E402

CASE = "CG-0123456789AB"


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "x")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "x")
    with mock_aws():
        t = boto3.resource("dynamodb").create_table(
            TableName="CloudOpsGuardian-CaseState", BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "PK", "AttributeType": "S"}, {"AttributeName": "SK", "AttributeType": "S"}])
        d._table = None
        for item in [
            {"PK": f"CASE#{CASE}", "SK": "METADATA", "caseId": CASE, "status": "AWAITING_APPROVAL",
             "alarmName": "CPU", "source": "CloudWatch", "createdAt": "2026-10-20T10:00:00+00:00", "stateVersion": 3},
            {"PK": f"CASE#{CASE}", "SK": "PLAN#PLAN-0123456789ABV1", "type": "PLAN", "planId": "PLAN-0123456789ABV1",
             "capability": "terminate_process", "tokenHash": "SECRETHASH", "approverEmails": ["boss@corp.com"],
             "parametersJson": '{"pid": 42}', "planStatus": "PROPOSED", "createdAt": "2026-10-20T10:02:00+00:00"},
            {"PK": f"CASE#{CASE}", "SK": "AUDIT#1", "type": "AUDIT", "event": "PLAN_SENT", "createdAt": "2026-10-20T10:03:00+00:00",
             "summary": json.dumps({"to": "alice@example.com", "note": "APPROVE PLAN-0123456789ABV1 tok123"})},
            {"PK": f"CASE#{CASE}", "SK": "APPROVAL#1", "type": "APPROVAL", "decision": "APPROVED", "reason": "ok",
             "sender": "alice@example.com", "messageId": "<m@x>", "createdAt": "2026-10-20T10:04:00+00:00"},
            {"PK": "CASE#CG-AAAAAAAAAAAA", "SK": "METADATA", "caseId": "CG-AAAAAAAAAAAA", "status": "OPEN",
             "createdAt": "2026-10-21T10:00:00+00:00"},
            {"PK": "MSG#abc", "SK": "DEDUP", "type": "MESSAGE"},
        ]:
            t.put_item(Item=item)
        yield t


def call(path, method="GET"):
    return d.lambda_handler({"rawPath": path, "requestContext": {"http": {"method": method}}}, None)


def test_list_newest_first_and_only_cases(db):
    body = json.loads(call("/api/cases")["body"])["cases"]
    assert [c["caseId"] for c in body] == ["CG-AAAAAAAAAAAA", CASE]
    assert "stateVersion" not in body[0]


def test_detail_masks_and_drops_secrets(db):
    r = call(f"/api/cases/{CASE}")
    text = r["body"]
    assert r["statusCode"] == 200
    for leak in ("alice@example.com", "boss@corp.com", "SECRETHASH", "tok123", "<m@x>"):
        assert leak not in text
    data = json.loads(text)
    assert [e["at"] for e in data["timeline"]] == sorted(e["at"] for e in data["timeline"])
    assert {e["kind"] for e in data["timeline"]} == {"case", "plan", "audit", "approval"}
    assert data["plans"][0]["parametersJson"] == {"pid": 42}
    assert "a***@example.com" in text


def test_case_id_not_mangled_by_account_scrub():
    assert d.scrub("CG-123456789012 acct 123456789012") == "CG-123456789012 acct ************"


def test_unknown_and_invalid_paths(db):
    assert call("/api/cases/CG-FFFFFFFFFFFF")["statusCode"] == 404
    assert call("/api/cases/CASE%23x")["statusCode"] == 404
    assert call("/nope")["statusCode"] == 404


def test_read_only(db):
    assert call("/api/cases", "POST")["statusCode"] == 405
    assert call("/api/cases", "DELETE")["statusCode"] == 405


def test_root_serves_html(db):
    r = call("/")
    assert r["statusCode"] == 200 and "text/html" in r["headers"]["content-type"] and "CloudOps Guardian" in r["body"]


def _verify(attempt, passed, blocking, checks_json=None):
    checks = checks_json if checks_json is not None else json.dumps({"checks": {}, "detail": {}, "blocking": blocking})
    return {"attempt": attempt, "passed": passed, "checks": checks}


def test_verification_titles_wait_then_pass():
    assert d.verification_title(_verify(1, False, ["alarm_ok", "cpu_ok"])) == \
        "Verification attempt 1: not yet recovered, waiting on alarm_ok, cpu_ok"
    assert d.verification_title(_verify(2, False, ["alarm_ok"])) == \
        "Verification attempt 2: not yet recovered, waiting on alarm_ok"
    assert d.verification_title(_verify(3, True, [])) == "Verification passed (attempt 3)"


def test_verification_title_survives_bad_records():
    assert d.verification_title(_verify(1, False, [], checks_json="not json")) == "Verification attempt 1: not yet recovered"
    assert d.verification_title({"passed": False}) == "Verification round: not yet recovered"


def test_timeline_shows_waiting_not_failed(db):
    db.put_item(Item={"PK": f"CASE#{CASE}", "SK": "VERIFY#PLAN-0123456789ABV1#1", "type": "VERIFICATION", "attempt": 1,
                      "passed": False, "createdAt": "2026-10-20T10:05:00+00:00",
                      "checks": json.dumps({"checks": {"alarm_ok": False}, "detail": {}, "blocking": ["alarm_ok"]})})
    titles = [e["title"] for e in json.loads(call(f"/api/cases/{CASE}")["body"])["timeline"]]
    assert "Verification attempt 1: not yet recovered, waiting on alarm_ok" in titles
    assert not any("failed" in t for t in titles)
