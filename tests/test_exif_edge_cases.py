from datetime import datetime, timezone
import os

from PIL import Image


def _load_process_app():
    os.environ.setdefault("BUCKET_NAME", "photo-frame-test")
    os.environ.setdefault("TABLE_NAME", "photo-frame-images-test")
    from process_image import app as process_app

    return process_app


def test_extract_exif_handles_missing_exif():
    process_app = _load_process_app()
    img = Image.new("RGB", (10, 10))
    img._getexif = lambda: None

    result = process_app.extract_exif(img)
    assert result == {"taken_at": None, "location": None}


def test_extract_exif_handles_invalid_date():
    process_app = _load_process_app()
    img = Image.new("RGB", (10, 10))
    img._getexif = lambda: {36867: "invalid-date"}

    result = process_app.extract_exif(img)
    assert result["taken_at"] is None


def test_extract_exif_handles_gps_errors():
    process_app = _load_process_app()
    img = Image.new("RGB", (10, 10))
    img._getexif = lambda: {
        36867: datetime.now(timezone.utc).strftime("%Y:%m:%d %H:%M:%S"),
        34853: {1: "N", 2: ("bad",)},
    }

    result = process_app.extract_exif(img)
    assert result["location"] is None
