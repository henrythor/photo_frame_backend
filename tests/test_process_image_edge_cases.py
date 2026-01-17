import os

import boto3
from moto import mock_aws


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
def test_process_image_ignores_non_input_prefix():
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
    os.environ["BUCKET_NAME"] = "photo-frame-test"
    os.environ["TABLE_NAME"] = "photo-frame-images-test"

    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=os.environ["BUCKET_NAME"])
    _create_table(os.environ["TABLE_NAME"])

    from process_image import app as process_app

    event = {
        "detail": {
            "bucket": {"name": os.environ["BUCKET_NAME"]},
            "object": {"key": "originals/image.jpg"},
        }
    }

    response = process_app.handler(event, {})
    assert response["statusCode"] == 200


@mock_aws
def test_process_image_rejects_unsupported_extension():
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
    os.environ["BUCKET_NAME"] = "photo-frame-test"
    os.environ["TABLE_NAME"] = "photo-frame-images-test"

    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=os.environ["BUCKET_NAME"])
    _create_table(os.environ["TABLE_NAME"])

    s3.put_object(
        Bucket=os.environ["BUCKET_NAME"],
        Key="input/test.txt",
        Body=b"not-an-image",
    )

    from process_image import app as process_app

    event = {
        "detail": {
            "bucket": {"name": os.environ["BUCKET_NAME"]},
            "object": {"key": "input/test.txt"},
        }
    }

    response = process_app.handler(event, {})
    assert response["statusCode"] == 400

    objects = s3.list_objects_v2(Bucket=os.environ["BUCKET_NAME"]).get("Contents", [])
    assert objects == []


@mock_aws
def test_process_image_skips_oversized_payload(monkeypatch):
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
    os.environ["BUCKET_NAME"] = "photo-frame-test"
    os.environ["TABLE_NAME"] = "photo-frame-images-test"

    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=os.environ["BUCKET_NAME"])
    _create_table(os.environ["TABLE_NAME"])

    from process_image import app as process_app

    monkeypatch.setattr(process_app, "MAX_IMAGE_SIZE", 10)

    s3.put_object(
        Bucket=os.environ["BUCKET_NAME"],
        Key="input/test.jpg",
        Body=b"x" * 20,
    )

    event = {
        "detail": {
            "bucket": {"name": os.environ["BUCKET_NAME"]},
            "object": {"key": "input/test.jpg"},
        }
    }

    response = process_app.handler(event, {})
    assert response["statusCode"] == 200

    objects = s3.list_objects_v2(Bucket=os.environ["BUCKET_NAME"]).get("Contents", [])
    assert objects == []
