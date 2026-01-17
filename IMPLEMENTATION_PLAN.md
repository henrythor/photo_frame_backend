# Photo Frame Backend - Implementation Plan

## Overview

Backend system for the **Seeed reTerminal E1002**, a 7.3-inch full-color ePaper display. Accepts photos from iPhone via Share Sheet, processes them with smart cropping and dithering, and serves random images to the display.

### Target Device

| Specification | Value |
|---------------|-------|
| Model | Seeed reTerminal E1002 |
| Display | 7.3" E Ink Spectra 6, 800x480 resolution |
| Color Palette | Black, White, Red, Green, Blue, Yellow |
| Processor | ESP32-S3 |
| Battery Life | ~3 months |
| Connectivity | WiFi 2.4GHz, USB-C |
| Platform | SenseCraft HMI |

### Display Care Guidelines

- Refresh interval: minimum 1 hour recommended
- Avoid static images exceeding 24 hours (prevents ghosting)
- Daily refresh recommended for optimal display health

## Architecture Summary

```
iPhone (iOS Shortcut)              reTerminal E1002 (SenseCraft HMI)
         |                                      |
         | x-api-key: UPLOAD_KEY                | x-api-key: DISPLAY_KEY
         v                                      v
+----------------------------------------------------------+
|            api.example.com (optional)                    |
|            (Custom Domain + API Gateway)                 |
|              (API Keys Required)                         |
+-----------------------------+----------------------------+
         |                                      |
         v                                      v
+---------------------------+       +---------------------------+
| /v1/photo-frame/upload-url|       | /v1/photo-frame/image     |
| Lambda                    |       | Lambda                    |
+-------------+-------------+       +-------------+-------------+
        |                                       |
        | returns presigned URL                 | returns presigned URL
        v                                       |
+----------------------------------------------------------+
|                        S3 Bucket                         |
|  input/ --> originals/ (lifecycle to Glacier)            |
|                 |                                        |
|                 v                                        |
|            dithered/ <-----------------------------------+
+----------------------------------------------------------+
        |
        | S3 ObjectCreated -> EventBridge
        v
+------------------+          +------------------+
| Process Lambda   |--------->| DynamoDB         |
| - Hash & dedup   |          | PhotoFrameImages |
| - Rekognition    |          +------------------+
| - Smart crop     |
| - Dither         |
+------------------+
```

---

## Tech Stack

- **IaC**: AWS SAM (template.yaml)
- **Runtime**: Python 3.12
- **Image Processing**: Pillow + pillow-heif (for iPhone HEIC support)
- **Face Detection**: AWS Rekognition
- **Storage**: S3 + DynamoDB
- **Custom Domain**: Optional (configure in samconfig.toml)

---

## Project Structure

```
photo_frame_backend/
|-- template.yaml              # SAM template (all infrastructure)
|-- samconfig.toml             # SAM deployment config (gitignored, personal values)
|-- samconfig.toml.example     # Example config (committed, safe to share)
|-- .gitignore                 # Includes samconfig.toml
|-- src/
|   |-- process_image/
|   |   |-- __init__.py
|   |   |-- app.py             # Main handler
|   |   |-- image_processor.py # Crop, resize, dither logic
|   |   |-- db.py              # DynamoDB operations
|   |   |-- requirements.txt
|   |
|   |-- get_upload_url/
|   |   |-- __init__.py
|   |   |-- app.py
|   |   |-- requirements.txt
|   |
|   |-- get_random_image/
|       |-- __init__.py
|       |-- app.py
|       |-- db.py              # DynamoDB operations
|       |-- requirements.txt
|
|-- tests/
|   |-- unit/
|   |   |-- test_image_processor.py
|   |   |-- test_get_random_image.py
|   |-- integration/
|       |-- test_e2e.py
|
|-- events/                    # Sample events for local testing
|   |-- s3_put_event.json
|
|-- README.md
|-- IMPLEMENTATION_PLAN.md     # This file
```

---

## Phase 1: Infrastructure + Process Lambda

### 1.0 Regional Defaults (Project-Wide)

- **Region**: `eu-west-1`



### 1.1 SAM Template Resources

Create `template.yaml` with the following resources.

#### Template Header

```yaml
AWSTemplateFormatVersion: '2010-09-09'
Transform: AWS::Serverless-2016-10-31
Description: Photo Frame Backend - processes and serves images for e-ink display

Globals:
  Function:
    Runtime: python3.12
    Timeout: 30
    MemorySize: 256
    Architectures:
      - arm64  # Graviton - better price/performance
    Environment:
      Variables:
        LOG_LEVEL: INFO
```

#### Parameters

```yaml
Parameters:
  Environment:
    Type: String
    Default: dev
    AllowedValues: [dev, prod]
  
  EnableCustomDomain:
    Type: String
    Default: "false"
    AllowedValues: ["true", "false"]
    Description: |
      Set to "true" to enable custom domain (api.yourdomain.com).
      Set to "false" to use the default API Gateway URL.
      When false, DomainName/HostedZoneName/HostedZoneId are ignored.
  
  DomainName:
    Type: String
    Default: api.example.com
    Description: Custom domain name for the API (e.g., api.yourdomain.com)
  
  HostedZoneName:
    Type: String
    Default: example.com.
    Description: |
      Route 53 hosted zone name (must end with a dot).
      SAM will automatically look up the hosted zone ID for Route53 alias records.
      Example: "yourdomain.com." for api.yourdomain.com
  
  HostedZoneId:
    Type: String
    Default: ""
    Description: |
      Route 53 hosted zone ID (required for ACM certificate DNS validation).
      Find this in Route 53 console or via: aws route53 list-hosted-zones
      Example: Z1234567890ABC
```

#### Conditions

```yaml
Conditions:
  UseCustomDomain: !Equals [!Ref EnableCustomDomain, "true"]
```

**Why both HostedZoneName and HostedZoneId?**
- `HostedZoneName`: Used by SAM's Route53Configuration to create the alias record. 
  SAM can look up the zone ID automatically from the name.
- `HostedZoneId`: Required by ACM's DomainValidationOptions for automatic certificate 
  validation. Unfortunately ACM doesn't support lookup by name.

**Making custom domain optional**: The `EnableCustomDomain` parameter defaults to `false`.
Users without a domain can deploy immediately. Users with domains set it to `true` in 
their `samconfig.toml` along with their domain details.

#### S3 Bucket

```yaml
PhotoBucket:
  Type: AWS::S3::Bucket
  DeletionPolicy: Retain  # Prevent accidental data loss
  UpdateReplacePolicy: Retain
  Properties:
    BucketName: !Sub photo-frame-${Environment}-${AWS::AccountId}
    NotificationConfiguration:
      EventBridgeConfiguration:
        EventBridgeEnabled: true
    LifecycleConfiguration:
      Rules:
        - Id: DeleteInputAfterProcessing
          Status: Enabled
          Prefix: input/
          ExpirationInDays: 1
        - Id: MoveOriginalsToIA
          Status: Enabled
          Prefix: originals/
          Transitions:
            - StorageClass: STANDARD_IA
              TransitionInDays: 30
            - StorageClass: GLACIER_IR
              TransitionInDays: 90
    PublicAccessBlockConfiguration:
      BlockPublicAcls: true
      BlockPublicPolicy: true
      IgnorePublicAcls: true
      RestrictPublicBuckets: true
```

