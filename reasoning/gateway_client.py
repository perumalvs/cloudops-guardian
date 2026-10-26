"""Minimal SigV4 MCP client for the READ gateway (no MCP library; botocore + urllib only)."""
import json
import time
import urllib.error
import urllib.request

from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.session import Session

MCP_PROTOCOL_VERSION = "2025-11-25"


class GatewayError(Exception):
    pass


def _default_transport(url, body, headers, timeout):
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise GatewayError(f"transport: {type(error).__name__}") from error


class ReadGatewayClient:
    def __init__(self, url, region="us-east-1", credentials=None, transport=None, timeout=60, sleep=time.sleep):
        self.url, self.region, self.timeout = url, region, timeout
        self._credentials, self._transport, self._sleep = credentials, transport or _default_transport, sleep
        self._id = 0

    def _rpc(self, method, params):
        self._id += 1
        body = json.dumps({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params})
        headers = {"Content-Type": "application/json", "Accept": "application/json",
                   "MCP-Protocol-Version": MCP_PROTOCOL_VERSION}
        for attempt in range(3):  # READ calls are idempotent
            request = AWSRequest("POST", self.url, data=body, headers=headers)
            SigV4Auth(self._credentials or Session().get_credentials(), "bedrock-agentcore", self.region).add_auth(request)
            try:
                status, raw = self._transport(self.url, body.encode(), dict(request.headers), self.timeout)
            except GatewayError:
                if attempt == 2:
                    raise
                self._sleep(0.5 * 2 ** attempt)
                continue
            if status in (429, 500, 502, 503, 504) and attempt < 2:
                self._sleep(0.5 * 2 ** attempt)
                continue
            break
        try:
            data = json.loads(raw)
        except ValueError as error:
            raise GatewayError(f"unparseable response (HTTP {status})") from error
        if "error" in data:
            raise GatewayError(str((data["error"] or {}).get("message", "gateway error"))[:300])
        return data.get("result", {})

    def list_tools(self):
        return self._rpc("tools/list", {}).get("tools", [])

    def call_tool(self, name, arguments):
        result = self._rpc("tools/call", {"name": name, "arguments": arguments})
        text = "\n".join(c.get("text", "") for c in result.get("content", []) if c.get("type", "text") == "text")
        if result.get("isError"):
            raise GatewayError(text[:300] or "tool error")
        return text
