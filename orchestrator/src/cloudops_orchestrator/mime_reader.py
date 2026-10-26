"""Raw inbound MIME: SES authentication verdicts and the approval token (which the parser redacts)."""
import re
from email import policy
from email.parser import BytesParser

_SEPARATORS = (
    re.compile(r"^\s*-+\s*Original Message\s*-+\s*$", re.I),
    re.compile(r"^\s*_{5,}\s*$"),
    re.compile(r"^\s*On .+ wrote:\s*$", re.I),
    re.compile(r"^\s*(From|Sent|To|Subject):\s+.+$", re.I),
)
_RESULT = {name: re.compile(rf"\b{name}=(\w+)", re.I) for name in ("spf", "dkim", "dmarc")}
SES_AUTHSERV = "amazonses.com"


def _plain_text(message):
    parts = []
    for part in (message.walk() if message.is_multipart() else [message]):
        if part.get_content_disposition() == "attachment" or part.get_content_type() != "text/plain":
            continue
        try:
            parts.append(part.get_content())
        except Exception:  # noqa: BLE001 - undecodable part: ignore it
            continue
    return "\n".join(parts)


def _latest_reply(body):
    lines = []
    for line in body.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line.lstrip().startswith(">") or any(p.match(line) for p in _SEPARATORS):
            break
        lines.append(line)
    return "\n".join(lines).strip()


def _first_header(message, name):
    """The topmost occurrence. SES prepends its own headers, so sender-forged copies sit below it."""
    values = message.get_all(name) or []
    return str(values[0]).strip() if values else ""


def ses_verdicts(message):
    """
    spam/virus from X-SES-*-Verdict and spf/dkim/dmarc from the topmost
    Authentication-Results, accepted only if SES itself (amazonses.com) wrote it.
    Anything missing or unrecognised is empty, which fails closed downstream.
    """
    out = {"spam": _first_header(message, "X-SES-Spam-Verdict").upper(),
           "virus": _first_header(message, "X-SES-Virus-Verdict").upper(),
           "spf": "", "dkim": "", "dmarc": ""}
    auth = " ".join(_first_header(message, "Authentication-Results").split())
    if auth.split(";", 1)[0].strip().lower() == SES_AUTHSERV:
        for name, pattern in _RESULT.items():
            results = [m.upper() for m in pattern.findall(auth)]
            out[name] = "PASS" if "PASS" in results else (results[0] if results else "")
    return out


def approval_token(latest_reply):
    """Token from a first-line `APPROVE PLAN-... <token>`; None otherwise."""
    lines = [l.strip() for l in latest_reply.splitlines() if l.strip()]
    tokens = lines[0].split() if lines else []
    if len(tokens) >= 3 and tokens[0].upper() == "APPROVE" and tokens[1].upper().startswith("PLAN-"):
        return tokens[2]
    return None


def read_inbound(raw):
    """Returns {"verdicts": {...}, "token": str|None}. The token must never be logged."""
    message = BytesParser(policy=policy.default).parsebytes(raw)
    return {"verdicts": ses_verdicts(message), "token": approval_token(_latest_reply(_plain_text(message)))}
