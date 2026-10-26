import os
import sys
import pathlib

for k, v in {"AWS_DEFAULT_REGION": "us-east-1", "AWS_ACCESS_KEY_ID": "test", "AWS_SECRET_ACCESS_KEY": "test"}.items():
    os.environ[k] = v
os.environ.pop("AWS_PROFILE", None)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

import boto3  # noqa: E402
import pytest  # noqa: E402
from moto import mock_aws  # noqa: E402

CASE = "CG-52650C02BEB8"


@pytest.fixture
def repos():
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        table = ddb.create_table(
            TableName="CloudOpsGuardian-CaseState",
            KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "PK", "AttributeType": "S"}, {"AttributeName": "SK", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        # Same item shape the deployed IncidentHandler writes.
        table.put_item(Item={
            "PK": f"CASE#{CASE}", "SK": "METADATA", "caseId": CASE, "status": "OPEN",
            "source": "CloudWatch", "alarmName": "CloudOpsGuardian-EC2CPUAlarm",
            "alarmState": "ALARM", "sourceEventId": "evt", "createdAt": "2026-10-22T00:00:00+00:00",
        })
        from cloudops_orchestrator.repositories import Repositories
        yield Repositories(table)
