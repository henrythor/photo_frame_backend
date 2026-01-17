import os

import boto3
from moto import mock_aws

from process_image import db as process_db


def _create_table(table_name: str) -> None:
    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    dynamodb.create_table(
        TableName=table_name,
        BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[
            {"AttributeName": "image_id", "AttributeType": "S"},
            {"AttributeName": "content_hash", "AttributeType": "S"},
        ],
        KeySchema=[{"AttributeName": "image_id", "KeyType": "HASH"}],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "ContentHashIndex",
                "KeySchema": [{"AttributeName": "content_hash", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "KEYS_ONLY"},
            }
        ],
    )


@mock_aws
def test_check_duplicate_and_save_metadata():
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
    table_name = "photo-frame-images-test"
    _create_table(table_name)

    metadata = {
        "image_id": "abc",
        "content_hash": "hash123",
        "random_sort": 0.42,
    }

    process_db.save_image_metadata(table_name, metadata)

    assert process_db.check_duplicate(table_name, "hash123") is True
    assert process_db.check_duplicate(table_name, "missing") is False
