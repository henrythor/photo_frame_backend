"""
DynamoDB operations for process_image Lambda.
"""

import logging
from decimal import Decimal

import boto3

logger = logging.getLogger()
dynamodb = boto3.resource("dynamodb")


def check_duplicate(table_name: str, content_hash: str) -> bool:
    """Check if an image with this content hash already exists."""
    table = dynamodb.Table(table_name)

    response = table.query(
        IndexName="ContentHashIndex",
        KeyConditionExpression="content_hash = :hash",
        ExpressionAttributeValues={":hash": content_hash},
        Limit=1,
    )

    return len(response.get("Items", [])) > 0


def save_image_metadata(table_name: str, metadata: dict) -> None:
    """Save image metadata to DynamoDB."""
    table = dynamodb.Table(table_name)

    item = {}
    for key, value in metadata.items():
        if isinstance(value, float):
            item[key] = Decimal(str(value))
        elif value is not None:
            item[key] = value

    table.put_item(Item=item)
    logger.info("Saved item: %s", metadata["image_id"])
