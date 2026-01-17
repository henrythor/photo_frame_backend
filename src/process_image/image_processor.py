"""
Image processing functions: smart crop and dithering.
"""

import json
import logging
import os

from PIL import Image, ImageEnhance, ImageFilter

logger = logging.getLogger()

TARGET_WIDTH = 800
TARGET_HEIGHT = 480
TARGET_ASPECT = TARGET_WIDTH / TARGET_HEIGHT

DEFAULT_EINK_PALETTE = [
    (0, 0, 0),
    (255, 255, 255),
    (180, 40, 30),
    (0, 170, 0),
    (0, 70, 200),
    (240, 220, 0),
]


def load_palette() -> list[tuple[int, int, int]]:
    palette_path = os.environ.get("PALETTE_PATH", "palette.json")
    candidate_path = palette_path

    if not os.path.isabs(candidate_path) and not os.path.exists(candidate_path):
        candidate_path = os.path.join(os.path.dirname(__file__), palette_path)

    if not os.path.exists(candidate_path):
        return DEFAULT_EINK_PALETTE

    try:
        with open(candidate_path, "r", encoding="utf-8") as file_handle:
            payload = json.load(file_handle)
        palette = payload.get("palette", DEFAULT_EINK_PALETTE)
        normalized: list[tuple[int, int, int]] = []
        for color in palette:
            if isinstance(color, (list, tuple)) and len(color) == 3:
                normalized.append((int(color[0]), int(color[1]), int(color[2])))
        return normalized or DEFAULT_EINK_PALETTE
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        logger.warning("Failed to load palette %s: %s", candidate_path, exc)
        return DEFAULT_EINK_PALETTE


def smart_crop(img: Image.Image, img_bytes: bytes, rekognition_client) -> Image.Image:
    """Crop image intelligently based on face detection."""
    img_w, img_h = img.size
    img_aspect = img_w / img_h

    try:
        response = rekognition_client.detect_faces(
            Image={"Bytes": img_bytes}, Attributes=["DEFAULT"]
        )
        faces = response.get("FaceDetails", [])
        logger.info("Detected %s faces", len(faces))
    except Exception as exc:
        logger.warning("Rekognition failed, using center crop: %s", exc)
        faces = []

    if faces:
        boxes = [f["BoundingBox"] for f in faces]
        face_left = min(b["Left"] for b in boxes) * img_w
        face_top = min(b["Top"] for b in boxes) * img_h
        face_right = max(b["Left"] + b["Width"] for b in boxes) * img_w
        face_bottom = max(b["Top"] + b["Height"] for b in boxes) * img_h

        center_x = (face_left + face_right) / 2
        center_y = (face_top + face_bottom) / 2

        face_w = (face_right - face_left) * 1.3
        face_h = (face_bottom - face_top) * 1.3
    else:
        center_x = img_w / 2
        center_y = img_h / 2
        face_w = face_h = 0

    if img_aspect > TARGET_ASPECT:
        crop_h: float = float(img_h)
        crop_w: float = crop_h * TARGET_ASPECT
    else:
        crop_w = float(img_w)
        crop_h = crop_w / TARGET_ASPECT

    if face_w > crop_w or face_h > crop_h:
        scale = max(face_w / crop_w, face_h / crop_h)
        crop_w *= scale
        crop_h *= scale

    crop_w = min(crop_w, img_w)
    crop_h = min(crop_h, img_h)

    if crop_w / crop_h > TARGET_ASPECT:
        crop_w = crop_h * TARGET_ASPECT
    else:
        crop_h = crop_w / TARGET_ASPECT

    left = max(0.0, min(center_x - crop_w / 2, img_w - crop_w))
    top = max(0.0, min(center_y - crop_h / 2, img_h - crop_h))

    cropped = img.crop((int(left), int(top), int(left + crop_w), int(top + crop_h)))
    resized = cropped.resize((TARGET_WIDTH, TARGET_HEIGHT), Image.Resampling.LANCZOS)

    return resized


def dither_image(img: Image.Image) -> Image.Image:
    """Dither image to 6-color palette with perceptual tuning."""
    if img.mode != "RGB":
        img = img.convert("RGB")

    img = _tone_map(img)
    img = _boost_saturation(img, factor=1.15)
    img = _local_contrast(img, amount=0.2)

    palette = load_palette()
    palette_img = Image.new("P", (1, 1))
    flat_palette: list[int] = []
    for color in palette:
        flat_palette.extend(color)
    flat_palette.extend([0, 0, 0] * (256 - len(palette)))
    palette_img.putpalette(flat_palette)

    dithered = img.quantize(
        colors=len(palette),
        palette=palette_img,
        dither=Image.Dither.FLOYDSTEINBERG,
    )

    return dithered.convert("RGB")


def _tone_map(img: Image.Image) -> Image.Image:
    """Apply gentle tone curve to preserve midtones and highlights."""

    def curve(channel: int) -> int:
        normalized = channel / 255.0
        midtone_boost = normalized**0.9
        highlight_rolloff = 1 - (1 - midtone_boost) ** 1.1
        return int(max(0, min(255, highlight_rolloff * 255)))

    lut = [curve(i) for i in range(256)]
    return img.point(lut * 3)


def _boost_saturation(img: Image.Image, factor: float = 1.15) -> Image.Image:
    """Boost image saturation by given factor."""
    enhancer = ImageEnhance.Color(img)
    return enhancer.enhance(factor)


def _local_contrast(img: Image.Image, amount: float = 0.2) -> Image.Image:
    """Apply mild local contrast enhancement."""
    percent = int(100 + (amount * 200))
    return img.filter(ImageFilter.UnsharpMask(radius=1, percent=percent, threshold=2))
