"""Read-only helper: SigV4-signed MCP JSON-RPC call to an AgentCore Gateway (tools/list by default)."""
import json, os, sys, urllib.request
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.session import Session

url, method = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "tools/list")
body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": {}})
creds = Session().get_credentials()
req = AWSRequest("POST", url, data=body, headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"})
SigV4Auth(creds, "bedrock-agentcore", "us-east-1").add_auth(req)
r = urllib.request.Request(url, data=body.encode(), headers=dict(req.headers), method="POST")
try:
    print(urllib.request.urlopen(r, timeout=30).read().decode())
except urllib.error.HTTPError as e:
    print("HTTP", e.code, e.read().decode()[:800]); sys.exit(1)