#### DynamoDB Table

```yaml
PhotoTable:
  Type: AWS::DynamoDB::Table
  DeletionPolicy: Retain  # Prevent accidental data loss
  UpdateReplacePolicy: Retain
  Properties:
    TableName: !Sub photo-frame-images-${Environment}
    BillingMode: PAY_PER_REQUEST
    AttributeDefinitions:
      - AttributeName: image_id
        AttributeType: S
      - AttributeName: content_hash
        AttributeType: S
      - AttributeName: pk
        AttributeType: S
      - AttributeName: random_sort
        AttributeType: N
    KeySchema:
      - AttributeName: image_id
        KeyType: HASH
    GlobalSecondaryIndexes:
      - IndexName: ContentHashIndex
        KeySchema:
          - AttributeName: content_hash
            KeyType: HASH
        Projection:
          ProjectionType: KEYS_ONLY
      - IndexName: RandomSortIndex
        KeySchema:
          - AttributeName: pk
            KeyType: HASH
          - AttributeName: random_sort
            KeyType: RANGE
        Projection:
          ProjectionType: ALL
```

#### Process Image Lambda

```yaml
ProcessImageFunction:
  Type: AWS::Serverless::Function
  Properties:
    FunctionName: !Sub photo-frame-process-${Environment}
    CodeUri: src/process_image/
    Handler: app.handler
    Runtime: python3.12
    Timeout: 60
    MemorySize: 1024
    Environment:
      Variables:
        BUCKET_NAME: !Ref PhotoBucket
        TABLE_NAME: !Ref PhotoTable
    Policies:
      - S3CrudPolicy:
          BucketName: !Ref PhotoBucket
      - DynamoDBCrudPolicy:
          TableName: !Ref PhotoTable
      - Version: '2012-10-17'
        Statement:
          - Effect: Allow
            Action:
              - rekognition:DetectFaces
            Resource: '*'
    Events:
      S3Event:
        Type: EventBridgeRule
        Properties:
          Pattern:
            source:
              - aws.s3
            detail-type:
              - Object Created
            detail:
              bucket:
                name:
                  - !Ref PhotoBucket
              object:
                key:
                  - prefix: input/
```

### 1.2 Process Image Lambda Code

#### src/process_image/requirements.txt

```
pillow>=10.0.0
pillow-heif>=0.13.0  # HEIC/HEIF support for iPhone photos
boto3>=1.28.0
```

**Note on HEIC support**: iPhones default to HEIC format. The `pillow-heif` package registers 
a plugin with Pillow to handle HEIC/HEIF files transparently. Import it at the top of app.py.

#### src/process_image/app.py

```python
"""
Process Image Lambda Handler

Triggered by S3 ObjectCreated events via EventBridge.
1. Downloads image from input/
2. Computes content hash for deduplication
3. Checks DynamoDB for existing hash
4. If new: extract EXIF, smart crop, dither, save
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
from datetime import datetime
from io import BytesIO

import boto3
from PIL import Image, ImageOps
from PIL.ExifTags import TAGS, GPSTAGS

# Register HEIC/HEIF support (iPhone format)
import pillow_heif
pillow_heif.register_heif_opener()

from image_processor import smart_crop, dither_image
from db import check_duplicate, save_image_metadata

logger = logging.getLogger()
logger.setLevel(os.environ.get('LOG_LEVEL', 'INFO'))

s3 = boto3.client('s3')
rekognition = boto3.client('rekognition')

BUCKET_NAME = os.environ['BUCKET_NAME']
TABLE_NAME = os.environ['TABLE_NAME']

# Supported image formats
SUPPORTED_FORMATS = {'.jpg', '.jpeg', '.png', '.heic', '.heif', '.webp'}
MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5MB - Rekognition limit for bytes input


def handler(event, context):
    """Main Lambda handler."""
    logger.info(f"Event: {json.dumps(event)}")
    
    # Extract S3 key from EventBridge event
    detail = event.get('detail', {})
    bucket = detail.get('bucket', {}).get('name')
    key = detail.get('object', {}).get('key')
    
    if not bucket or not key:
        logger.error("Missing bucket or key in event")
        return {'statusCode': 400, 'body': 'Invalid event'}
    
    if not key.startswith('input/'):
        logger.info(f"Ignoring non-input key: {key}")
        return {'statusCode': 200, 'body': 'Ignored'}
    
    # Validate file extension
    ext = os.path.splitext(key)[1].lower()
    if ext not in SUPPORTED_FORMATS:
        logger.warning(f"Unsupported format {ext}, deleting: {key}")
        s3.delete_object(Bucket=bucket, Key=key)
        return {'statusCode': 400, 'body': f'Unsupported format: {ext}'}
    
    try:
        process_image(bucket, key)
        return {'statusCode': 200, 'body': 'Processed'}
    except Exception as e:
        logger.exception(f"Error processing {key}: {e}")
        raise


def process_image(bucket: str, key: str):
    """Process a single image."""
    
    # 1. Download image
    logger.info(f"Downloading s3://{bucket}/{key}")
    response = s3.get_object(Bucket=bucket, Key=key)
    image_bytes = response['Body'].read()
    
    # Validate file size
    if len(image_bytes) > MAX_IMAGE_SIZE:
        logger.warning(f"Image too large ({len(image_bytes)} bytes), skipping")
        s3.delete_object(Bucket=bucket, Key=key)
        return
    
    # 2. Compute content hash
    content_hash = hashlib.sha256(image_bytes).hexdigest()
    logger.info(f"Content hash: {content_hash}")
    
    # 3. Check for duplicate
    if check_duplicate(TABLE_NAME, content_hash):
        logger.info(f"Duplicate detected, skipping: {content_hash}")
        s3.delete_object(Bucket=bucket, Key=key)
        return
    
    # 4. Open image and extract EXIF
    try:
        img = Image.open(BytesIO(image_bytes))
    except Exception as e:
        logger.error(f"Failed to open image: {e}")
        s3.delete_object(Bucket=bucket, Key=key)
        return
    
    exif_data = extract_exif(img)
    logger.info(f"EXIF data: {exif_data}")
    
    # 5. Fix orientation based on EXIF (handles rotated phone photos)
    img = ImageOps.exif_transpose(img)
    
    # Convert to RGB if necessary (handles RGBA, P mode, etc.)
    if img.mode not in ('RGB', 'L'):
        img = img.convert('RGB')
    
    # 6. Smart crop using Rekognition (pass bytes, not S3 reference to avoid race condition)
    img_cropped = smart_crop(img, image_bytes, rekognition)
    
    # 7. Dither to 6-color Spectra 6 palette
    img_dithered = dither_image(img_cropped)
    
    # 8. Generate IDs and paths
    image_id = str(uuid.uuid4())
    timestamp = datetime.utcnow()
    date_prefix = timestamp.strftime('%Y/%m/%d')
    
    input_ext = os.path.splitext(key)[1].lower()
    is_heic = input_ext in ('.heic', '.heif')
    
    # Normalize HEIC to JPG for storage (smaller, more compatible)
    original_ext = '.jpg' if is_heic else input_ext
    original_key = f"originals/{date_prefix}/{image_id}{original_ext}"
    dithered_key = f"dithered/{image_id}.png"
    
    # 9. Save dithered image
    dithered_buffer = BytesIO()
    img_dithered.save(dithered_buffer, format='PNG', optimize=True)
    dithered_buffer.seek(0)
    
    s3.put_object(
        Bucket=bucket,
        Key=dithered_key,
        Body=dithered_buffer.getvalue(),
        ContentType='image/png'
    )
    logger.info(f"Saved dithered image: {dithered_key}")
    
    # 10. Save original (convert HEIC to JPG, copy others as-is)
    if is_heic:
        # Convert HEIC to JPG
        original_buffer = BytesIO()
        original_img = Image.open(BytesIO(image_bytes))
        original_img = ImageOps.exif_transpose(original_img)
        if original_img.mode != 'RGB':
            original_img = original_img.convert('RGB')
        original_img.save(original_buffer, format='JPEG', quality=95)
        original_buffer.seek(0)
        s3.put_object(
            Bucket=bucket,
            Key=original_key,
            Body=original_buffer.getvalue(),
            ContentType='image/jpeg'
        )
    else:
        # Copy JPG/PNG/WebP as-is
        s3.copy_object(
            Bucket=bucket,
            Key=original_key,
            CopySource={'Bucket': bucket, 'Key': key}
        )
    logger.info(f"Saved original to: {original_key}")
    
    # 11. Save metadata to DynamoDB
    metadata = {
        'image_id': image_id,
        'content_hash': content_hash,
        'original_s3_key': original_key,
        'dithered_s3_key': dithered_key,
        'processing_version': 'v1',
        'created_at': timestamp.isoformat(),
        'taken_at': exif_data.get('taken_at'),
        'location': exif_data.get('location'),
        'source': 'manual',
        'last_shown_at': None,
        'show_count': 0,
        'random_sort': random.random(),
        'pk': 'IMAGE',  # Partition key for RandomSortIndex
    }
    save_image_metadata(TABLE_NAME, metadata)
    logger.info(f"Saved metadata for: {image_id}")
    
    # 12. Delete input file
    s3.delete_object(Bucket=bucket, Key=key)
    logger.info(f"Deleted input file: {key}")


def extract_exif(img: Image.Image) -> dict:
    """Extract relevant EXIF data from image."""
    result = {
        'taken_at': None,
        'location': None,
    }
    
    try:
        exif = img._getexif()
        if not exif:
            return result
        
        exif_dict = {TAGS.get(k, k): v for k, v in exif.items()}
        
        # Date taken
        date_taken = exif_dict.get('DateTimeOriginal') or exif_dict.get('DateTime')
        if date_taken:
            try:
                dt = datetime.strptime(date_taken, '%Y:%m:%d %H:%M:%S')
                result['taken_at'] = dt.isoformat()
            except ValueError:
                pass
        
        # GPS coordinates
        gps_info = exif_dict.get('GPSInfo')
        if gps_info:
            gps_dict = {GPSTAGS.get(k, k): v for k, v in gps_info.items()}
            lat = _convert_gps_coord(
                gps_dict.get('GPSLatitude'),
                gps_dict.get('GPSLatitudeRef')
            )
            lng = _convert_gps_coord(
                gps_dict.get('GPSLongitude'),
                gps_dict.get('GPSLongitudeRef')
            )
            if lat and lng:
                result['location'] = {'lat': lat, 'lng': lng}
    
    except Exception as e:
        logger.warning(f"Error extracting EXIF: {e}")
    
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
        if ref in ['S', 'W']:
            decimal = -decimal
        return decimal
    except (TypeError, IndexError, ValueError):
        return None
```

