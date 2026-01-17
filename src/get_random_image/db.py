"""
DynamoDB operations for get_random_image Lambda.
"""

import random
from datetime import datetime, UTC
from decimal import Decimal

import boto3

dynamodb = boto3.resource("dynamodb")


def fetch_random_candidates(table_name: str, limit: int = 20) -> list[dict]:
    """Fetch candidate images for random selection."""
    table = dynamodb.Table(table_name)
    random_start = Decimal(str(random.random()))

    response = table.query(
        IndexName="RandomSortIndex",
        KeyConditionExpression="pk = :pk AND random_sort >= :start",
        ExpressionAttributeValues={":pk": "IMAGE", ":start": random_start},
        Limit=limit,
    )

    candidates = response.get("Items", [])

    if len(candidates) < limit:
        wrap_response = table.query(
            IndexName="RandomSortIndex",
            KeyConditionExpression="pk = :pk AND random_sort < :start",
            ExpressionAttributeValues={":pk": "IMAGE", ":start": random_start},
            Limit=limit - len(candidates),
        )
        candidates.extend(wrap_response.get("Items", []))

    return candidates


def update_shown(table_name: str, image_id: str) -> None:
    """Update last_shown_at and increment show_count."""
    table = dynamodb.Table(table_name)

    table.update_item(
        Key={"image_id": image_id},
        UpdateExpression=(
            "SET last_shown_at = :now, "
            "show_count = if_not_exists(show_count, :zero) + :one"
        ),
        ExpressionAttributeValues={
            ":now": datetime.now(UTC).isoformat(),
            ":zero": 0,
            ":one": 1,
        },
    )
