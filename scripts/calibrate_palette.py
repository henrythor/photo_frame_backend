"""
Generate a palette calibration image for Spectra 6 screens.

Outputs:
- palette_test.png: color swatches + gradients + skin-tone patches
- palette.json: editable palette values

Copy palette.json to src/process_image/palette.json for Lambda use.
"""

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

DEFAULT_PALETTE = [
    (0, 0, 0),
    (255, 255, 255),
    (180, 40, 30),
    (0, 170, 0),
    (0, 70, 200),
    (240, 220, 0),
]

OUTPUT_DIR = Path(__file__).resolve().parent


def main() -> None:
    width = 800
    height = 480
    img = Image.new("RGB", (width, height), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)

    swatch_height = 120
    swatch_width = width // len(DEFAULT_PALETTE)

    for idx, color in enumerate(DEFAULT_PALETTE):
        x0 = idx * swatch_width
        draw.rectangle([x0, 0, x0 + swatch_width, swatch_height], fill=color)
        draw.rectangle([x0, 0, x0 + swatch_width, swatch_height], outline=(0, 0, 0))

    gradient_top = swatch_height + 20
    gradient_height = 80
    for x in range(width):
        shade = int((x / (width - 1)) * 255)
        draw.line(
            [(x, gradient_top), (x, gradient_top + gradient_height)],
            fill=(shade, shade, shade),
        )

    labels_top = gradient_top + gradient_height + 20
    patch_size = 80
    patches = [
        (205, 133, 63),
        (240, 200, 170),
        (120, 80, 60),
        (90, 120, 140),
    ]

    for idx, color in enumerate(patches):
        x0 = 40 + idx * (patch_size + 40)
        y0 = labels_top
        draw.rectangle([x0, y0, x0 + patch_size, y0 + patch_size], fill=color)
        draw.rectangle([x0, y0, x0 + patch_size, y0 + patch_size], outline=(0, 0, 0))

    img.save(OUTPUT_DIR / "palette_test.png")

    palette_path = OUTPUT_DIR / "palette.json"
    with palette_path.open("w", encoding="utf-8") as fp:
        json.dump({"palette": DEFAULT_PALETTE}, fp, indent=2)

    print(f"Wrote {OUTPUT_DIR / 'palette_test.png'}")
    print(f"Wrote {palette_path}")


if __name__ == "__main__":
    main()
