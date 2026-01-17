import json
import os

import boto3
from moto import mock_aws


@mock_aws
def test_get_upload_url_returns_presigned_url():
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
    os.environ["AWS_REGION"] = "us-east-1"
    bucket_name = "photo-frame-test"
    os.environ["BUCKET_NAME"] = bucket_name

    from get_upload_url import app as upload_app

    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=bucket_name)

    response = upload_app.handler({}, {})

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert "upload_url" in body
    assert "image_id" in body
