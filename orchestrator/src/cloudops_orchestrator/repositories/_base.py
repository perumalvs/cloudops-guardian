"""Shared DynamoDB helpers. Single-table layout: PK=CASE#<id>, SK=<TYPE>#<key>."""
import uuid
from datetime import datetime, timezone

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

NOT_EXISTS = "attribute_not_exists(PK) AND attribute_not_exists(SK)"


def now():
    return datetime.now(timezone.utc).isoformat()


def case_pk(case_id):
    return f"CASE#{case_id}"


def sortable_id():
    """Time-ordered unique suffix for SK values."""
    return f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')}-{uuid.uuid4().hex[:6]}"


def _failed(error):
    return error.response["Error"]["Code"] == "ConditionalCheckFailedException"


class Store:
    def __init__(self, table):
        self.table = table

    def put_new(self, item):
        """Create an item only if absent. True if created, False if it already existed."""
        try:
            self.table.put_item(Item=item, ConditionExpression=NOT_EXISTS)
            return True
        except ClientError as error:
            if _failed(error):
                return False
            raise

    def get(self, pk, sk):
        return self.table.get_item(Key={"PK": pk, "SK": sk}, ConsistentRead=True).get("Item")

    def query_prefix(self, pk, prefix, reverse=False):
        items, kwargs = [], dict(
            KeyConditionExpression=Key("PK").eq(pk) & Key("SK").begins_with(prefix),
            ConsistentRead=True,
            ScanIndexForward=not reverse,
        )
        while True:
            page = self.table.query(**kwargs)
            items.extend(page.get("Items", []))
            if "LastEvaluatedKey" not in page:
                return items
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    def update_if(self, pk, sk, sets, condition, cond_names=None, cond_values=None, adds=None):
        """
        Conditional update. sets: {attr: value}. Returns True if applied,
        False if the condition did not hold. Item must already exist
        (the condition always references it).
        """
        names, values, parts = dict(cond_names or {}), dict(cond_values or {}), []
        for i, (attr, value) in enumerate(sets.items()):
            names[f"#s{i}"] = attr
            values[f":s{i}"] = value
            parts.append(f"#s{i} = :s{i}")
        expr = "SET " + ", ".join(parts)
        if adds:
            add_parts = []
            for i, (attr, value) in enumerate(adds.items()):
                names[f"#a{i}"] = attr
                values[f":a{i}"] = value
                add_parts.append(f"#a{i} :a{i}")
            expr += " ADD " + ", ".join(add_parts)
        try:
            self.table.update_item(
                Key={"PK": pk, "SK": sk},
                UpdateExpression=expr,
                ConditionExpression=f"attribute_exists(PK) AND ({condition})",
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
            )
            return True
        except ClientError as error:
            if _failed(error):
                return False
            raise
