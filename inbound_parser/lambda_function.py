import json
import logging
import urllib.parse
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses
import re

import boto3


logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")
PARSER_VERSION = "V1.1"


def extract_text_body(message):
    """
    Return the text/plain body where available.
    Falls back to text/html only when no plain-text body exists.
    Attachments are ignored.
    """

    plain_parts = []
    html_parts = []

    if message.is_multipart():
        for part in message.walk():

            if part.get_content_disposition() == "attachment":
                continue

            content_type = part.get_content_type()

            if content_type not in ("text/plain", "text/html"):
                continue

            try:
                content = part.get_content()
            except Exception:
                payload = part.get_payload(decode=True)

                if payload is None:
                    continue

                charset = part.get_content_charset() or "utf-8"

                content = payload.decode(
                    charset,
                    errors="replace"
                )

            if content_type == "text/plain":
                plain_parts.append(content)
            else:
                html_parts.append(content)

    else:
        content_type = message.get_content_type()

        try:
            content = message.get_content()
        except Exception:
            payload = message.get_payload(decode=True)

            if payload is None:
                content = ""
            else:
                charset = message.get_content_charset() or "utf-8"
                content = payload.decode(
                    charset,
                    errors="replace"
                )

        if content_type == "text/plain":
            plain_parts.append(content)
        elif content_type == "text/html":
            html_parts.append(content)

    if plain_parts:
        return "\n".join(plain_parts).strip(), "text/plain"

    if html_parts:
        return "\n".join(html_parts).strip(), "text/html"

    return "", None

def extract_latest_reply(body):
    """
    Best-effort extraction of only the newest operator-written portion
    of a plain-text email reply.

    Stops before common quoted-message separators/header blocks.
    """

    if not body:
        return ""

    body = body.replace("\r\n", "\n").replace("\r", "\n")

    lines = body.split("\n")
    latest_lines = []

    # Common reply/thread separators.
    separator_patterns = [
        re.compile(r"^\s*-+\s*Original Message\s*-+\s*$", re.IGNORECASE),
        re.compile(r"^\s*_{5,}\s*$"),
        re.compile(r"^\s*On .+ wrote:\s*$", re.IGNORECASE),
    ]

    # Common beginning of Outlook-style quoted header blocks.
    header_patterns = [
        re.compile(r"^\s*From:\s+.+$", re.IGNORECASE),
        re.compile(r"^\s*Sent:\s+.+$", re.IGNORECASE),
        re.compile(r"^\s*To:\s+.+$", re.IGNORECASE),
        re.compile(r"^\s*Subject:\s+.+$", re.IGNORECASE),
    ]

    for line in lines:
        # Gmail / conventional quoted lines.
        if line.lstrip().startswith(">"):
            break

        if any(pattern.match(line) for pattern in separator_patterns):
            break

        # In Outlook replies, "From:" commonly begins the quoted
        # previous-message header block.
        if any(pattern.match(line) for pattern in header_patterns):
            break

        latest_lines.append(line)

    latest_reply = "\n".join(latest_lines).strip()

    return latest_reply

def parse_addresses(header_values):
    """
    Normalize email address headers into address strings.
    """

    if not header_values:
        return []

    addresses = getaddresses(header_values)

    return [
        address.lower()
        for _, address in addresses
        if address
    ]

# Canonical case ID is CG-<12 hex> (created by IncidentHandler).
# Legacy CASE-<alnum> is still accepted for backward compatibility.
CASE_PATTERN = re.compile(
    r"\b(?:CG-[A-F0-9]{12}|CASE-[A-Za-z0-9]+)\b",
    re.IGNORECASE
)

PLAN_PATTERN = re.compile(
    r"\bPLAN-[A-Za-z0-9]+\b",
    re.IGNORECASE
)

APPROVE_PATTERN = re.compile(
    r"^\s*APPROVE\s+(PLAN-[A-Za-z0-9]+)(?:\s+(\S+))?\s*$",
    re.IGNORECASE
)

REJECT_PATTERN = re.compile(
    r"^\s*REJECT\s+(PLAN-[A-Za-z0-9]+)\s*$",
    re.IGNORECASE
)

DETAILS_PATTERN = re.compile(
    r"^\s*DETAILS\s+(CASE-[A-Za-z0-9]+)\s*$",
    re.IGNORECASE
)


def find_identifier(pattern, *values):
    for value in values:
        if not value:
            continue

        match = pattern.search(value)

        if match:
            return match.group(0).upper()

    return None


