import json
import time
from datetime import datetime, timedelta, timezone

import boto3
from mcp.server.fastmcp import FastMCP


AWS_REGION = "us-east-1"
INSTANCE_ID = "__DEMO_INSTANCE_ID__"
ALARM_NAME = "CloudOpsGuardian-EC2CPUAlarm"

ec2 = boto3.client("ec2", region_name=AWS_REGION)
cloudwatch = boto3.client("cloudwatch", region_name=AWS_REGION)
ssm = boto3.client("ssm", region_name=AWS_REGION)
TOP_CPU_DOCUMENT_NAME = "CloudOpsGuardian-GetTopCPUProcesses"
TOP_CPU_LINE_LIMIT = 11  # header + top 10; the SSM document lists all processes
MAX_PID = 4194304  # Linux pid_max upper bound
SSM_OUTPUT_LIMIT = 24000  # SSM truncates StandardOutputContent beyond this

mcp = FastMCP(
    name="CloudOpsGuardianReadTools",
    host="0.0.0.0",
    stateless_http=True
)


def json_response(data):
    """Return an MCP-safe JSON string for AWS SDK responses."""
    return json.dumps(data, default=str, indent=2)


@mcp.tool()
def describe_demo_instance() -> str:
    """
    Return configuration details for the allowlisted CloudOps Guardian
    P0 EC2 instance. This tool performs read-only EC2 DescribeInstances.
    """
    response = ec2.describe_instances(
        InstanceIds=[INSTANCE_ID]
    )

    instances = [
        instance
        for reservation in response.get("Reservations", [])
        for instance in reservation.get("Instances", [])
    ]

    if not instances:
        return json_response({
            "status": "NOT_FOUND",
            "instanceId": INSTANCE_ID
        })

    instance = instances[0]

    result = {
        "instanceId": instance.get("InstanceId"),
        "instanceType": instance.get("InstanceType"),
        "state": instance.get("State", {}).get("Name"),
        "availabilityZone": (
            instance.get("Placement", {}).get("AvailabilityZone")
        ),
        "privateIpAddress": instance.get("PrivateIpAddress"),
        "publicIpAddress": instance.get("PublicIpAddress"),
        "architecture": instance.get("Architecture"),
        "platformDetails": instance.get("PlatformDetails"),
        "launchTime": instance.get("LaunchTime"),
        "tags": instance.get("Tags", [])
    }

    return json_response(result)


@mcp.tool()
def get_demo_instance_status() -> str:
    """
    Return system and instance status checks for the allowlisted
    CloudOps Guardian P0 EC2 instance. This tool is read-only.
    """
    response = ec2.describe_instance_status(
        InstanceIds=[INSTANCE_ID],
        IncludeAllInstances=True
    )

    statuses = response.get("InstanceStatuses", [])

    if not statuses:
        return json_response({
            "status": "NOT_FOUND",
            "instanceId": INSTANCE_ID
        })

    status = statuses[0]

    result = {
        "instanceId": status.get("InstanceId"),
        "instanceState": status.get("InstanceState", {}).get("Name"),
        "systemStatus": status.get("SystemStatus", {}).get("Status"),
        "instanceStatus": status.get("InstanceStatus", {}).get("Status"),
        "availabilityZone": status.get("AvailabilityZone"),
        "events": status.get("Events", [])
    }

    return json_response(result)


@mcp.tool()
def get_demo_instance_cpu_metrics(lookback_hours: int = 1) -> str:
    """
    Return average and maximum CPUUtilization datapoints for the
    allowlisted CloudOps Guardian P0 EC2 instance.

    lookback_hours must be between 1 and 24.
    This tool performs read-only CloudWatch GetMetricStatistics.
    """
    if lookback_hours < 1 or lookback_hours > 24:
        raise ValueError("lookback_hours must be between 1 and 24")

    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(hours=lookback_hours)

    response = cloudwatch.get_metric_statistics(
        Namespace="AWS/EC2",
        MetricName="CPUUtilization",
        Dimensions=[
            {
                "Name": "InstanceId",
                "Value": INSTANCE_ID
            }
        ],
        StartTime=start_time,
        EndTime=end_time,
        Period=300,
        Statistics=[
            "Average",
            "Maximum"
        ]
    )

    datapoints = sorted(
        response.get("Datapoints", []),
        key=lambda item: item["Timestamp"]
    )

    result = {
        "instanceId": INSTANCE_ID,
        "metric": "CPUUtilization",
        "namespace": "AWS/EC2",
        "lookbackHours": lookback_hours,
        "periodSeconds": 300,
        "datapoints": datapoints
    }

    return json_response(result)


@mcp.tool()
def describe_cpu_alarm() -> str:
    """
    Return the current configuration and state of the allowlisted
    CloudOps Guardian CPU alarm. This tool is read-only.
    """
    response = cloudwatch.describe_alarms(
        AlarmNames=[ALARM_NAME]
    )

    alarms = response.get("MetricAlarms", [])

    if not alarms:
        return json_response({
            "status": "NOT_FOUND",
            "alarmName": ALARM_NAME
        })

    alarm = alarms[0]

    result = {
        "alarmName": alarm.get("AlarmName"),
        "alarmArn": alarm.get("AlarmArn"),
        "stateValue": alarm.get("StateValue"),
        "stateReason": alarm.get("StateReason"),
        "stateUpdatedTimestamp": alarm.get(
            "StateUpdatedTimestamp"
        ),
        "metricName": alarm.get("MetricName"),
        "namespace": alarm.get("Namespace"),
        "threshold": alarm.get("Threshold"),
        "comparisonOperator": alarm.get("ComparisonOperator"),
        "evaluationPeriods": alarm.get("EvaluationPeriods"),
        "datapointsToAlarm": alarm.get("DatapointsToAlarm"),
        "period": alarm.get("Period"),
        "statistic": alarm.get("Statistic"),
        "dimensions": alarm.get("Dimensions", [])
    }

    return json_response(result)


