"""
Get Upload URL Lambda Handler

Returns a presigned S3 URL for uploading images.
Called by iOS Shortcut.
"""

import os
import json
import uuid
from datetime import datetime, UTC

import boto3
from botocore.config import Config

AWS_REGION = os.environ.get("AWS_REGION") or os.environ.get(
    "AWS_DEFAULT_REGION", "eu-west-1"
)
S3_CONFIG = Config(signature_version="s3v4", s3={"addressing_style": "virtual"})

s3 = boto3.client(
    "s3",
    region_name=AWS_REGION,
    endpoint_url=f"https://s3.{AWS_REGION}.amazonaws.com",
    config=S3_CONFIG,
)
BUCKET_NAME = os.environ["BUCKET_NAME"]


def handler(event, context):
    """Generate presigned URL for image upload."""
    image_id = str(uuid.uuid4())
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    key = f"input/{timestamp}-{image_id}.jpg"

    presigned_url = s3.generate_presigned_url(
        "put_object",
        Params={
            "Bucket": BUCKET_NAME,
            "Key": key,
        },
        ExpiresIn=300,
    )

    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps({"upload_url": presigned_url, "image_id": image_id}),
    }
