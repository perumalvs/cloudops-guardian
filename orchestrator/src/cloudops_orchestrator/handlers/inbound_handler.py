"""Consumes InboundEmailProcessor normalized output (Lambda async OnSuccess destination) and calls router."""
import logging

from ..mime_reader import read_inbound
from ..router import Router
from ..services import build_services

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_services = None


def fetch_raw(services, msg):
    """Raw MIME from the configured inbound bucket only; returns {"verdicts", "token"}."""
    if not services.cfg.inbound_bucket or msg.get("s3_bucket") != services.cfg.inbound_bucket:
        raise ValueError("message does not come from the configured inbound bucket")
    key = msg.get("s3_key")
    if not key or not isinstance(key, str):
        raise ValueError("message has no s3 key")
    body = services.s3.get_object(Bucket=services.cfg.inbound_bucket, Key=key)["Body"].read()
    return read_inbound(body)


def handle_envelope(event, services):
    """
    event: Lambda destination envelope {requestPayload, responsePayload: {messages: [...]}}.
    One bad message never blocks the others; failures are logged by type only.
    """
    router = Router(services)
    summary = {"messages": 0, "results": [], "errors": 0}
    for msg in (event.get("responsePayload") or {}).get("messages") or []:
        summary["messages"] += 1
        try:
            raw = fetch_raw(services, msg)
            out = router.route(msg, raw)
            raw["token"] = None                                  # plaintext token discarded
            summary["results"].append(out.get("result"))
        except Exception as error:  # noqa: BLE001
            summary["errors"] += 1
            logger.error("inbound message failed: %s", type(error).__name__)
    return summary


def lambda_handler(event, context):
    global _services
    _services = _services or build_services()
    summary = handle_envelope(event, _services)
    logger.info("inbound handled: %s", summary)               # counts and result codes only
    return summary
