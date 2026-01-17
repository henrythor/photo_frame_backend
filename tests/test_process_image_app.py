import io
import os
from decimal import Decimal

import boto3
from moto import mock_aws
from PIL import Image


class RekognitionStub:
    def detect_faces(self, Image=None, Attributes=None):
        return {"FaceDetails": []}


def _create_table(table_name: str) -> None:
    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    dynamodb.create_table(
        TableName=table_name,
        BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[
            {"AttributeName": "image_id", "AttributeType": "S"},
            {"AttributeName": "content_hash", "AttributeType": "S"},
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "random_sort", "AttributeType": "N"},
        ],
        KeySchema=[{"AttributeName": "image_id", "KeyType": "HASH"}],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "ContentHashIndex",
                "KeySchema": [{"AttributeName": "content_hash", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "KEYS_ONLY"},
            },
            {
                "IndexName": "RandomSortIndex",
                "KeySchema": [
                    {"AttributeName": "pk", "KeyType": "HASH"},
                    {"AttributeName": "random_sort", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
        ],
    )


@mock_aws
def test_process_image_app_pipeline(monkeypatch, tmp_path):
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
    os.environ["BUCKET_NAME"] = "photo-frame-test"
    os.environ["TABLE_NAME"] = "photo-frame-images-test"
    os.environ["PALETTE_PATH"] = str(tmp_path / "palette.json")

    (tmp_path / "palette.json").write_text(
        '{"palette": [[0,0,0],[255,255,255],[180,40,30],[0,170,0],[0,70,200],[240,220,0]]}',
        encoding="utf-8",
    )

    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=os.environ["BUCKET_NAME"])

    _create_table(os.environ["TABLE_NAME"])

    img = Image.new("RGB", (1200, 800), color=(120, 80, 60))
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG")
    buffer.seek(0)

    s3.put_object(
        Bucket=os.environ["BUCKET_NAME"],
        Key="input/test-image.jpg",
        Body=buffer.getvalue(),
        ContentType="image/jpeg",
    )

    from process_image import app as process_app

    monkeypatch.setattr(process_app, "rekognition", RekognitionStub())

    event = {
        "detail": {
            "bucket": {"name": os.environ["BUCKET_NAME"]},
            "object": {"key": "input/test-image.jpg"},
        }
    }

    response = process_app.handler(event, {})

    assert response["statusCode"] == 200

    items = (
        boto3.resource("dynamodb", region_name="us-east-1")
        .Table(os.environ["TABLE_NAME"])
        .scan()["Items"]
    )
    assert len(items) == 1
    assert items[0]["pk"] == "IMAGE"
    assert isinstance(items[0]["random_sort"], Decimal)

    objects = s3.list_objects_v2(Bucket=os.environ["BUCKET_NAME"]).get("Contents", [])
    keys = {obj["Key"] for obj in objects}
    assert any(key.startswith("dithered/") for key in keys)
    assert any(key.startswith("originals/") for key in keys)
    assert "input/test-image.jpg" not in keys
