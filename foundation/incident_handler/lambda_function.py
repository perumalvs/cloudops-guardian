import boto3
import os
from datetime import datetime, timezone
from botocore.exceptions import ClientError

dynamodb = boto3.resource(
    "dynamodb",
    region_name=os.environ.get("AWS_REGION", "us-east-1")
)

table = dynamodb.Table("CloudOpsGuardian-CaseState")

EXPECTED_ALARM = "CloudOpsGuardian-EC2CPUAlarm"


def lambda_handler(event, context):

    # Validate that this is the expected CloudWatch alarm event
    if event.get("source") != "aws.cloudwatch":
        raise ValueError("Unexpected event source")

    if event.get("detail-type") != "CloudWatch Alarm State Change":
        raise ValueError("Unexpected event type")

    detail = event.get("detail", {})

    if detail.get("alarmName") != EXPECTED_ALARM:
        raise ValueError("Unexpected alarm")

    state = detail.get("state", {}).get("value")

    if state != "ALARM":
        raise ValueError("Alarm is not in ALARM state")

    # Generate internal CloudOps Guardian case ID
    event_id = event.get("id")

    if not event_id:
        raise ValueError("Missing EventBridge event ID")

    normalized_event_id = event_id.replace("-", "").upper()
    case_id = f"CG-{normalized_event_id[:12]}"  

    created_at = datetime.now(timezone.utc).isoformat()

    # Preserve EventBridge event ID for correlation/idempotency work
    #event_id = event.get("id", "UNKNOWN")

    item = {
        "PK": f"CASE#{case_id}",
        "SK": "METADATA",
        "caseId": case_id,
        "status": "OPEN",
        "source": "CloudWatch",
        "alarmName": EXPECTED_ALARM,
        "alarmState": state,
        "sourceEventId": event_id,
        "createdAt": created_at
    }

    try:
        table.put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)"
        )

    except ClientError as error:
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return {
                "caseId": case_id,
                "status": "DUPLICATE",
                "sourceEventId": event_id
                }

        raise

    return {
        "caseId": case_id,
        "status": "OPEN",
        "sourceEventId": event_id
    }
