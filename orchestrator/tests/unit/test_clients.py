import io
import json

import pytest
from botocore.credentials import Credentials
from cloudops_orchestrator.clients._gateway import (GatewayClient, GatewayDenied, GatewayError,
                                                    GatewayTransportError, ToolError)
from cloudops_orchestrator.clients.action_gateway import ActionGateway
from cloudops_orchestrator.clients.read_gateway import ReadGateway
from cloudops_orchestrator.clients.reasoning_runtime import (ReasoningError, ReasoningRuntime,
                                                             session_id, validate_reasoning_response)
from cloudops_orchestrator.clients.ses_outbound import RecipientNotAllowed, SesOutbound
from cloudops_orchestrator.config import Config

CREDS = Credentials("AKIATESTTESTTESTTEST", "secret")
URL = "https://gw.example.com/mcp"
PLAN = "PLAN-52650C02BEB8V1"


class Transport:
    """Scripted gateway: list of (status, body) responses; records every request."""
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def __call__(self, url, body, headers, timeout):
        self.calls.append((json.loads(body), headers))
        status, payload = self.responses.pop(0)
        if isinstance(payload, Exception):
            raise payload
        return status, {"Content-Type": "application/json"}, json.dumps(payload).encode()


def ok(text, is_error=False):
    return 200, {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": text}], "isError": is_error}}


def read(*r, **kw):
    return ReadGateway(URL, credentials=CREDS, transport=Transport(*r), sleep=lambda s: None, **kw)


def action(*r):
    return ActionGateway(URL, credentials=CREDS, transport=Transport(*r))


def test_requests_are_sigv4_signed_with_protocol_version():
    g = read(ok('{"status":"SUCCESS"}'))
    g.process_details(4242)
    body, headers = g.client._transport.calls[0]
    assert headers["Authorization"].startswith("AWS4-HMAC-SHA256") and "bedrock-agentcore" in headers["Authorization"]
    assert headers["MCP-Protocol-Version"] == "2025-11-25"
    assert body["params"] == {"name": "hackathon-cloudopsguardian-aws-read-tools___get_process_details",
                              "arguments": {"process_id": 4242}}


def test_read_gateway_refuses_non_read_tools():
    g = read()
    with pytest.raises(GatewayError):
        g.call("terminate_process", process_id=1)
    assert g.client._transport.calls == []


def test_read_retries_transient_failures_then_succeeds():
    g = read((503, {}), (429, {}), ok('{"a":1}'))
    assert g.alarm() == {"a": 1} and len(g.client._transport.calls) == 3


def test_read_gives_up_after_retries():
    g = read(*[(503, {})] * 4)
    with pytest.raises(GatewayTransportError):
        g.alarm()


def test_tool_error_and_nonjson():
    with pytest.raises(ToolError):
        read(ok("Error executing tool: process_id must be an integer", True)).process_details(0)
    with pytest.raises(GatewayError):
        read(ok("not json")).alarm()


def test_denials_map_to_gatewaydenied():
    with pytest.raises(GatewayDenied):
        read((403, {"jsonrpc": "2.0", "id": 1, "error": {"code": -32002, "message": "Authorization error"}})).alarm()
    with pytest.raises(GatewayDenied):
        action((200, {"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "Denied by policy enforcement"}})
               ).reboot_demo_instance(PLAN)


def test_action_calls_prefixed_tool_with_exact_args():
    g = action(ok('{"status":"SUCCESS","verificationRequired":true}'))
    assert g.terminate_process(4242, "yes", PLAN)["status"] == "SUCCESS"
    body = g.client._transport.calls[0][0]
    assert body["params"]["name"] == "CloudOpsGuardian-ACTION-Tools___terminate_process"
    assert body["params"]["arguments"] == {"process_id": 4242, "expected_process_name": "yes", "plan_id": PLAN}


def test_action_never_retries_and_marks_unknown_outcome():
    g = action((503, {}), ok('{"status":"ACTION_ACCEPTED"}'))
    with pytest.raises(GatewayTransportError):
        g.reboot_demo_instance(PLAN)
    assert len(g.client._transport.calls) == 1            # a retry could reboot twice


def test_action_timeout_is_transport_error_without_retry():
    g = action((0, GatewayTransportError("TimeoutError")))
    with pytest.raises(GatewayTransportError):
        g.reboot_demo_instance(PLAN)
    assert len(g.client._transport.calls) == 1


@pytest.mark.parametrize("args", [(1, "yes", PLAN), (5, "yes; reboot", PLAN), (True, "yes", PLAN), (5, "yes", "PLAN-bad")])
def test_action_revalidates_arguments_before_sending(args):
    g = action(ok("{}"))
    with pytest.raises(ValueError):
        g.terminate_process(*args)
    assert g.client._transport.calls == []


def test_execute_plan_uses_stored_params_only():
    g = action(ok('{"status":"ACTION_ACCEPTED"}'))
    g.execute_plan({"capability": "reboot_demo_instance", "planId": PLAN, "parameters": {"plan_id": PLAN}})
    assert g.client._transport.calls[0][0]["params"]["arguments"] == {"plan_id": PLAN}
    with pytest.raises(ValueError):
        g.execute_plan({"capability": "run_shell_command", "planId": PLAN, "parameters": {"plan_id": PLAN}})


def test_event_stream_response_parsed():
    def transport(url, body, headers, timeout):
        data = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"tools": [{"name": "x"}]}})
        return 200, {"content-type": "text/event-stream"}, f"event: message\ndata: {data}\n\n".encode()
    assert GatewayClient(URL, credentials=CREDS, transport=transport).list_tools() == [{"name": "x"}]


