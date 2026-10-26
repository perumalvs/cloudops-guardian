"""Read-only helper: call one READ gateway tool. Usage: gateway_tool.py <mcp_url> <tool_suffix> [json_args]"""
import json, sys, urllib.request, urllib.error
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.session import Session

url, tool = sys.argv[1], sys.argv[2]
args = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                   "params": {"name": f"hackathon-cloudopsguardian-aws-read-tools___{tool}", "arguments": args}})
h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}
req = AWSRequest("POST", url, data=body, headers=h)
SigV4Auth(Session().get_credentials(), "bedrock-agentcore", "us-east-1").add_auth(req)
try:
    r = urllib.request.urlopen(urllib.request.Request(url, data=body.encode(), headers=dict(req.headers), method="POST"), timeout=90)
    d = json.loads(r.read())
except urllib.error.HTTPError as e:
    print("HTTP", e.code, e.read().decode()[:600]); sys.exit(1)
res = d.get("result", d)
for c in res.get("content", []):
    print(c.get("text", c))
print("isError:", res.get("isError"))
