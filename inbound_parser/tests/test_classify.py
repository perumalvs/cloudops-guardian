import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from lambda_function import classify_message as c, redact_sensitive_body as r

CASE = "CG-52650C02BEB8"
PLAN = "PLAN-52650C02BEB8V1"

def test_details():
    x = c(f"Re: [{CASE}] incident", f"DETAILS {CASE}")
    assert x["intent"] == "CASE_DETAILS" and x["case_id"] == CASE

def test_followup_case_from_subject():
    x = c(f"Re: [{CASE}] incident", "why is yes running?")
    assert x["intent"] == "CASE_FOLLOWUP" and x["case_id"] == CASE

def test_approval_and_redaction():
    x = c(f"Re: [{CASE}] {PLAN}", f"APPROVE {PLAN} s3cr3t-token")
    assert x["intent"] == "APPROVAL" and x["plan_id"] == PLAN
    assert x["case_id"] == CASE and x["approval_token_present"] is True
    assert "s3cr3t" not in r(f"APPROVE {PLAN} s3cr3t-token", x)

def test_reject():
    assert c(f"[{CASE}]", f"REJECT {PLAN}")["intent"] == "REJECTION"

def test_generic_yes_is_not_approval():
    assert c(f"Re: [{CASE}]", "YES")["intent"] == "CASE_FOLLOWUP"

def test_no_case_is_ambiguous():
    assert c("hello", "YES")["intent"] == "NEW_OR_AMBIGUOUS_QUERY"

def test_legacy_case_prefix_still_works():
    assert c("x", "DETAILS CASE-ABC123")["intent"] == "CASE_DETAILS"

def test_short_cg_id_rejected():
    assert c("x", "hi CG-123")["case_id"] is None
