"""
Get Random Image Lambda Handler

Returns a random image from the dithered collection.
Called by SenseCraft HMI on reTerminal.
"""

import os
import json
import random
import logging
from datetime import datetime, UTC

import boto3

try:
    from .db import fetch_random_candidates, update_shown
except ImportError:
    from db import fetch_random_candidates, update_shown

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")

BUCKET_NAME = os.environ["BUCKET_NAME"]
TABLE_NAME = os.environ["TABLE_NAME"]


def handler(event, context):
    """Return a random dithered image."""
    params = event.get("queryStringParameters") or {}
    include_metadata = params.get("metadata", "false").lower() == "true"

    image = select_random_image()
    if not image:
        return {"statusCode": 404, "body": json.dumps({"error": "No images available"})}

    presigned_url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": BUCKET_NAME, "Key": image["dithered_s3_key"]},
        ExpiresIn=3600,
    )

    update_shown(TABLE_NAME, image["image_id"])

    response_body = {"image_url": presigned_url, "image_id": image["image_id"]}

    if include_metadata:
        response_body["metadata"] = {
            "taken_at": image.get("taken_at"),
            "location": image.get("location"),
            "show_count": int(image.get("show_count", 0)) + 1,
        }

    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(response_body),
    }


def select_random_image() -> dict | None:
    """Select a random image with weighted probability."""
    candidates = fetch_random_candidates(TABLE_NAME, limit=20)

    if not candidates:
        return None

    weighted_candidates = []
    now = datetime.now(UTC)

    for item in candidates:
        weight = 1.0
        last_shown = item.get("last_shown_at")
        if last_shown:
            try:
                last_shown_dt = datetime.fromisoformat(last_shown)
                if last_shown_dt.tzinfo is None:
                    last_shown_dt = last_shown_dt.replace(tzinfo=UTC)
                hours_ago = (now - last_shown_dt).total_seconds() / 3600

                if hours_ago < 24:
                    weight *= 0.1
                elif hours_ago < 72:
                    weight *= 0.5
            except ValueError:
                pass

        show_count = int(item.get("show_count", 0))
        if show_count == 0:
            weight *= 1.5

        weighted_candidates.append((item, weight))

    total_weight = sum(w for _, w in weighted_candidates)
    r = random.random() * total_weight

    cumulative = 0.0
    for item, weight in weighted_candidates:
        cumulative += weight
        if r <= cumulative:
            return item

    return weighted_candidates[-1][0]
