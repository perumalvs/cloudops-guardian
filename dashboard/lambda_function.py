"""CloudOps Guardian dashboard: read-only view over CloudOpsGuardian-CaseState.

One Lambda behind a Function URL: GET / serves the page, GET /api/cases lists
cases, GET /api/cases/<CG-id> returns one case with its merged timeline.
It never writes. Emails, tokens and message ids are masked or dropped, and
every string is truncated, because the URL is public.
"""
import base64
import json
import os
import re
from decimal import Decimal
from pathlib import Path

import boto3
from boto3.dynamodb.conditions import Attr, Key

CASE_ID_RE = re.compile(r"^CG-[A-F0-9]{12}$")
EMAIL_RE = re.compile(r"([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
APPROVE_RE = re.compile(r"(APPROVE\s+PLAN-[A-Za-z0-9]+)\s+\S+", re.I)
AWS_KEY_RE = re.compile(r"\b(AKIA|ASIA)[A-Z0-9]{16}\b")
ACCOUNT_RE = re.compile(r"(?<![\w-])\d{12}(?!\w)")
SENSITIVE_KEY = re.compile(r"token|secret|password|authorization|credential|api[_-]?key|session|messageid", re.I)
MAX_STR = 1200
MAX_CASES = 50
HTML = Path(__file__).with_name("index.html")

_table = None


def table():
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb").Table(os.environ.get("CASE_TABLE_NAME", "CloudOpsGuardian-CaseState"))
    return _table


def scrub(text):
    text = EMAIL_RE.sub(lambda m: f"{m.group(1)}***@{m.group(2)}", text)
    text = APPROVE_RE.sub(r"\1 [REDACTED]", text)
    text = AWS_KEY_RE.sub("[REDACTED]", text)
    text = ACCOUNT_RE.sub("************", text)
    return text if len(text) <= MAX_STR else text[:MAX_STR] + "...[truncated]"


def clean(obj):
    """JSON-safe copy with sensitive keys dropped and strings scrubbed."""
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items() if not SENSITIVE_KEY.search(str(k))}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    if isinstance(obj, str):
        stripped = obj.strip()
        if stripped[:1] in "{[":                      # stored JSON strings: show them structured
            try:
                return clean(json.loads(stripped))
            except ValueError:
                pass
        return scrub(obj)
    return obj


def query_case(case_id):
    items, kwargs = [], dict(KeyConditionExpression=Key("PK").eq(f"CASE#{case_id}"))
    while True:
        page = table().query(**kwargs)
        items.extend(page.get("Items", []))
        if "LastEvaluatedKey" not in page:
            return items
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def list_cases():
    cases, kwargs = [], dict(FilterExpression=Attr("SK").eq("METADATA"))
    while True:
        page = table().scan(**kwargs)
        cases.extend(page.get("Items", []))
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    cases.sort(key=lambda c: c.get("createdAt", ""), reverse=True)
    keep = ("caseId", "status", "alarmName", "alarmState", "source", "createdAt", "updatedAt")
    return [clean({k: c[k] for k in keep if k in c}) for c in cases[:MAX_CASES]]


def make_event(at, kind, title, detail=None):
    return {"at": at, "kind": kind, "title": title, "detail": clean(detail) if detail else None}


def verification_title(item):
    """A round that is not yet recovered is waiting for monitoring to catch up, not an error. The final give-up is a separate audit event."""
    n = item.get("attempt")
    label = f"attempt {int(n)}" if n is not None else "round"
    if item.get("passed"):
        return f"Verification passed ({label})"
    try:
        blocking = json.loads(item.get("checks") or "{}").get("blocking") or []
    except (ValueError, AttributeError):
        blocking = []
    waiting = f", waiting on {', '.join(str(b) for b in blocking)}" if blocking else ""
    return f"Verification {label}: not yet recovered{waiting}"


def build_case(items):
    meta = next((i for i in items if i["SK"] == "METADATA"), None)
    if meta is None:
        return None
    timeline, plans = [make_event(meta.get("createdAt", ""), "case", f"Case opened from {meta.get('source', 'alarm')}",
                             {"alarm": meta.get("alarmName"), "state": meta.get("alarmState")})], []
    for i in items:
        kind, at = i.get("type"), i.get("createdAt", "")
        if kind == "AUDIT":
            timeline.append(make_event(at, "audit", str(i.get("event", "")).replace("_", " ").title(), i.get("summary")))
        elif kind == "EVIDENCE":
            timeline.append(make_event(at, "evidence", f"{i.get('kind', 'Evidence')}: {i.get('tool', '')}", i.get("payload")))
        elif kind == "PLAN":
            plans.append(clean({k: i.get(k) for k in (
                "planId", "planVersion", "resource", "capability", "parametersJson", "diagnosis", "impact",
                "risk", "verification", "planStatus", "expiresAt", "createdAt") if k in i}))
            timeline.append(make_event(at, "plan", f"Plan {i.get('planId')} proposed: {i.get('capability')}",
                                  {"diagnosis": i.get("diagnosis"), "risk": i.get("risk")}))
        elif kind == "APPROVAL":
            timeline.append(make_event(at, "approval", f"Approval {i.get('decision')} ({i.get('reason')})",
                                  {"plan": i.get("planId"), "by": i.get("sender")}))
        elif kind == "EXECUTION":
            timeline.append(make_event(i.get("finishedAt") or i.get("claimedAt", ""), "execution",
                                  f"Execution {i.get('execStatus')}: {i.get('outcome', 'in progress')}",
                                  {"capability": i.get("capability"), "result": i.get("result")}))
        elif kind == "VERIFICATION":
            timeline.append(make_event(at, "verification", verification_title(i),
                                  {"attempt": i.get("attempt"), "checks": i.get("checks")}))
    timeline.sort(key=lambda e: e["at"])
    keep = ("caseId", "status", "alarmName", "alarmState", "source", "createdAt", "updatedAt")
    return {"case": clean({k: meta[k] for k in keep if k in meta}), "plans": plans, "timeline": timeline}


def respond(status, body, content_type="application/json"):
    return {
        "statusCode": status,
        "headers": {
            "content-type": content_type,
            "cache-control": "no-store",
            "x-content-type-options": "nosniff",
            "content-security-policy": "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'",
        },
        "body": body if isinstance(body, str) else json.dumps(body),
    }


def lambda_handler(event, context):
    http = event.get("requestContext", {}).get("http", {})
    path = event.get("rawPath", "/")
    if http.get("method") not in ("GET", "HEAD"):
        return respond(405, {"error": "read-only"})
    if path == "/":
        return respond(200, HTML.read_text(encoding="utf-8"), "text/html; charset=utf-8")
    if path == "/api/cases":
        return respond(200, {"cases": list_cases()})
    m = re.fullmatch(r"/api/cases/(CG-[A-Fa-f0-9]{12})", path)
    if m and CASE_ID_RE.match(m.group(1).upper()):
        data = build_case(query_case(m.group(1).upper()))
        return respond(200, data) if data else respond(404, {"error": "case not found"})
    return respond(404, {"error": "not found"})
