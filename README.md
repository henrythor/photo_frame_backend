# Photo Frame Backend

Backend system for the Seeed reTerminal E1002 ePaper display. Accepts photos via a presigned upload URL, processes them with smart cropping and dithering, and serves random images to the display.

## Features

- Presigned upload URL for iOS Shortcuts or other clients
- Event-driven image processing with deduplication
- Smart cropping with Rekognition fallback to center crop
- Spectra 6 palette dithering tuned for best visual output
- Random image selection with recency weighting
- Optional custom domain with API Gateway

## Architecture

```
Upload Client (iOS Shortcut)         reTerminal E1002 (SenseCraft HMI)
         | x-api-key: UPLOAD_KEY              | x-api-key: DISPLAY_KEY
         v                                    v
+----------------------------------------------------------+
|               API Gateway (optional domain)              |
+---------------------------+------------------------------+
        |                                      |
        v                                      v
+---------------------------+      +---------------------------+
| /upload-url Lambda        |      | /image Lambda             |
+-------------+-------------+      +-------------+-------------+
        |                                      |
        v                                      v
+----------------------------------------------------------+
|                        S3 Bucket                         |
|  input/ --> originals/            dithered/              |
+----------------------------------------------------------+
        |
        | S3 ObjectCreated -> EventBridge
        v
+------------------+          +------------------+
| Process Lambda   |--------->| DynamoDB         |
+------------------+          +------------------+
```

## Repository Layout

```
photo_frame_backend/
|-- template.yaml
|-- samconfig.toml.example
|-- src/
|   |-- process_image/
|   |-- get_upload_url/
|   |-- get_random_image/
|-- events/
|-- scripts/
|-- tests/
|-- README.md
```

## Deploy (AWS SAM)

1. Install AWS SAM CLI and configure credentials.
2. Copy `samconfig.toml.example` to `samconfig.toml` and update values.
3. Build and deploy:

```bash
sam build
sam deploy --guided
```

## Local Setup (uv)

```bash
uv venv --python 3.12.0
uv pip install -r requirements-dev.txt
```

## Image Processing Notes

The pipeline intentionally enhances midtones and local contrast before palette quantization to improve the final look on Spectra 6 panels. Palette values are placeholders and should be tuned after viewing output on the actual display.

### Palette Calibration

Generate a calibration image and palette template:

```bash
uv run python scripts/calibrate_palette.py
```

This produces:
- `scripts/palette_test.png` (swatches, gradient, skin-tone patches)
- `scripts/palette.json` (editable palette values)

Copy `scripts/palette.json` to `src/process_image/palette.json` and adjust values as needed. The Lambda reads from `PALETTE_PATH` (defaults to `palette.json` in the Lambda package).

## API Usage

### Upload URL

```bash
curl -H "x-api-key: YOUR_UPLOAD_KEY" \
  https://{api-id}.execute-api.{region}.amazonaws.com/prod/upload-url
```

Response:

```json
{
  "upload_url": "https://...",
  "image_id": "..."
}
```

### Upload Image

```bash
curl -X PUT -H "Content-Type: image/jpeg" \
  --data-binary @photo.jpg \
  "PRESIGNED_URL"
```

### Fetch Random Image

```bash
curl -H "x-api-key: YOUR_DISPLAY_KEY" \
  https://{api-id}.execute-api.{region}.amazonaws.com/prod/image
```

## Local Testing

```bash
sam local invoke ProcessImageFunction -e events/s3_put_event.json
sam local start-api
```

## Lambda Dependencies (optional)

```bash
uv pip install -r src/process_image/requirements.txt
uv pip install -r src/get_upload_url/requirements.txt
uv pip install -r src/get_random_image/requirements.txt
```

## Tests

```bash
uv venv --python 3.12.0
uv pip install -r requirements-dev.txt
uv run pytest
```

Coverage reporting (local):

```bash
uv run pytest --cov=src --cov-report=term-missing
```

Type checking (local):

```bash
uv run mypy src tests
```

Current test suite focuses on:
- Moto-backed DynamoDB/S3 integration tests
- Image processing pipeline unit tests
- Edge cases (unsupported formats, oversized uploads, Rekognition failures)

## AI Disclosure

We used AI-assisted tools during planning and implementation:

- Planning: Claude Opus 4.5
- Implementation: GPT-5.2 Codex xhigh

## License

GPL-3.0-only. See `LICENSE`.
