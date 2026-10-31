import io
import subprocess
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from app.pipeline import image_tiles, ocr


def picture(mode='RGB', size=(32, 48)):
    stream = io.BytesIO()
    color = 123 if mode == 'L' else (80, 140, 200, 160) if mode == 'RGBA' else (80, 140, 200)
    image = Image.new(mode, size, color)
    try:
        image.save(stream, format='PNG')
        return stream.getvalue()
    finally:
        image.close()


@contextmanager
def captured_images():
    """Retain references deliberately: released C buffers must still be closed."""
    images = []
    original_open, original_convert, original_resize = Image.open, Image.Image.convert, Image.Image.resize
    original_crop = Image.Image.crop
    def opened(*args, **kwargs):
        image = original_open(*args, **kwargs)
        images.append(image)
        return image
    def converted(image, *args, **kwargs):
        result = original_convert(image, *args, **kwargs)
        images.append(result)
        return result
    def resized(image, *args, **kwargs):
        result = original_resize(image, *args, **kwargs)
        images.append(result)
        return result
    def cropped(image, *args, **kwargs):
        result = original_crop(image, *args, **kwargs)
        images.append(result)
        return result
    with patch.object(Image, 'open', side_effect=opened), \
         patch.object(Image.Image, 'convert', new=converted), \
         patch.object(Image.Image, 'resize', new=resized), \
         patch.object(Image.Image, 'crop', new=cropped):
        yield images, original_open


class OcrBufferTests(unittest.TestCase):
    def assert_released(self, images):
        self.assertTrue(images)
        for image in images:
            with self.assertRaises(ValueError):
                image.getpixel((0, 0))

    def test_resize_and_conversion_release_every_buffer_without_changing_output(self):
        data = picture('RGBA', (1800, 300))
        with captured_images() as (images, _):
            result = ocr._shrink(data)
            self.assert_released(images)
        with Image.open(io.BytesIO(result)) as image:
            self.assertEqual('RGB', image.mode)
            self.assertEqual((1600, 266), image.size)
            self.assertEqual((80, 140, 200), image.getpixel((0, 0)))

    def test_no_resize_still_releases_original_buffer(self):
        data = picture()
        with captured_images() as (images, _):
            result = ocr._shrink(data)
            self.assert_released(images)
        with Image.open(io.BytesIO(result)) as image:
            self.assertEqual((32, 48), image.size)
            self.assertEqual((80, 140, 200), image.getpixel((0, 0)))

    def test_shrink_save_failure_releases_buffers_and_keeps_original_fallback(self):
        data = picture('RGBA')
        with captured_images() as (images, _), \
             patch.object(Image.Image, 'save', side_effect=OSError('fixture failure')):
            self.assertEqual(data, ocr._shrink(data))
            self.assert_released(images)

    def test_shrink_conversion_failure_releases_source(self):
        data = picture('RGBA')
        with captured_images() as (images, _), \
             patch.object(Image.Image, 'convert', side_effect=OSError('fixture failure')):
            self.assertEqual(data, ocr._shrink(data))
            self.assert_released(images)

    def test_tesseract_releases_buffers_before_subprocess_without_changing_input(self):
        data = picture()
        with captured_images() as (images, original_open):
            def run(command, **kwargs):
                self.assert_released(images)
                with original_open(command[1]) as saved:
                    self.assertEqual('L', saved.mode)
                    self.assertEqual((64, 96), saved.size)
                self.assertEqual(8, kwargs['timeout'])
                self.assertEqual(['--psm', '6'], command[-2:])
                return SimpleNamespace(stdout='AI 기능\n')
            with patch.object(ocr, '_tess_opts', return_value=['-l', 'kor+eng']), \
                 patch.object(ocr.subprocess, 'run', side_effect=run):
                self.assertEqual('AI 기능', ocr._tesseract(data, timeout_seconds=8))
            self.assert_released(images)

    def test_tesseract_timeout_leaves_no_live_pillow_buffers(self):
        data = picture()
        with captured_images() as (images, _):
            def run(command, **kwargs):
                self.assert_released(images)
                raise subprocess.TimeoutExpired(command, kwargs['timeout'])
            with patch.object(ocr, '_tess_opts', return_value=['-l', 'kor+eng']), \
                 patch.object(ocr.subprocess, 'run', side_effect=run):
                with self.assertRaises(subprocess.TimeoutExpired):
                    ocr._tesseract(data, timeout_seconds=8)
            self.assert_released(images)

    def test_rgb_tiles_do_not_copy_full_source_and_release_all_crops(self):
        data = picture(size=(320, 4000))
        with captured_images() as (images, _), \
             patch.object(Image.Image, 'convert', side_effect=AssertionError('RGB source must not be duplicated')):
            tiles = image_tiles.split_image(data)
            self.assert_released(images)
        self.assertEqual(3, len(tiles))
        for payload, expected_size in zip(tiles, [(320, 1600), (320, 1600), (320, 1040)]):
            with Image.open(io.BytesIO(payload)) as image:
                self.assertEqual('RGB', image.mode)
                self.assertEqual(expected_size, image.size)
                self.assertEqual((80, 140, 200), image.getpixel((0, 0)))

    def test_grayscale_tiles_keep_rgb_output_and_release_conversion(self):
        data = picture('L', (200, 1800))
        with captured_images() as (images, _):
            tiles = image_tiles.split_image(data)
            self.assert_released(images)
        self.assertEqual(2, len(tiles))
        for payload in tiles:
            with Image.open(io.BytesIO(payload)) as image:
                self.assertEqual('RGB', image.mode)
                self.assertEqual(200, image.width)
                self.assertEqual((123, 123, 123), image.getpixel((0, 0)))

    def test_tile_save_failure_releases_source_and_crop(self):
        data = picture(size=(320, 4000))
        with captured_images() as (images, _), \
             patch.object(Image.Image, 'save', side_effect=OSError('fixture failure')):
            with self.assertRaises(OSError):
                image_tiles.split_image(data)
            self.assert_released(images)

    def test_tile_resize_failure_releases_converted_source(self):
        data = picture('L', (1250, 2600))
        with captured_images() as (images, _), \
             patch.object(Image.Image, 'resize', side_effect=OSError('fixture failure')):
            with self.assertRaises(OSError):
                image_tiles.split_image(data)
            self.assert_released(images)


if __name__ == '__main__':
    unittest.main()
