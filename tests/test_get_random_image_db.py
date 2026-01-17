import os
from decimal import Decimal

import boto3
from moto import mock_aws

from get_random_image import db as random_db


def _create_table(table_name: str) -> None:
    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    dynamodb.create_table(
        TableName=table_name,
        BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[
            {"AttributeName": "image_id", "AttributeType": "S"},
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "random_sort", "AttributeType": "N"},
        ],
        KeySchema=[{"AttributeName": "image_id", "KeyType": "HASH"}],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "RandomSortIndex",
                "KeySchema": [
                    {"AttributeName": "pk", "KeyType": "HASH"},
                    {"AttributeName": "random_sort", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
    )


@mock_aws
def test_fetch_random_candidates_wraps():
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
    table_name = "photo-frame-images-test"
    _create_table(table_name)

    table = boto3.resource("dynamodb", region_name="us-east-1").Table(table_name)

    items = [
        {"image_id": "a", "pk": "IMAGE", "random_sort": Decimal("0.1")},
        {"image_id": "b", "pk": "IMAGE", "random_sort": Decimal("0.2")},
        {"image_id": "c", "pk": "IMAGE", "random_sort": Decimal("0.9")},
    ]

    for item in items:
        table.put_item(Item=item)

    candidates = random_db.fetch_random_candidates(table_name, limit=5)
    assert len(candidates) == 3
    ids = {item["image_id"] for item in candidates}
    assert ids == {"a", "b", "c"}
