import math

from PIL import Image

from process_image import image_processor


def test_smart_crop_returns_target_size():
    img = Image.new("RGB", (1600, 1200), color=(255, 0, 0))

    class RekognitionStub:
        def detect_faces(self, Image=None, Attributes=None):
            return {"FaceDetails": []}

    cropped = image_processor.smart_crop(img, b"fake", RekognitionStub())

    assert cropped.size == (
        image_processor.TARGET_WIDTH,
        image_processor.TARGET_HEIGHT,
    )


def test_dither_image_uses_palette_colors():
    img = Image.new(
        "RGB", (image_processor.TARGET_WIDTH, image_processor.TARGET_HEIGHT)
    )
    for x in range(image_processor.TARGET_WIDTH):
        for y in range(image_processor.TARGET_HEIGHT):
            img.putpixel((x, y), (x % 256, y % 256, (x + y) % 256))

    dithered = image_processor.dither_image(img)
    palette_set = set(image_processor.load_palette())

    pixels = dithered.get_flattened_data()
    assert all(pixel in palette_set for pixel in pixels)


def test_tone_map_preserves_bounds():
    img = Image.new("RGB", (2, 2))
    img.putpixel((0, 0), (0, 0, 0))
    img.putpixel((1, 0), (255, 255, 255))
    img.putpixel((0, 1), (128, 128, 128))
    img.putpixel((1, 1), (200, 200, 200))

    mapped = image_processor._tone_map(img)
    for pixel in mapped.get_flattened_data():
        assert all(0 <= channel <= 255 for channel in pixel)


def test_local_contrast_keeps_size():
    img = Image.new("RGB", (10, 10), color=(100, 100, 100))
    contrasted = image_processor._local_contrast(img)
    assert contrasted.size == img.size