# ---- reasoning runtime ----

def test_reasoning_response_filters_to_allowed_outcomes():
    out = validate_reasoning_response({
        "diagnosis": {"summary": "yes is hot", "confidence": "high", "process_id": 4242, "process_name": "yes"},
        "outcomes": [
            {"capability": "run_shell_command", "rank": 1, "parameters": {"cmd": "rm -rf /"}},
            {"capability": "terminate_process", "rank": 2, "parameters": {"process_id": 4242}},
            {"capability": "reboot_demo_instance", "rank": 3},
            {"capability": "StopInstances", "rank": 4},
        ]})
    assert [o["capability"] for o in out["outcomes"]] == ["terminate_process", "reboot_demo_instance"]
    assert out["dropped"] == ["run_shell_command", "StopInstances"]
    assert out["diagnosis"]["confidence"] == "HIGH"


def test_reasoning_with_nothing_usable_escalates():
    out = validate_reasoning_response({"outcomes": [{"capability": "delete_everything"}]})
    assert [o["capability"] for o in out["outcomes"]] == ["ESCALATE"] and out["diagnosis"]["confidence"] == "LOW"
    assert validate_reasoning_response({})["outcomes"][0]["capability"] == "ESCALATE"


def test_reasoning_rejects_non_objects_and_bad_confidence():
    with pytest.raises(ReasoningError):
        validate_reasoning_response(["x"])
    assert validate_reasoning_response({"diagnosis": {"confidence": "certain"}})["diagnosis"]["confidence"] == "LOW"


class FakeAgentCore:
    def __init__(self, reply):
        self.reply, self.kw = reply, None

    def invoke_agent_runtime(self, **kw):
        self.kw = kw
        return {"response": io.BytesIO(self.reply)}


def test_runtime_invoke_contract():
    fake = FakeAgentCore(json.dumps({"diagnosis": {"summary": "s"}, "outcomes": [{"capability": "ESCALATE"}]}).encode())
    out = ReasoningRuntime("arn:rt", client=fake).invoke("investigate", "CG-52650C02BEB8", case={"x": 1})
    assert fake.kw["agentRuntimeArn"] == "arn:rt" and fake.kw["qualifier"] == "DEFAULT"
    assert len(fake.kw["runtimeSessionId"]) >= 33 and fake.kw["runtimeSessionId"] == session_id("CG-52650C02BEB8")
    assert json.loads(fake.kw["payload"])["mode"] == "investigate" and out["outcomes"][0]["capability"] == "ESCALATE"
    with pytest.raises(ReasoningError):
        ReasoningRuntime("arn", client=FakeAgentCore(b"<html>")).invoke("query", "CG-52650C02BEB8")
    with pytest.raises(ReasoningError):
        ReasoningRuntime("arn", client=fake).invoke("delete", "CG-52650C02BEB8")


# ---- SES ----

class FakeSes:
    def __init__(self):
        self.sent = []

    def send_raw_email(self, **kw):
        self.sent.append(kw)
        return {"MessageId": "ses-1"}


CFG = Config(operator_emails=("op@x.com",), approver_emails=("boss@x.com",))


def test_ses_sends_threaded_plain_text_to_allowed_only():
    fake = FakeSes()
    ses = SesOutbound(CFG, client=fake)
    assert ses.send(["OP@x.com"], "[CG-1] hi\r\nBcc: evil@x.com", "body", in_reply_to="<m1>") == "ses-1"
    raw = fake.sent[0]["RawMessage"]["Data"].decode()
    assert "In-Reply-To: <m1>" in raw and "From: CloudOps Guardian <__SES_FROM_ADDRESS__>" in raw
    assert "Bcc" not in raw.split("\n\n")[0].replace("Subject: [CG-1] hi Bcc: evil@x.com", "")
    assert fake.sent[0]["Destinations"] == ["op@x.com"]


def test_ses_refuses_unknown_and_empty_recipients():
    ses = SesOutbound(CFG, client=FakeSes())
    for bad in (["stranger@evil.com"], ["op@x.com", "stranger@evil.com"], [], [""]):
        with pytest.raises(RecipientNotAllowed):
            ses.send(bad, "s", "b")
    assert ses.client.sent == []
    ses.send_to_approvers("s", "b")
    assert ses.client.sent[0]["Destinations"] == ["boss@x.com"]