#### src/process_image/image_processor.py

```python
"""
Image processing functions: smart crop and dithering.
"""

import logging
from io import BytesIO
from PIL import Image

logger = logging.getLogger()

# Target dimensions for e-ink display
TARGET_WIDTH = 800
TARGET_HEIGHT = 480
TARGET_ASPECT = TARGET_WIDTH / TARGET_HEIGHT  # ~1.667

# E Ink Spectra 6 palette (6 colors, not 7)
# Based on reTerminal E1002 specifications
# TODO: These RGB values are approximate - calibrate after seeing real output
EINK_PALETTE = [
    (0, 0, 0),        # Black
    (255, 255, 255),  # White
    (200, 0, 0),      # Red
    (0, 200, 0),      # Green
    (0, 0, 200),      # Blue
    (255, 255, 0),    # Yellow
    # Note: Spectra 6 has 6 colors, no orange
]


def smart_crop(
    img: Image.Image,
    img_bytes: bytes,
    rekognition_client
) -> Image.Image:
    """
    Crop image intelligently based on face detection.
    Falls back to center crop if no faces detected.
    
    Args:
        img: PIL Image object (already orientation-corrected)
        img_bytes: Original image bytes (passed to Rekognition directly)
        rekognition_client: boto3 Rekognition client
    
    Returns:
        Cropped and resized PIL Image (800x480)
    
    Note: We pass bytes directly to Rekognition instead of S3 reference
    to avoid race conditions (the input file may be deleted before 
    Rekognition processes it).
    """
    img_w, img_h = img.size
    img_aspect = img_w / img_h
    
    # Detect faces (using bytes, not S3 reference)
    try:
        response = rekognition_client.detect_faces(
            Image={'Bytes': img_bytes},
            Attributes=['DEFAULT']
        )
        faces = response.get('FaceDetails', [])
        logger.info(f"Detected {len(faces)} faces")
    except Exception as e:
        logger.warning(f"Rekognition failed, using center crop: {e}")
        faces = []
    
    if faces:
        # Calculate bounding box containing all faces
        boxes = [f['BoundingBox'] for f in faces]
        
        # Rekognition returns normalized coords (0-1)
        face_left = min(b['Left'] for b in boxes) * img_w
        face_top = min(b['Top'] for b in boxes) * img_h
        face_right = max(b['Left'] + b['Width'] for b in boxes) * img_w
        face_bottom = max(b['Top'] + b['Height'] for b in boxes) * img_h
        
        # Center point of all faces
        center_x = (face_left + face_right) / 2
        center_y = (face_top + face_bottom) / 2
        
        # Add 30% padding around faces
        face_w = (face_right - face_left) * 1.3
        face_h = (face_bottom - face_top) * 1.3
    else:
        # No faces - use image center
        center_x = img_w / 2
        center_y = img_h / 2
        face_w = face_h = 0
    
    # Calculate crop dimensions (largest 5:3 box that fits in image)
    if img_aspect > TARGET_ASPECT:
        # Image wider than target - crop width
        crop_h = img_h
        crop_w = crop_h * TARGET_ASPECT
    else:
        # Image taller than target - crop height
        crop_w = img_w
        crop_h = crop_w / TARGET_ASPECT
    
    # Ensure crop is large enough to contain faces with padding
    if face_w > crop_w or face_h > crop_h:
        # Faces don't fit - scale up crop (may exceed image bounds)
        scale = max(face_w / crop_w, face_h / crop_h)
        crop_w *= scale
        crop_h *= scale
    
    # Clamp crop to image bounds
    crop_w = min(crop_w, img_w)
    crop_h = min(crop_h, img_h)
    
    # Re-enforce aspect ratio after clamping
    if crop_w / crop_h > TARGET_ASPECT:
        crop_w = crop_h * TARGET_ASPECT
    else:
        crop_h = crop_w / TARGET_ASPECT
    
    # Position crop centered on faces, clamped to image bounds
    left = max(0, min(center_x - crop_w / 2, img_w - crop_w))
    top = max(0, min(center_y - crop_h / 2, img_h - crop_h))
    
    # Crop and resize
    cropped = img.crop((left, top, left + crop_w, top + crop_h))
    resized = cropped.resize((TARGET_WIDTH, TARGET_HEIGHT), Image.LANCZOS)
    
    return resized


def dither_image(img: Image.Image) -> Image.Image:
    """
    Dither image to 6-color E Ink Spectra 6 palette.
    
    Uses Floyd-Steinberg dithering with perceptual color matching.
    Colors: Black, White, Red, Green, Blue, Yellow
    
    TODO: Consider using CIELAB color space for better perceptual matching.
          Current implementation uses simple RGB distance.
    
    Args:
        img: PIL Image (should be 800x480 RGB)
    
    Returns:
        Dithered PIL Image with only palette colors
    """
    # Ensure RGB mode
    if img.mode != 'RGB':
        img = img.convert('RGB')
    
    # Boost saturation slightly (e-ink colors look muted)
    img = _boost_saturation(img, factor=1.2)
    
    # Create palette image
    palette_img = Image.new('P', (1, 1))
    flat_palette = []
    for color in EINK_PALETTE:
        flat_palette.extend(color)
    # Pad palette to 256 colors (required by PIL)
    flat_palette.extend([0, 0, 0] * (256 - len(EINK_PALETTE)))
    palette_img.putpalette(flat_palette)
    
    # Quantize with dithering
    # Method 0 = median cut, 1 = maximum coverage, 2 = fast octree
    # Using Floyd-Steinberg dithering (dither=1)
    dithered = img.quantize(
        colors=len(EINK_PALETTE),
        palette=palette_img,
        dither=Image.Dither.FLOYDSTEINBERG
    )
    
    # Convert back to RGB for saving
    return dithered.convert('RGB')


def _boost_saturation(img: Image.Image, factor: float = 1.2) -> Image.Image:
    """Boost image saturation by given factor."""
    from PIL import ImageEnhance
    enhancer = ImageEnhance.Color(img)
    return enhancer.enhance(factor)
```

