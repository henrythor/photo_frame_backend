import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3
from moto import mock_aws


@mock_aws
def test_get_random_image_app_returns_presigned_url(monkeypatch):
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
    os.environ["BUCKET_NAME"] = "photo-frame-test"
    os.environ["TABLE_NAME"] = "photo-frame-images-test"

    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=os.environ["BUCKET_NAME"])

    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    dynamodb.create_table(
        TableName=os.environ["TABLE_NAME"],
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

    table = dynamodb.Table(os.environ["TABLE_NAME"])
    table.put_item(
        Item={
            "image_id": "img-1",
            "pk": "IMAGE",
            "random_sort": Decimal("0.5"),
            "dithered_s3_key": "dithered/img-1.png",
            "last_shown_at": (
                datetime.now(timezone.utc) - timedelta(days=2)
            ).isoformat(),
            "show_count": 0,
        }
    )

    monkeypatch.setattr("random.random", lambda: 0.4)

    from get_random_image import app as random_app

    response = random_app.handler({"queryStringParameters": {"metadata": "true"}}, {})

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert "image_url" in body
    assert body["image_id"] == "img-1"
    assert body["metadata"]["show_count"] == 1