def classify_message(subject, body):
    subject = subject or ""
    body = (body or "").strip()

    case_id = find_identifier(
        CASE_PATTERN,
        subject,
        body
    )

    plan_id = find_identifier(
        PLAN_PATTERN,
        subject,
        body
    )

    non_empty_lines = [
    line.strip()
    for line in body.splitlines()
    if line.strip()
]

    command_line = non_empty_lines[0] if non_empty_lines else ""

    normalized_body = " ".join(command_line.split())
    normalized_upper = normalized_body.upper()

    # DETAILS CASE-xxxx
    if (
        case_id
        and normalized_upper == f"DETAILS {case_id}"
    ):
        return {
            "intent": "CASE_DETAILS",
            "case_id": case_id,
            "plan_id": plan_id,
            "approval_token_present": False
        }

    tokens = normalized_body.split()
    command = tokens[0].upper() if tokens else ""

    # APPROVE PLAN-xxxx <token>
    if command == "APPROVE":
        if len(tokens) >= 2:
            submitted_plan_id = find_identifier(
                PLAN_PATTERN,
                tokens[1]
            )

            if submitted_plan_id:
                return {
                    "intent": "APPROVAL",
                    "case_id": case_id,
                    "plan_id": submitted_plan_id,
                    "approval_token_present": len(tokens) >= 3
                }

    # REJECT PLAN-xxxx
    if command == "REJECT":
        if len(tokens) == 2:
            submitted_plan_id = find_identifier(
                PLAN_PATTERN,
                tokens[1]
            )

            if submitted_plan_id:
                return {
                    "intent": "REJECTION",
                    "case_id": case_id,
                    "plan_id": submitted_plan_id,
                    "approval_token_present": False
                }

    if case_id:
        return {
            "intent": "CASE_FOLLOWUP",
            "case_id": case_id,
            "plan_id": plan_id,
            "approval_token_present": False
        }

    return {
        "intent": "NEW_OR_AMBIGUOUS_QUERY",
        "case_id": None,
        "plan_id": plan_id,
        "approval_token_present": False
    }
def redact_sensitive_body(body, classification):
    if not body:
        return ""

    if classification.get("intent") == "APPROVAL":
        plan_id = classification.get("plan_id")

        if plan_id:
            return f"APPROVE {plan_id} [REDACTED]"

        return "APPROVE [REDACTED]"

    return body

def process_s3_record(record):
    bucket = record["s3"]["bucket"]["name"]

    encoded_key = record["s3"]["object"]["key"]
    key = urllib.parse.unquote_plus(encoded_key)

    response = s3.get_object(
        Bucket=bucket,
        Key=key
    )

    raw_message = response["Body"].read()

    message = BytesParser(
        policy=policy.default
    ).parsebytes(raw_message)

    body, body_content_type = extract_text_body(message)

    latest_reply = extract_latest_reply(body)

    classification = classify_message(
    message.get("Subject", ""),
    latest_reply
)
    safe_body = redact_sensitive_body(
    latest_reply,
    classification
)

    result = {
        "parser_version": PARSER_VERSION,
        "message_id": message.get("Message-ID"),
        "from": parse_addresses(
            message.get_all("From", [])
        ),
        "to": parse_addresses(
            message.get_all("To", [])
        ),
        "subject": message.get("Subject", ""),
        "in_reply_to": message.get("In-Reply-To"),
        "references": message.get_all("References", []),
        "date": message.get("Date"),
        "body_content_type": body_content_type,
        "body_text": safe_body,
        "s3_bucket": bucket,
        "s3_key": key,
        "classification": classification,
    }

    return result


def lambda_handler(event, context):

    logger.info(
        "Received S3 event containing %s record(s)",
        len(event.get("Records", []))
    )

    messages = []

    for record in event.get("Records", []):

        if record.get("eventSource") != "aws:s3":
            logger.warning(
                "Ignoring non-S3 event record"
            )
            continue

        parsed_message = process_s3_record(record)

        # Do not log the complete email body.
        logger.info(
            "Parsed inbound email: message_id=%s from=%s to=%s subject=%s bucket=%s key=%s",
            parsed_message.get("message_id"),
            parsed_message.get("from"),
            parsed_message.get("to"),
            parsed_message.get("subject"),
            parsed_message.get("s3_bucket"),
            parsed_message.get("s3_key")
        )

        messages.append(parsed_message)

    return {
        "statusCode": 200,
        "processed": len(messages),
        "messages": messages
    }
