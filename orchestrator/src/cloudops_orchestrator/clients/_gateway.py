"""SigV4-signed MCP (JSON-RPC over HTTPS) client for an AgentCore Gateway."""
import json
import time
import urllib.error
import urllib.request

from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.session import Session

MCP_PROTOCOL_VERSION = "2025-11-25"
SERVICE = "bedrock-agentcore"


class GatewayError(Exception):
    """Gateway answered with an error."""


class GatewayDenied(GatewayError):
    """Authorization or policy refused the call (IAM, Cedar)."""


class ToolError(GatewayError):
    """The tool ran and reported an error."""


class GatewayTransportError(GatewayError):
    """No usable response (timeout, connection, 5xx). Outcome of a write is UNKNOWN."""


def _default_transport(url, body, headers, timeout):
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise GatewayTransportError(type(error).__name__) from error


def _parse_body(raw, headers):
    text = raw.decode("utf-8", errors="replace")
    if "text/event-stream" in {v.lower().split(";")[0] for k, v in headers.items() if k.lower() == "content-type"}:
        for line in text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        raise GatewayError("empty event stream")
    return json.loads(text)


class GatewayClient:
    def __init__(self, url, region="us-east-1", credentials=None, transport=None,
                 timeout=60, retries=0, sleep=time.sleep):
        self.url, self.region, self.timeout, self.retries = url, region, timeout, retries
        self._credentials = credentials
        self._transport = transport or _default_transport
        self._sleep = sleep
        self._id = 0

    def _creds(self):
        return self._credentials or Session().get_credentials()

    def _post(self, payload):
        body = json.dumps(payload)
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                   "MCP-Protocol-Version": MCP_PROTOCOL_VERSION}
        request = AWSRequest("POST", self.url, data=body, headers=headers)
        SigV4Auth(self._creds(), SERVICE, self.region).add_auth(request)
        return self._transport(self.url, body.encode(), dict(request.headers), self.timeout)

    def rpc(self, method, params=None):
        self._id += 1
        payload = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}}
        for attempt in range(self.retries + 1):
            try:
                status, headers, raw = self._post(payload)
            except GatewayTransportError:
                if attempt == self.retries:
                    raise
                self._sleep(0.5 * 2 ** attempt)
                continue
            if status in (429, 500, 502, 503, 504):
                if attempt == self.retries:
                    raise GatewayTransportError(f"HTTP {status}")
                self._sleep(0.5 * 2 ** attempt)
                continue
            break
        try:
            data = _parse_body(raw, headers)
        except (ValueError, GatewayError):
            if status == 403:
                raise GatewayDenied("HTTP 403")
            raise GatewayError(f"unparseable response (HTTP {status})")
        if "error" in data:
            error = data["error"] or {}
            message = str(error.get("message", ""))[:300]
            if status == 403 or error.get("code") == -32002 or "policy" in message.lower():
                raise GatewayDenied(message or "denied")
            raise GatewayError(f"{error.get('code')}: {message}")
        return data.get("result", {})

    def list_tools(self):
        return self.rpc("tools/list").get("tools", [])

    def call_tool(self, name, arguments):
        """Returns the tool's text. Raises ToolError if the tool reported an error."""
        result = self.rpc("tools/call", {"name": name, "arguments": arguments})
        text = "\n".join(c.get("text", "") for c in result.get("content", []) if c.get("type", "text") == "text")
        if result.get("isError"):
            raise ToolError(text[:300])
        return text