#### src/process_image/db.py

```python
"""
DynamoDB operations for process_image Lambda.
"""

import logging
import boto3
from decimal import Decimal

logger = logging.getLogger()
dynamodb = boto3.resource('dynamodb')


def check_duplicate(table_name: str, content_hash: str) -> bool:
    """
    Check if an image with this content hash already exists.
    
    Args:
        table_name: DynamoDB table name
        content_hash: SHA-256 hash of image content
    
    Returns:
        True if duplicate exists, False otherwise
    """
    table = dynamodb.Table(table_name)
    
    response = table.query(
        IndexName='ContentHashIndex',
        KeyConditionExpression='content_hash = :hash',
        ExpressionAttributeValues={':hash': content_hash},
        Limit=1
    )
    
    return len(response.get('Items', [])) > 0


def save_image_metadata(table_name: str, metadata: dict) -> None:
    """
    Save image metadata to DynamoDB.
    
    Args:
        table_name: DynamoDB table name
        metadata: Image metadata dictionary
    """
    table = dynamodb.Table(table_name)
    
    # Convert floats to Decimal for DynamoDB
    item = {}
    for key, value in metadata.items():
        if isinstance(value, float):
            item[key] = Decimal(str(value))
        elif value is not None:
            item[key] = value
    
    table.put_item(Item=item)
    logger.info(f"Saved item: {metadata['image_id']}")
```

### 1.3 Phase 1 Testing

After deploying Phase 1:

```bash
# 1. Build and deploy
sam build
sam deploy --guided

# 2. Upload test image manually
aws s3 cp test_photo.jpg s3://photo-frame-dev-{account-id}/input/test.jpg

# 3. Check CloudWatch logs
aws logs tail /aws/lambda/photo-frame-process-dev --follow

# 4. Verify outputs
aws s3 ls s3://photo-frame-dev-{account-id}/originals/ --recursive
aws s3 ls s3://photo-frame-dev-{account-id}/dithered/

# 5. Check DynamoDB
aws dynamodb scan --table-name photo-frame-images-dev

# 6. Download and visually inspect dithered image
aws s3 cp s3://photo-frame-dev-{account-id}/dithered/{image-id}.png ./test_output.png
```

---

## Phase 2: API Gateway + Custom Domain + Endpoints

### 2.1 ACM Certificate (Conditional)

**Important**: For regional endpoints, create the certificate in the same region as your API.
CloudFormation will automatically validate via DNS if you provide the HostedZoneId.

```yaml
# ACM Certificate for custom domain - only created if EnableCustomDomain is true
ApiCertificate:
  Type: AWS::CertificateManager::Certificate
  Condition: UseCustomDomain
  Properties:
    DomainName: !Ref DomainName
    ValidationMethod: DNS
    DomainValidationOptions:
      - DomainName: !Ref DomainName
        HostedZoneId: !Ref HostedZoneId
```

**Note**: When `EnableCustomDomain` is `true`, the `HostedZoneId` parameter is required 
for automatic certificate validation. Find it via:
```bash
aws route53 list-hosted-zones --query "HostedZones[?Name=='yourdomain.com.'].Id" --output text
# Returns: /hostedzone/Z1234567890ABC
# Use just the ID part: Z1234567890ABC
```

### 2.2 Add API Gateway with Custom Domain to template.yaml

SAM has built-in support for custom domains via the `Domain` property. This automatically creates:
- AWS::ApiGateway::DomainName
- AWS::ApiGateway::BasePathMapping  
- AWS::Route53::RecordSet (A record alias)

```yaml
# API Gateway with Optional Custom Domain
PhotoFrameApi:
  Type: AWS::Serverless::Api
  Properties:
    Name: !Sub photo-frame-api-${Environment}
    StageName: prod
    Auth:
      ApiKeyRequired: true
      UsagePlan:
        CreateUsagePlan: PER_API
        UsagePlanName: !Sub photo-frame-usage-plan-${Environment}
        Throttle:
          RateLimit: 10
          BurstLimit: 20
        Quota:
          Limit: 5000
          Period: MONTH
    
    # Custom Domain Configuration - conditionally included
    # Uses Fn::If with AWS::NoValue to omit the entire Domain property when disabled
    Domain: !If
      - UseCustomDomain
      - DomainName: !Ref DomainName
        CertificateArn: !Ref ApiCertificate
        EndpointConfiguration: REGIONAL
        Route53:
          HostedZoneName: !Ref HostedZoneName
        BasePath:
          - /v1/photo-frame
      - !Ref AWS::NoValue
```

**How the conditional works:**
- When `EnableCustomDomain` is `"true"`: The `Domain` property is populated with the full 
  custom domain configuration, creating `api.yourdomain.com/v1/photo-frame/...`
- When `EnableCustomDomain` is `"false"`: `AWS::NoValue` removes the `Domain` property 
  entirely, so the API uses the default API Gateway URL: 
  `https://{api-id}.execute-api.{region}.amazonaws.com/prod/...`

### 2.3 API Keys

