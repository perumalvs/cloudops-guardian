from cloudops_orchestrator.mime_reader import approval_token, read_inbound

SES = ("X-SES-Spam-Verdict: PASS\r\nX-SES-Virus-Verdict: PASS\r\n"
       "Authentication-Results: amazonses.com; spf=pass (sender ok) smtp.mailfrom=example.com; "
       "dkim=pass header.i=@example.com; dmarc=pass header.from=example.com;\r\n")
BODY = "APPROVE PLAN-52650C02BEB8V1 tok_ABC-123\r\n\r\nThanks,\r\nBoss\r\n\r\nOn Mon, x wrote:\r\n> quoted\r\n"


def mail(prefix="", body=BODY, extra=""):
    return (f"{prefix}Return-Path: <a@example.com>\r\n{extra}From: Boss <boss@example.com>\r\nTo: __SES_FROM_ADDRESS__\r\n"
            f"Subject: Re: [CG-52650C02BEB8]\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n{body}").encode()


def test_good_mail_verdicts_and_token():
    r = read_inbound(mail(SES))
    assert r["verdicts"] == {"spam": "PASS", "virus": "PASS", "spf": "PASS", "dkim": "PASS", "dmarc": "PASS"}
    assert r["token"] == "tok_ABC-123"


def test_forged_headers_below_the_ses_ones_are_ignored():
    forged = "X-SES-Spam-Verdict: PASS\r\nAuthentication-Results: amazonses.com; dmarc=pass; spf=pass; dkim=pass\r\n"
    ses_fail = ("X-SES-Spam-Verdict: FAIL\r\nX-SES-Virus-Verdict: PASS\r\n"
                "Authentication-Results: amazonses.com; spf=fail; dkim=none; dmarc=fail;\r\n")
    r = read_inbound(mail(ses_fail, extra=forged))
    assert r["verdicts"]["spam"] == "FAIL" and r["verdicts"]["dmarc"] == "FAIL" and r["verdicts"]["spf"] == "FAIL"


def test_authentication_results_from_another_server_is_not_trusted():
    other = "X-SES-Spam-Verdict: PASS\r\nX-SES-Virus-Verdict: PASS\r\nAuthentication-Results: evil.example; dmarc=pass; spf=pass; dkim=pass\r\n"
    v = read_inbound(mail(other))["verdicts"]
    assert v["dmarc"] == v["spf"] == v["dkim"] == ""


def test_missing_ses_headers_fail_closed():
    v = read_inbound(mail(""))["verdicts"]
    assert set(v.values()) == {""}


def test_any_passing_dkim_signature_counts():
    hdr = "X-SES-Spam-Verdict: PASS\r\nAuthentication-Results: amazonses.com; dkim=fail; dkim=pass; spf=pass; dmarc=pass\r\n"
    assert read_inbound(mail(hdr))["verdicts"]["dkim"] == "PASS"


def test_token_only_from_the_first_line_of_the_latest_reply():
    assert read_inbound(mail(SES, body="hello\r\nAPPROVE PLAN-52650C02BEB8V1 tok\r\n"))["token"] is None
    assert read_inbound(mail(SES, body="> APPROVE PLAN-52650C02BEB8V1 quoted\r\n"))["token"] is None
    assert read_inbound(mail(SES, body="YES\r\n"))["token"] is None
    assert read_inbound(mail(SES, body="APPROVE PLAN-52650C02BEB8V1\r\n"))["token"] is None
    assert approval_token("\n\n  approve plan-52650c02beb8v1  tok \n") == "tok"


def test_multipart_alternative_uses_plain_part():
    raw = (SES + "From: b@example.com\r\nSubject: Re: x\r\nMIME-Version: 1.0\r\n"
           "Content-Type: multipart/alternative; boundary=B\r\n\r\n--B\r\nContent-Type: text/plain\r\n\r\n"
           "APPROVE PLAN-52650C02BEB8V1 tk1\r\n--B\r\nContent-Type: text/html\r\n\r\n<p>APPROVE PLAN-52650C02BEB8V1 nope</p>\r\n--B--\r\n").encode()
    assert read_inbound(raw)["token"] == "tk1"
