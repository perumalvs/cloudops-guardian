"""Single Lambda entrypoint: case events, inbound-mail envelopes, and verification/probe invocations."""
from . import alarm_handler, inbound_handler, verification_handler


def lambda_handler(event, context):
    if isinstance(event, dict) and ("verify" in event or "probe" in event or "abort" in event):
        return verification_handler.lambda_handler(event, context)
    if isinstance(event, dict) and "responsePayload" in event:
        return inbound_handler.lambda_handler(event, context)
    if isinstance(event, dict) and "caseId" in event:
        return alarm_handler.lambda_handler(event, context)
    raise ValueError("unrecognized event shape")