```yaml
# API Keys
UploadApiKey:
  Type: AWS::ApiGateway::ApiKey
  Properties:
    Name: !Sub photo-frame-upload-key-${Environment}
    Enabled: true
    StageKeys:
      - RestApiId: !Ref PhotoFrameApi
        StageName: prod

DisplayApiKey:
  Type: AWS::ApiGateway::ApiKey
  Properties:
    Name: !Sub photo-frame-display-key-${Environment}
    Enabled: true
    StageKeys:
      - RestApiId: !Ref PhotoFrameApi
        StageName: prod

# Usage Plan Key Associations
UploadApiKeyUsagePlanKey:
  Type: AWS::ApiGateway::UsagePlanKey
  Properties:
    KeyId: !Ref UploadApiKey
    KeyType: API_KEY
    UsagePlanId: !Ref PhotoFrameApiUsagePlan
  # Note: SAM creates the usage plan with name {ApiLogicalId}UsagePlan

DisplayApiKeyUsagePlanKey:
  Type: AWS::ApiGateway::UsagePlanKey
  Properties:
    KeyId: !Ref DisplayApiKey
    KeyType: API_KEY
    UsagePlanId: !Ref PhotoFrameApiUsagePlan
```

### 2.4 API URL Structure

| Mode | Upload URL | Get Image |
|------|------------|-----------|
| With custom domain | `https://api.example.com/v1/photo-frame/upload-url` | `https://api.example.com/v1/photo-frame/image` |
| Without custom domain | `https://{api-id}.execute-api.{region}.amazonaws.com/prod/upload-url` | `https://{api-id}.execute-api.{region}.amazonaws.com/prod/image` |

**Note**: The `/v1/photo-frame` base path only applies when using a custom domain. Without custom domain, paths are rooted at `/prod/`.

This allows future expansion:
- `/v1/photo-frame/...` - Photo frame product
- `/v1/other-product/...` - Future products
- `/v2/photo-frame/...` - Future API versions

### 2.5 Lambda Function Definitions with API Events

Add these to template.yaml:

```yaml
# Get Upload URL Lambda
GetUploadUrlFunction:
  Type: AWS::Serverless::Function
  Properties:
    FunctionName: !Sub photo-frame-get-upload-url-${Environment}
    CodeUri: src/get_upload_url/
    Handler: app.handler
    Runtime: python3.12
    Timeout: 10
    MemorySize: 128
    Environment:
      Variables:
        BUCKET_NAME: !Ref PhotoBucket
    Policies:
      - S3WritePolicy:
          BucketName: !Ref PhotoBucket
    Events:
      ApiEvent:
        Type: Api
        Properties:
          RestApiId: !Ref PhotoFrameApi
          Path: /upload-url
          Method: GET
          Auth:
            ApiKeyRequired: true

# Get Random Image Lambda
GetRandomImageFunction:
  Type: AWS::Serverless::Function
  Properties:
    FunctionName: !Sub photo-frame-get-random-image-${Environment}
    CodeUri: src/get_random_image/
    Handler: app.handler
    Runtime: python3.12
    Timeout: 10
    MemorySize: 256
    Environment:
      Variables:
        BUCKET_NAME: !Ref PhotoBucket
        TABLE_NAME: !Ref PhotoTable
    Policies:
      - S3ReadPolicy:
          BucketName: !Ref PhotoBucket
      - DynamoDBCrudPolicy:
          TableName: !Ref PhotoTable
    Events:
      ApiEvent:
        Type: Api
        Properties:
          RestApiId: !Ref PhotoFrameApi
          Path: /image
          Method: GET
          Auth:
            ApiKeyRequired: true
```

Note: The Lambda paths are `/upload-url` and `/image`. With custom domain + BasePath mapping 
they become `https://api.example.com/v1/photo-frame/upload-url`. Without custom domain,
they're at `https://{api-id}.execute-api.{region}.amazonaws.com/prod/upload-url`.

### 2.6 Get Upload URL Lambda

#### src/get_upload_url/requirements.txt

```
boto3>=1.28.0
```

#### src/get_upload_url/app.py

```python
"""
Get Upload URL Lambda Handler

Returns a presigned S3 URL for uploading images.
Called by iOS Shortcut.
"""

import os
import json
import uuid
import boto3
from datetime import datetime

s3 = boto3.client('s3')
BUCKET_NAME = os.environ['BUCKET_NAME']


def handler(event, context):
    """Generate presigned URL for image upload."""
    
    # Generate unique filename
    image_id = str(uuid.uuid4())
    timestamp = datetime.utcnow().strftime('%Y%m%d%H%M%S')
    key = f"input/{timestamp}-{image_id}.jpg"
    
    # Generate presigned URL (valid 5 minutes)
    presigned_url = s3.generate_presigned_url(
        'put_object',
        Params={
            'Bucket': BUCKET_NAME,
            'Key': key,
            'ContentType': 'image/jpeg'
        },
        ExpiresIn=300
    )
    
    return {
        'statusCode': 200,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*'
        },
        'body': json.dumps({
            'upload_url': presigned_url,
            'image_id': image_id
        })
    }
```

### 2.7 Get Random Image Lambda

**Fallback when Rekognition is unavailable**: if Rekognition fails or is disabled, use the center-crop path in `smart_crop` and log the fallback reason. This keeps the pipeline usable without external dependency failures.

#### src/get_random_image/requirements.txt

```
boto3>=1.28.0
```

#### src/get_random_image/app.py

