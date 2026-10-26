"""MCP client for hackathon-cloudopsguardian-action-tools-gateway (terminate_process, reboot_demo_instance)."""
import json

from ..models import parse_plan_id
from ..plan_builder import validate_parameters
from ._gateway import GatewayClient, GatewayError

TARGET_PREFIX = "CloudOpsGuardian-ACTION-Tools___"


class ActionGateway:
    """
    WRITE path. Never retries: a retried reboot would reboot twice. A
    GatewayTransportError means the outcome is UNKNOWN and must be verified.
    Callers pass stored, approved parameters only; they are re-validated here.
    """

    def __init__(self, url, **kw):
        kw["retries"] = 0
        self.client = GatewayClient(url, **kw)

    def _call(self, tool, parameters, plan_id):
        parse_plan_id(plan_id)  # raises ValueError on a malformed id
        validate_parameters(tool, parameters, plan_id)
        text = self.client.call_tool(TARGET_PREFIX + tool, parameters)
        try:
            return json.loads(text)
        except ValueError as error:
            raise GatewayError(f"{tool} returned non-JSON") from error

    def terminate_process(self, process_id, expected_process_name, plan_id):
        return self._call("terminate_process", {
            "process_id": process_id, "expected_process_name": expected_process_name, "plan_id": plan_id}, plan_id)

    def reboot_demo_instance(self, plan_id):
        return self._call("reboot_demo_instance", {"plan_id": plan_id}, plan_id)

    def execute_plan(self, plan):
        """Run exactly the stored capability with its stored parameters."""
        return self._call(plan["capability"], plan["parameters"], plan["planId"])