@mcp.tool()
def get_cpu_alarm_history(max_records: int = 10) -> str:
    """
    Return recent state-update history for the allowlisted
    CloudOps Guardian CPU alarm.

    max_records must be between 1 and 20.
    This tool is read-only.
    """
    if max_records < 1 or max_records > 20:
        raise ValueError("max_records must be between 1 and 20")

    response = cloudwatch.describe_alarm_history(
        AlarmName=ALARM_NAME,
        HistoryItemType="StateUpdate",
        MaxRecords=max_records,
        ScanBy="TimestampDescending"
    )

    result = {
        "alarmName": ALARM_NAME,
        "history": response.get("AlarmHistoryItems", [])
    }

    return json_response(result)

def run_process_listing():
    """
    Run the governed SSM diagnostic document and wait for the result.
    Returns (status, command_id, stdout, stderr).
    """
    response = ssm.send_command(
        InstanceIds=[INSTANCE_ID],
        DocumentName=TOP_CPU_DOCUMENT_NAME,
        DocumentVersion="$DEFAULT",
        Comment="CloudOps Guardian governed CPU process diagnostic",
        TimeoutSeconds=30
    )

    command_id = response["Command"]["CommandId"]

    for _ in range(25):
        try:
            result = ssm.get_command_invocation(
                CommandId=command_id,
                InstanceId=INSTANCE_ID,
                PluginName="getTopCPUProcesses"
            )
        except ssm.exceptions.InvocationDoesNotExist:
            time.sleep(1)
            continue

        status = result.get("Status")

        if status == "Success":
            return (
                status,
                command_id,
                result.get("StandardOutputContent", "").strip(),
                ""
            )

        if status in ["Cancelled", "TimedOut", "Failed", "Cancelling"]:
            return (
                status,
                command_id,
                "",
                result.get("StandardErrorContent", "").strip()
            )

        time.sleep(1)

    return "TIMEOUT_WAITING_FOR_RESULT", command_id, "", ""


@mcp.tool()
def get_top_cpu_processes() -> str:
    """
    Return the top CPU-consuming processes from the allowlisted
    CloudOps Guardian P0 EC2 instance.

    This tool invokes only the governed SSM diagnostic document.
    It accepts no instance ID and no shell command from the caller.
    """
    status, command_id, output, error = run_process_listing()

    if status == "Success":
        top = "\n".join(output.splitlines()[:TOP_CPU_LINE_LIMIT])

        return json_response({
            "status": "SUCCESS",
            "instanceId": INSTANCE_ID,
            "diagnostic": "top_cpu_processes",
            "commandId": command_id,
            "output": top
        })

    result = {
        "status": status,
        "instanceId": INSTANCE_ID,
        "commandId": command_id
    }

    if error:
        result["error"] = error

    return json_response(result)


@mcp.tool()
def get_process_details(process_id: int) -> str:
    """
    Report whether one process (PID) exists on the allowlisted
    CloudOps Guardian P0 EC2 instance, with its CPU and memory use.

    Used for deterministic post-remediation verification. NOT_FOUND is
    returned only when the complete process listing was obtained and
    the PID is absent. If the listing may be truncated the status is
    INCONCLUSIVE, never NOT_FOUND.
    This tool invokes only the governed SSM diagnostic document. It
    accepts no instance ID and no shell command from the caller.
    """
    if (
        isinstance(process_id, bool)
        or not isinstance(process_id, int)
        or process_id < 1
        or process_id > MAX_PID
    ):
        raise ValueError(
            f"process_id must be an integer between 1 and {MAX_PID}"
        )

    status, command_id, output, error = run_process_listing()

    base = {
        "instanceId": INSTANCE_ID,
        "processId": process_id,
        "commandId": command_id
    }

    if status != "Success":
        result = {**base, "status": status}

        if error:
            result["error"] = error

        return json_response(result)

    lines = output.splitlines()
    header = lines[0] if lines else ""
    rows = lines[1:]

    # A complete listing has well over 10 processes and is under the
    # SSM output limit; otherwise absence proves nothing.
    complete = len(rows) > TOP_CPU_LINE_LIMIT - 1 and len(output) < SSM_OUTPUT_LIMIT - 500

    for row in rows:
        fields = row.split()

        if fields and fields[0] == str(process_id):
            return json_response({
                **base,
                "status": "FOUND",
                "output": f"{header}\n{row}"
            })

    if not complete:
        return json_response({
            **base,
            "status": "INCONCLUSIVE",
            "reason": "Process listing may be truncated; absence of the PID is not proven."
        })

    return json_response({**base, "status": "NOT_FOUND"})


if __name__ == "__main__":
    mcp.run(transport="streamable-http")