```python
"""
Get Random Image Lambda Handler

Returns a random image from the dithered collection.
Implements weighted random selection to avoid repetition.
Called by SenseCraft HMI on reTerminal.
"""

import os
import json
import random
import logging
from datetime import datetime, timedelta
from decimal import Decimal

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')

BUCKET_NAME = os.environ['BUCKET_NAME']
TABLE_NAME = os.environ['TABLE_NAME']

# Images shown in last N selections get lower weight
RECENCY_PENALTY_COUNT = 10


def handler(event, context):
    """Return a random dithered image."""
    
    # Parse query params
    params = event.get('queryStringParameters') or {}
    include_metadata = params.get('metadata', 'false').lower() == 'true'
    
    try:
        image = select_random_image()
        
        if not image:
            return {
                'statusCode': 404,
                'body': json.dumps({'error': 'No images available'})
            }
        
        # Generate presigned URL for the dithered image
        presigned_url = s3.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': BUCKET_NAME,
                'Key': image['dithered_s3_key']
            },
            ExpiresIn=3600  # 1 hour
        )
        
        # Update last_shown_at and show_count
        update_shown(image['image_id'])
        
        # Build response
        response_body = {
            'image_url': presigned_url,
            'image_id': image['image_id']
        }
        
        if include_metadata:
            response_body['metadata'] = {
                'taken_at': image.get('taken_at'),
                'location': image.get('location'),
                'show_count': int(image.get('show_count', 0)) + 1
            }
        
        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps(response_body)
        }
    
    except Exception as e:
        logger.exception(f"Error getting random image: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': 'Internal server error'})
        }


def select_random_image() -> dict | None:
    """
    Select a random image with weighted probability.
    
    Recently shown images have lower weight.
    Uses RandomSortIndex GSI for efficient random access.
    """
    table = dynamodb.Table(TABLE_NAME)
    
    # Generate random starting point
    random_start = Decimal(str(random.random()))
    
    # Query from random point, get batch of candidates
    response = table.query(
        IndexName='RandomSortIndex',
        KeyConditionExpression='pk = :pk AND random_sort >= :start',
        ExpressionAttributeValues={
            ':pk': 'IMAGE',
            ':start': random_start
        },
        Limit=20
    )
    
    candidates = response.get('Items', [])
    
    # If we got less than 20, wrap around and get more from beginning
    if len(candidates) < 20:
        wrap_response = table.query(
            IndexName='RandomSortIndex',
            KeyConditionExpression='pk = :pk AND random_sort < :start',
            ExpressionAttributeValues={
                ':pk': 'IMAGE',
                ':start': random_start
            },
            Limit=20 - len(candidates)
        )
        candidates.extend(wrap_response.get('Items', []))
    
    if not candidates:
        return None
    
    # Apply weights based on recency
    weighted_candidates = []
    now = datetime.utcnow()
    
    for item in candidates:
        weight = 1.0
        
        # Penalize recently shown images
        last_shown = item.get('last_shown_at')
        if last_shown:
            try:
                last_shown_dt = datetime.fromisoformat(last_shown)
                hours_ago = (now - last_shown_dt).total_seconds() / 3600
                
                if hours_ago < 24:
                    weight *= 0.1  # Heavy penalty for last 24 hours
                elif hours_ago < 72:
                    weight *= 0.5  # Medium penalty for last 3 days
            except ValueError:
                pass
        
        # Slight bonus for never/rarely shown images
        show_count = int(item.get('show_count', 0))
        if show_count == 0:
            weight *= 1.5
        
        weighted_candidates.append((item, weight))
    
    # Weighted random selection
    total_weight = sum(w for _, w in weighted_candidates)
    r = random.random() * total_weight
    
    cumulative = 0
    for item, weight in weighted_candidates:
        cumulative += weight
        if r <= cumulative:
            return item
    
    # Fallback (shouldn't reach here)
    return weighted_candidates[-1][0]


def update_shown(image_id: str) -> None:
    """Update last_shown_at and increment show_count."""
    table = dynamodb.Table(TABLE_NAME)
    
    table.update_item(
        Key={'image_id': image_id},
        UpdateExpression='SET last_shown_at = :now, show_count = if_not_exists(show_count, :zero) + :one',
        ExpressionAttributeValues={
            ':now': datetime.utcnow().isoformat(),
            ':zero': 0,
            ':one': 1
        }
    )
```

### 2.8 Phase 2 Testing

After deployment, check the stack outputs for your actual endpoints:
```bash
sam list stack-outputs --stack-name photo-frame-backend
```

**Without custom domain:**
```bash
# Test upload URL endpoint
curl -H "x-api-key: YOUR_UPLOAD_KEY" \
  https://{api-id}.execute-api.{region}.amazonaws.com/prod/upload-url

# Test get random image
curl -H "x-api-key: YOUR_DISPLAY_KEY" \
  https://{api-id}.execute-api.{region}.amazonaws.com/prod/image
```

**With custom domain:**
```bash
# Test upload URL endpoint
curl -H "x-api-key: YOUR_UPLOAD_KEY" \
  https://api.example.com/v1/photo-frame/upload-url

# Test get random image
curl -H "x-api-key: YOUR_DISPLAY_KEY" \
  https://api.example.com/v1/photo-frame/image

# With metadata
curl -H "x-api-key: YOUR_DISPLAY_KEY" \
  "https://api.example.com/v1/photo-frame/image?metadata=true"
```

**Upload test:**
```bash
# Test uploading with presigned URL (same for both modes)
curl -X PUT -H "Content-Type: image/jpeg" \
  --data-binary @test_photo.jpg \
  "PRESIGNED_URL_FROM_ABOVE"
```

---

## Phase 3: iOS Shortcut

### 3.1 Create Shortcut

Name: "Upload to Photo Frame"

**Get your endpoint URL from stack outputs:**
```bash
sam list stack-outputs --stack-name photo-frame-backend --output json | jq -r '.[] | select(.OutputKey=="UploadEndpoint") | .OutputValue'
```

Steps:
1. **Receive** Images from Share Sheet
2. **Get Contents of URL**
   - URL: `{YOUR_UPLOAD_ENDPOINT}` (from stack outputs)
     - With custom domain: `https://api.example.com/v1/photo-frame/upload-url`
     - Without: `https://{api-id}.execute-api.{region}.amazonaws.com/prod/upload-url`
   - Method: GET
   - Headers: `x-api-key` = `{UPLOAD_API_KEY}`
3. **Get Dictionary Value** "upload_url" from previous result
4. **Get Contents of URL**
   - URL: (Dictionary Value from step 3)
   - Method: PUT  
   - Headers: `Content-Type` = `image/jpeg`
   - Request Body: Shortcut Input (the image)
5. **Show Notification** "Photo uploaded to frame!"

### 3.2 Handling Multiple Images

To upload multiple images at once, wrap steps 2-4 in a **Repeat with Each** block:

1. **Receive** Images from Share Sheet
2. **Repeat with Each** item in Shortcut Input
   - **Get Contents of URL** (get presigned URL)
   - **Get Dictionary Value** "upload_url"
   - **Get Contents of URL** (PUT to presigned URL)
3. **Show Notification** "Uploaded X photos!"

### 3.3 HEIC vs JPEG

iPhones default to HEIC format. The backend handles HEIC natively, but for the Shortcut:
- Use `image/jpeg` Content-Type (iOS Shortcuts auto-converts when needed)
- Or use `application/octet-stream` to preserve original format

### 3.4 Shortcut Debugging

If upload fails, common issues:
- Content-Type mismatch (try `image/jpeg` or `application/octet-stream`)
- Presigned URL expired (5 min window)
- API key not being sent properly
- Image too large (>5MB limit due to Rekognition)

---

## Phase 4: SenseCraft HMI Integration

