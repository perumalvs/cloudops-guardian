"""MCP client for hackathon-cloudopsguardian-gateway (READ tools only)."""
import json

from ._gateway import GatewayClient, GatewayError

TARGET_PREFIX = "hackathon-cloudopsguardian-aws-read-tools___"
READ_TOOLS = frozenset({
    "describe_demo_instance", "get_demo_instance_status", "get_demo_instance_cpu_metrics",
    "describe_cpu_alarm", "get_cpu_alarm_history", "get_top_cpu_processes", "get_process_details",
})


class ReadGateway:
    """Deterministic READ calls (verification, pre-action checks). Retries are safe: reads only."""

    def __init__(self, url, **kw):
        kw.setdefault("retries", 3)
        self.client = GatewayClient(url, **kw)

    def call(self, tool, **arguments):
        if tool not in READ_TOOLS:
            raise GatewayError(f"not a READ tool: {tool}")
        text = self.client.call_tool(TARGET_PREFIX + tool, arguments)
        try:
            return json.loads(text)
        except ValueError as error:
            raise GatewayError(f"{tool} returned non-JSON") from error

    def describe_instance(self): return self.call("describe_demo_instance")
    def instance_status(self): return self.call("get_demo_instance_status")
    def cpu_metrics(self, lookback_hours=1): return self.call("get_demo_instance_cpu_metrics", lookback_hours=lookback_hours)
    def alarm(self): return self.call("describe_cpu_alarm")
    def alarm_history(self, max_records=10): return self.call("get_cpu_alarm_history", max_records=max_records)
    def top_processes(self): return self.call("get_top_cpu_processes")
    def process_details(self, process_id): return self.call("get_process_details", process_id=int(process_id))
