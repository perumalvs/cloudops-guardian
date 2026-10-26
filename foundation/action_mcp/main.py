import json
import time

import boto3
from mcp.server.fastmcp import FastMCP


AWS_REGION = "us-east-1"
INSTANCE_ID = "__DEMO_INSTANCE_ID__"
TERMINATE_PROCESS_DOCUMENT = "CloudOpsGuardian-TerminateProcess"


ssm = boto3.client("ssm", region_name=AWS_REGION)
ec2 = boto3.client("ec2", region_name=AWS_REGION)


mcp = FastMCP(
    name="CloudOpsGuardianActionTools",
    host="0.0.0.0",
    stateless_http=True
)


def json_response(data):
    """Return an MCP-safe JSON string."""
    return json.dumps(data, default=str, indent=2)


@mcp.tool()
def terminate_process(
    process_id: int,
    expected_process_name: str,
    plan_id: str
) -> str:
    """
    Terminate an evidence-identified process on the allowlisted
    CloudOps Guardian demo instance.

    The governed SSM document revalidates the process identity
    immediately before termination.

    plan_id is correlation metadata. Approval validation occurs
    in the orchestration layer before this ACTION tool is invoked.
    """

    if process_id <= 1:
        return json_response({
            "status": "REFUSED",
            "action": "terminate_process",
            "planId": plan_id,
            "reason": "INVALID_PROCESS_ID",
            "verificationRequired": False
        })

    if not expected_process_name:
        return json_response({
            "status": "REFUSED",
            "action": "terminate_process",
            "planId": plan_id,
            "reason": "EXPECTED_PROCESS_NAME_REQUIRED",
            "verificationRequired": False
        })

    response = ssm.send_command(
        InstanceIds=[INSTANCE_ID],
        DocumentName=TERMINATE_PROCESS_DOCUMENT,
        DocumentVersion="$DEFAULT",
        Parameters={
            "ProcessId": [str(process_id)],
            "ExpectedProcessName": [expected_process_name]
        },
        Comment=f"CloudOps Guardian governed action {plan_id}",
        TimeoutSeconds=30
    )

    command_id = response["Command"]["CommandId"]

    for _ in range(15):
        try:
            result = ssm.get_command_invocation(
                CommandId=command_id,
                InstanceId=INSTANCE_ID,
                PluginName="terminateApprovedProcess"
            )

        except ssm.exceptions.InvocationDoesNotExist:
            time.sleep(1)
            continue

        status = result.get("Status")

        if status == "Success":
            return json_response({
                "status": "SUCCESS",
                "action": "terminate_process",
                "instanceId": INSTANCE_ID,
                "planId": plan_id,
                "verificationRequired": True,
                "details": {
                    "processId": process_id,
                    "expectedProcessName": expected_process_name,
                    "commandId": command_id,
                    "ssmStatus": status,
                    "output": result.get(
                        "StandardOutputContent",
                        ""
                    ).strip()
                }
            })

        if status in [
            "Cancelled",
            "TimedOut",
            "Failed",
            "Cancelling"
        ]:
            return json_response({
                "status": (
                    "REFUSED"
                    if status == "Failed"
                    else "FAILED"
                ),
                "action": "terminate_process",
                "instanceId": INSTANCE_ID,
                "planId": plan_id,
                "verificationRequired": True,
                "details": {
                    "processId": process_id,
                    "expectedProcessName": expected_process_name,
                    "commandId": command_id,
                    "ssmStatus": status,
                    "output": result.get(
                        "StandardOutputContent",
                        ""
                    ).strip(),
                    "error": result.get(
                        "StandardErrorContent",
                        ""
                    ).strip()
                }
            })

        time.sleep(1)

    return json_response({
        "status": "TIMEOUT",
        "action": "terminate_process",
        "instanceId": INSTANCE_ID,
        "planId": plan_id,
        "commandId": command_id,
        "verificationRequired": True
    })


@mcp.tool()
def reboot_demo_instance(plan_id: str) -> str:
    """
    Request a reboot of the allowlisted CloudOps Guardian demo instance.

    plan_id is correlation metadata. Approval validation occurs
    in the orchestration layer before this ACTION tool is invoked.

    A successful API call means the reboot action was accepted.
    It does not mean the incident is resolved.
    """

    if not plan_id or not plan_id.strip():
        return json_response({
            "status": "REFUSED",
            "action": "reboot_demo_instance",
            "instanceId": INSTANCE_ID,
            "reason": "PLAN_ID_REQUIRED",
            "verificationRequired": False
        })

    ec2.reboot_instances(
        InstanceIds=[INSTANCE_ID]
    )

    return json_response({
        "status": "ACTION_ACCEPTED",
        "action": "reboot_demo_instance",
        "instanceId": INSTANCE_ID,
        "planId": plan_id,
        "verificationRequired": True
    })
    
if __name__ == "__main__":
    mcp.run(transport="streamable-http")