The reTerminal E1002 uses SenseCraft HMI (https://sensecraft.seeed.cc/hmi) for content management.

### 4.1 Configuration Options

**Get your endpoint URL from stack outputs:**
```bash
sam list stack-outputs --stack-name photo-frame-backend --output json | jq -r '.[] | select(.OutputKey=="ImageEndpoint") | .OutputValue'
```

#### Option A: Using SenseCraft HMI Web Content Feature

SenseCraft HMI has a "Web" feature that can fetch and display content from URLs:

1. Log into SenseCraft HMI at https://sensecraft.seeed.cc/hmi
2. Create a new dashboard using "Web" content type
3. Configure to fetch from your API endpoint
4. Set refresh interval (recommended: 1-6 hours to preserve screen life)

#### Option B: Using SenseCraft Gallery + External Sync

1. Use SenseCraft HMI's Gallery feature
2. Create a separate sync script that:
   - Calls your `/image` endpoint
   - Downloads the image
   - Uploads to SenseCraft Gallery via their API

#### API Endpoint Details

- URL: `{YOUR_IMAGE_ENDPOINT}` (from stack outputs)
  - With custom domain: `https://api.example.com/v1/photo-frame/image`
  - Without: `https://{api-id}.execute-api.{region}.amazonaws.com/prod/image`
- Method: GET
- Headers: `x-api-key` = `{DISPLAY_API_KEY}`
- Response: JSON with `image_url` (presigned S3 URL)

### 4.2 Refresh Interval

**Important for screen longevity:**
- Minimum recommended: 1 hour between refreshes
- Optimal for battery + screen life: 4-6 hours
- Don't display static image for >24 hours (causes ghosting)
- The device has ~3 month battery life with default refresh settings

### 4.3 Display Considerations

The E Ink Spectra 6 display has 6 colors: black, white, red, green, blue, yellow.
- No orange or purple - these will be approximated
- Refresh takes several seconds (normal for color e-paper)
- Avoid rapid refresh cycles (degrades screen faster)

---

## Phase 5: Monitoring & Error Handling

### 5.1 CloudWatch Alarms (Optional)

Add to template.yaml for production monitoring:

```yaml
ProcessImageErrorAlarm:
  Type: AWS::CloudWatch::Alarm
  Properties:
    AlarmName: !Sub photo-frame-process-errors-${Environment}
    MetricName: Errors
    Namespace: AWS/Lambda
    Statistic: Sum
    Period: 300
    EvaluationPeriods: 1
    Threshold: 1
    ComparisonOperator: GreaterThanOrEqualToThreshold
    Dimensions:
      - Name: FunctionName
        Value: !Ref ProcessImageFunction
```

### 5.2 Dead Letter Queue (Optional)

For failed processing attempts:

```yaml
ProcessingDLQ:
  Type: AWS::SQS::Queue
  Properties:
    QueueName: !Sub photo-frame-dlq-${Environment}
    MessageRetentionPeriod: 1209600  # 14 days

# Add to ProcessImageFunction:
DeadLetterQueue:
  Type: SQS
  TargetArn: !GetAtt ProcessingDLQ.Arn
```

---

## Phase 6: Quality Tuning

### 6.1 Dithering Improvements

Research notes for Spectra 6 output:

1. **Discrete palette** - Each pixel is a single color from six muted inks
2. **High-chroma mapping** - Saturated colors (magenta/cyan) map to nearest ink
3. **Portrait handling** - Midtone-preserving tone curves keep faces natural

Target pipeline for best visual output:

1. **Tone mapping** - Apply gentle midtone lift and highlight roll-off
2. **Local contrast** - Optional mild unsharp mask or contrast boost
3. **Perceptual matching** - Use OKLab/CIELAB distance for palette selection
4. **Error diffusion** - Floyd-Steinberg or Jarvis-Judice-Ninke with serpentine scan
5. **Palette tuning** - Calibrate palette swatches on the actual display

### 6.2 Reprocessing

When algorithm improves:

```python
# Scan for v1 images
# For each: fetch original from originals/, reprocess, update dithered/, update version
```

The `processing_version` field in DynamoDB enables this.

---

## Environment Variables Reference

| Lambda | Variable | Value |
|--------|----------|-------|
| ProcessImage | BUCKET_NAME | photo-frame-{env}-{account} |
| ProcessImage | TABLE_NAME | photo-frame-images-{env} |
| GetUploadUrl | BUCKET_NAME | photo-frame-{env}-{account} |
| GetRandomImage | BUCKET_NAME | photo-frame-{env}-{account} |
| GetRandomImage | TABLE_NAME | photo-frame-images-{env} |

---

## Outputs (from SAM template)

```yaml
Outputs:
  # Always show the raw API Gateway URL
  ApiEndpoint:
    Description: API Gateway endpoint URL
    Value: !Sub https://${PhotoFrameApi}.execute-api.${AWS::Region}.amazonaws.com/prod
  
  # Conditional outputs for custom domain
  CustomDomainUrl:
    Condition: UseCustomDomain
    Description: Custom domain API URL
    Value: !Sub https://${DomainName}/v1/photo-frame
  
  # Dynamic endpoint URLs based on whether custom domain is enabled
  UploadEndpoint:
    Description: Full upload URL endpoint
    Value: !If
      - UseCustomDomain
      - !Sub https://${DomainName}/v1/photo-frame/upload-url
      - !Sub https://${PhotoFrameApi}.execute-api.${AWS::Region}.amazonaws.com/prod/upload-url
  
  ImageEndpoint:
    Description: Full get image endpoint  
    Value: !If
      - UseCustomDomain
      - !Sub https://${DomainName}/v1/photo-frame/image
      - !Sub https://${PhotoFrameApi}.execute-api.${AWS::Region}.amazonaws.com/prod/image
  
  BucketName:
    Description: S3 bucket name
    Value: !Ref PhotoBucket
  
  TableName:
    Description: DynamoDB table name
    Value: !Ref PhotoTable
  
  UploadApiKeyId:
    Description: Upload API Key ID (retrieve value with aws apigateway get-api-key)
    Value: !Ref UploadApiKey
  
  DisplayApiKeyId:
    Description: Display API Key ID (retrieve value with aws apigateway get-api-key)
    Value: !Ref DisplayApiKey
```

---

## SAM Configuration (samconfig.toml)

The template uses generic defaults so it can be shared publicly.
Override with your specific values in `samconfig.toml`.

### samconfig.toml.example (committed to repo)

```toml
# samconfig.toml.example
# Copy this to samconfig.toml and customize for your deployment
#
# QUICK START (no custom domain):
#   Just set EnableCustomDomain=false and deploy!
#
# WITH CUSTOM DOMAIN:
#   1. Set EnableCustomDomain=true
#   2. Set DomainName to your API subdomain (e.g., api.example.com)
#   3. Set HostedZoneName to your domain with trailing dot (e.g., example.com.)
#   4. Find your HostedZoneId:
#      aws route53 list-hosted-zones --query "HostedZones[?Name=='example.com.'].Id"

version = 0.1

[default.deploy.parameters]
stack_name = "photo-frame-backend"
resolve_s3 = true
s3_prefix = "photo-frame-backend"
region = "eu-west-1"
confirm_changeset = true
capabilities = "CAPABILITY_IAM"

parameter_overrides = [
    "Environment=dev",
    "EnableCustomDomain=false"
    # Uncomment and customize if EnableCustomDomain=true:
    # "DomainName=api.example.com",
    # "HostedZoneName=example.com.",
    # "HostedZoneId=YOUR_HOSTED_ZONE_ID"
]

[default.build.parameters]
use_container = true
```

### .gitignore addition

```
# Add to .gitignore
samconfig.toml
```

This keeps your personal configuration private while allowing others to use the template.

---

## Sample Events for Local Testing

### events/s3_put_event.json

```json
{
  "version": "0",
  "id": "example-id",
  "detail-type": "Object Created",
  "source": "aws.s3",
  "account": "123456789012",
  "time": "2024-01-15T12:00:00Z",
  "region": "us-west-2",
  "detail": {
    "bucket": {
      "name": "photo-frame-dev-123456789012"
    },
    "object": {
      "key": "input/test-image.jpg",
      "size": 1024000
    }
  }
}
```

### Local Testing Commands

```bash
# Test process_image locally
sam local invoke ProcessImageFunction -e events/s3_put_event.json

# Test API endpoints locally
sam local start-api
curl http://localhost:3000/upload-url
curl http://localhost:3000/image
```

---

## Stack Cleanup

To delete the stack and all resources:

```bash
# First, empty the S3 bucket (required before deletion)
aws s3 rm s3://photo-frame-{env}-{account-id} --recursive

# Delete the CloudFormation stack
sam delete --stack-name photo-frame-backend

# Note: If DeletionPolicy is Retain, manually delete:
# - S3 bucket
# - DynamoDB table
```

**Warning**: This will permanently delete all photos and metadata.

---

## Implementation Notes

1. **Verify AWS syntax** - SAM template syntax evolves; consult current AWS documentation
2. **Deploy incrementally** - Phase 1 first, test, then Phase 2, etc.
3. **Check CloudWatch logs** when things fail
4. **Pillow Lambda layer** - May need to use a Lambda layer or Docker-based build for Pillow dependencies
5. **API Key retrieval** - After deploy, retrieve actual key values:
   ```bash
   aws apigateway get-api-key --api-key {KeyId} --include-value
   ```
6. **ACM Certificate validation** - CloudFormation will automatically create DNS validation 
   records if using Route 53. The stack may take 5-10 minutes while waiting for certificate 
   validation. If it hangs longer, check the ACM console for validation status.
7. **DNS propagation** - After deployment, the custom domain may take a few minutes to 
   propagate. Test with the raw API Gateway URL first if needed.
8. **Configuration** - Copy `samconfig.toml.example` to `samconfig.toml` and customize 
   before deploying. The `samconfig.toml` is gitignored.
9. **Quick start without domain** - Set `EnableCustomDomain=false` to skip all domain 
   configuration and deploy immediately with the default API Gateway URL.
10. **HostedZoneName trailing dot** - If using custom domain, the HostedZoneName parameter 
    must end with a dot (e.g., `example.com.` not `example.com`). This is a Route 53 convention.

---

## Estimated Implementation Time

| Phase | Time |
|-------|------|
| Phase 1: Infrastructure + Process Lambda | 2-3 hours |
| Phase 2: API Layer | 1-2 hours |
| Phase 3: iOS Shortcut | 30 min |
| Phase 4: SenseCraft HMI | 30 min - 1 hour |
| Phase 5: Monitoring (optional) | 30 min |
| Phase 6: Quality tuning | Ongoing |
| **Total** | **5-8 hours** |

Allow additional time for debugging and iteration.

---

## Cost Estimate (Monthly)

| Service | Usage | Estimated Cost |
|---------|-------|----------------|
| S3 (Standard) | 5GB dithered images | $0.12 |
| S3 (Glacier IR) | 50GB originals | $0.20 |
| DynamoDB | On-demand, <1M reads/writes | Free tier |
| Lambda | <1M invocations | Free tier |
| API Gateway | <1M requests | Free tier |
| Rekognition | 1000 images/month | $1.00 |
| **Total** | | **~$1.50/month** |

Costs scale linearly with usage. Main cost drivers are Rekognition and S3 storage.

---

## Runtime & Language Analysis

### Current Choice: Python 3.12 on ARM64 (Graviton)

We chose Python with Graviton. Here's the analysis of alternatives:

### Cold Start Comparison (typical ranges)

| Language | Cold Start (x86) | Cold Start (ARM64) | Warm Invocation |
|----------|------------------|--------------------|--------------------|
| **Python 3.12** | 200-400ms | 150-300ms | 5-15ms |
| **Node.js/TypeScript** | 150-300ms | 100-250ms | 3-10ms |
| **Go** | 80-150ms | 60-120ms | 1-5ms |
| **Rust** | 10-50ms | 8-40ms | <1ms |

*Note: These are approximate ranges for simple functions. Actual times vary based on 
dependencies, memory allocation, and function complexity.*

### Analysis by Function

#### ProcessImage Lambda (CPU-intensive, image processing)

| Factor | Python | Go | TypeScript |
|--------|--------|-----|------------|
| Pillow ecosystem | Excellent (native) | Limited (external CGO) | Decent (Sharp) |
| HEIC support | pillow-heif | Requires CGO/external | Sharp supports |
| Cold start impact | Low (infrequent) | Lower | Medium |
| Development speed | Fast | Medium | Fast |
| **Recommendation** | **Good choice** | Possible | Possible |

**Verdict**: Python is a good choice here because:
- Pillow is mature, well-documented, and handles edge cases
- Cold starts don't matter much (EventBridge triggered, not user-facing)
- Image processing happens in C extensions anyway (Pillow is C-backed)
- Easy to iterate on dithering algorithms

#### GetUploadUrl Lambda (simple, latency-sensitive)

| Factor | Python | Go | TypeScript |
|--------|--------|-----|------------|
| Cold start | 200-400ms | 80-150ms | 150-300ms |
| Warm latency | 5-15ms | 1-5ms | 3-10ms |
| Code complexity | Simple | Simple | Simple |
| **Recommendation** | Adequate | **Best** | Good |

**Verdict**: Go would be optimal here for lowest latency, but the difference 
is <200ms on cold start. For a photo frame refreshing hourly, this doesn't matter.

#### GetRandomImage Lambda (DynamoDB query, latency-sensitive)

| Factor | Python | Go | TypeScript |
|--------|--------|-----|------------|
| Cold start | 200-400ms | 80-150ms | 150-300ms |
| boto3/SDK overhead | Higher | Lower | Medium |
| **Recommendation** | Adequate | **Best** | Good |

**Verdict**: Go would be ~2x faster, but again, for an e-ink display refreshing 
every 1-6 hours, sub-second latency differences are irrelevant.

### ARM64 (Graviton) Benefits

| Metric | x86_64 | ARM64 (Graviton) | Savings |
|--------|--------|------------------|---------|
| Price | $0.0000166667/GB-s | $0.0000133334/GB-s | **20% cheaper** |
| Performance | Baseline | 5-20% faster (varies) | **Better** |
| Cold start | Baseline | 10-20% faster | **Better** |

**Recommendation**: Use ARM64 for all functions. Already configured in Globals section.

### Should We Use Go or TypeScript?

#### Arguments FOR switching to Go:
- 2-3x faster cold starts
- Lower memory usage (~50% less)
- Single binary deployment (no dependencies)
- Better for high-frequency APIs

#### Arguments AGAINST switching:
- This is a personal photo frame, not a high-traffic API
- Display refreshes every 1-6 hours (cold starts irrelevant)
- Python's image processing ecosystem is superior
- Development velocity matters more than milliseconds
- Mixing languages adds complexity

#### Arguments FOR TypeScript:
- Good middle ground on performance
- Strong typing
- Sharp library for image processing
- Same language for potential future web UI

#### Arguments AGAINST TypeScript:
- Sharp has native dependencies (build complexity)
- Still slower than Go for simple operations
- Node.js cold starts aren't dramatically better than Python

### Final Recommendation

**Stick with Python 3.12 on ARM64 (Graviton)** because:

1. **Cold starts don't matter** - Display refreshes infrequently
2. **Image processing is the bottleneck** - Pillow is C-backed, same speed as alternatives
3. **Pillow ecosystem is unmatched** - HEIC, EXIF, dithering all well-supported
4. **20% cost savings with ARM64** - Already configured
5. **Simplicity** - Single language, easier to maintain
6. **Iteration speed** - Can quickly adjust dithering algorithms

**If requirements change** (high-traffic API, sub-100ms SLA), then:
- Split GetUploadUrl/GetRandomImage to Go
- Keep ProcessImage in Python (image processing)

### Alternative: Rust for Maximum Performance

If absolute performance were critical:
- Rust has <50ms cold starts
- `image` crate handles most formats
- But: Much steeper learning curve, slower development

For a personal project, the development time cost outweighs the runtime benefits.
