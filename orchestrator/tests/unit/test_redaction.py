from cloudops_orchestrator.redaction import redact, redact_text, REDACTED


def test_approve_token_scrubbed():
    assert "s3cr3t" not in redact_text("APPROVE PLAN-52650C02BEB8V1 s3cr3t-token")
    assert "PLAN-52650C02BEB8V1" in redact_text("APPROVE PLAN-52650C02BEB8V1 s3cr3t-token")


def test_sensitive_keys_and_nesting():
    out = redact({"approvalToken": "x", "nested": {"password": "y", "ok": 1}, "list": [{"apiKey": "z"}]})
    assert out == {"approvalToken": REDACTED, "nested": {"password": REDACTED, "ok": 1}, "list": [{"apiKey": REDACTED}]}


def test_aws_keys_scrubbed():
    assert "AKIAABCDEFGHIJKLMNOP" not in redact_text("key AKIAABCDEFGHIJKLMNOP here")


def test_long_strings_truncated():
    assert len(redact_text("a" * 5000)) < 2100
