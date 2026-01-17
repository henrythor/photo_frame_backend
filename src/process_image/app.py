"""
Process Image Lambda Handler

Triggered by S3 ObjectCreated events via EventBridge.
1. Downloads image from input/
2. Computes content hash for deduplication
3. Checks DynamoDB for existing hash
4. If new: extract EXIF, smart crop, enhance, dither, save
5. Writes metadata to DynamoDB
6. Moves original to originals/ prefix
7. Deletes input file
"""

import os
import json
import logging
import hashlib
import uuid
import random
from datetime import datetime, UTC
from io import BytesIO

import boto3
from PIL import Image, ImageOps
from PIL.ExifTags import TAGS, GPSTAGS

import pillow_heif

try:
    from .image_processor import smart_crop, dither_image
    from .db import check_duplicate, save_image_metadata
except ImportError:
    from image_processor import smart_crop, dither_image
    from db import check_duplicate, save_image_metadata

pillow_heif.register_heif_opener()

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

s3 = boto3.client("s3")
rekognition = boto3.client("rekognition")

BUCKET_NAME = os.environ["BUCKET_NAME"]
TABLE_NAME = os.environ["TABLE_NAME"]

SUPPORTED_FORMATS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp"}
MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5MB Rekognition bytes limit


def handler(event, context):
    """Main Lambda handler."""
    logger.info("Event: %s", json.dumps(event))

    detail = event.get("detail", {})
    bucket = detail.get("bucket", {}).get("name")
    key = detail.get("object", {}).get("key")

    if not bucket or not key:
        logger.error("Missing bucket or key in event")
        return {"statusCode": 400, "body": "Invalid event"}

    if not key.startswith("input/"):
        logger.info("Ignoring non-input key: %s", key)
        return {"statusCode": 200, "body": "Ignored"}

    ext = os.path.splitext(key)[1].lower()
    if ext not in SUPPORTED_FORMATS:
        logger.warning("Unsupported format %s, deleting: %s", ext, key)
        s3.delete_object(Bucket=bucket, Key=key)
        return {"statusCode": 400, "body": f"Unsupported format: {ext}"}

    process_image(bucket, key)
    return {"statusCode": 200, "body": "Processed"}


def process_image(bucket: str, key: str) -> None:
    """Process a single image."""
    logger.info("Downloading s3://%s/%s", bucket, key)
    response = s3.get_object(Bucket=bucket, Key=key)
    image_bytes = response["Body"].read()

    if len(image_bytes) > MAX_IMAGE_SIZE:
        logger.warning("Image too large (%s bytes), skipping", len(image_bytes))
        s3.delete_object(Bucket=bucket, Key=key)
        return

    content_hash = hashlib.sha256(image_bytes).hexdigest()
    logger.info("Content hash: %s", content_hash)

    if check_duplicate(TABLE_NAME, content_hash):
        logger.info("Duplicate detected, skipping: %s", content_hash)
        s3.delete_object(Bucket=bucket, Key=key)
        return

    try:
        img: Image.Image = Image.open(BytesIO(image_bytes))
    except Exception as exc:
        logger.error("Failed to open image: %s", exc)
        s3.delete_object(Bucket=bucket, Key=key)
        return

    exif_data = extract_exif(img)
    logger.info("EXIF data: %s", exif_data)

    img = ImageOps.exif_transpose(img)

    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    img_cropped = smart_crop(img, image_bytes, rekognition)
    img_dithered = dither_image(img_cropped)

    image_id = str(uuid.uuid4())
    timestamp = datetime.now(UTC)
    date_prefix = timestamp.strftime("%Y/%m/%d")

    input_ext = os.path.splitext(key)[1].lower()
    is_heic = input_ext in (".heic", ".heif")

    original_ext = ".jpg" if is_heic else input_ext
    original_key = f"originals/{date_prefix}/{image_id}{original_ext}"
    dithered_key = f"dithered/{image_id}.png"

    dithered_buffer = BytesIO()
    img_dithered.save(dithered_buffer, format="PNG", optimize=True)
    dithered_buffer.seek(0)

    s3.put_object(
        Bucket=bucket,
        Key=dithered_key,
        Body=dithered_buffer.getvalue(),
        ContentType="image/png",
    )
    logger.info("Saved dithered image: %s", dithered_key)

    if is_heic:
        original_buffer = BytesIO()
        original_img: Image.Image = Image.open(BytesIO(image_bytes))
        original_img = ImageOps.exif_transpose(original_img)
        if original_img.mode != "RGB":
            original_img = original_img.convert("RGB")
        original_img.save(original_buffer, format="JPEG", quality=95)
        original_buffer.seek(0)
        s3.put_object(
            Bucket=bucket,
            Key=original_key,
            Body=original_buffer.getvalue(),
            ContentType="image/jpeg",
        )
    else:
        s3.copy_object(
            Bucket=bucket, Key=original_key, CopySource={"Bucket": bucket, "Key": key}
        )
    logger.info("Saved original to: %s", original_key)

    metadata = {
        "image_id": image_id,
        "content_hash": content_hash,
        "original_s3_key": original_key,
        "dithered_s3_key": dithered_key,
        "processing_version": "v1",
        "created_at": timestamp.isoformat(),
        "taken_at": exif_data.get("taken_at"),
        "location": exif_data.get("location"),
        "source": "manual",
        "last_shown_at": None,
        "show_count": 0,
        "random_sort": random.random(),
        "pk": "IMAGE",
    }
    save_image_metadata(TABLE_NAME, metadata)
    logger.info("Saved metadata for: %s", image_id)

    s3.delete_object(Bucket=bucket, Key=key)
    logger.info("Deleted input file: %s", key)


def extract_exif(img: Image.Image) -> dict:
    """Extract relevant EXIF data from image."""
    result: dict = {
        "taken_at": None,
        "location": None,
    }

    try:
        exif = img.getexif()
        if not exif:
            return result

        exif_dict = {TAGS.get(k, k): v for k, v in exif.items()}

        date_taken = exif_dict.get("DateTimeOriginal") or exif_dict.get("DateTime")
        if date_taken:
            try:
                dt = datetime.strptime(date_taken, "%Y:%m:%d %H:%M:%S")
                result["taken_at"] = dt.isoformat()
            except ValueError:
                pass

        gps_info = exif_dict.get("GPSInfo")
        if gps_info:
            gps_dict = {GPSTAGS.get(k, k): v for k, v in gps_info.items()}
            lat = _convert_gps_coord(
                gps_dict.get("GPSLatitude"), gps_dict.get("GPSLatitudeRef")
            )
            lng = _convert_gps_coord(
                gps_dict.get("GPSLongitude"), gps_dict.get("GPSLongitudeRef")
            )
            if lat and lng:
                result["location"] = {"lat": lat, "lng": lng}

    except Exception as exc:
        logger.warning("Error extracting EXIF: %s", exc)

    return result


def _convert_gps_coord(coord, ref):
    """Convert GPS coordinate from EXIF format to decimal degrees."""
    if not coord or not ref:
        return None
    try:
        degrees = float(coord[0])
        minutes = float(coord[1])
        seconds = float(coord[2])
        decimal = degrees + minutes / 60 + seconds / 3600
        if ref in ["S", "W"]:
            decimal = -decimal
        return decimal
    except (TypeError, IndexError, ValueError):
        return None
