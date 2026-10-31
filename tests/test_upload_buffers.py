import io
import unittest
from contextlib import ExitStack, contextmanager
from unittest.mock import patch

from PIL import Image, ImageOps, PngImagePlugin

from app.upload_safety import UploadSafetyError, check_upload


LIMIT = 8 * 1024 * 1024


def encode(image, **options):
    with io.BytesIO() as stream:
        image.save(stream, format='PNG', **options)
        return stream.getvalue()


@contextmanager
def tracked_images():
    """Keep real objects alive so garbage collection cannot hide buffer leaks."""
    created = []

    def recorder(original):
        def call(*args, **kwargs):
            image = original(*args, **kwargs)
            created.append(image)
            return image
        return call

    with ExitStack() as stack:
        for owner, name in ((Image, 'open'), (Image, 'new'),
                            (ImageOps, 'exif_transpose'),
                            (Image.Image, 'convert'), (Image.Image, 'getchannel')):
            stack.enter_context(patch.object(owner, name, recorder(getattr(owner, name))))
        yield created


class UploadBufferTests(unittest.TestCase):
    def check(self, data):
        return check_upload('capture.png', 'image/png', data, max_bytes=LIMIT)

    def assert_closed(self, images):
        self.assertGreaterEqual(len(images), 2)
        for image in images:
            with self.assertRaises(ValueError):
                image.getpixel((0, 0))

    def test_rgb_without_rotation_avoids_full_image_copies(self):
        for orientation in (None, 1):
            with self.subTest(orientation=orientation):
                with Image.new('RGB', (60, 1800), (12, 34, 56)) as source:
                    metadata = PngImagePlugin.PngInfo()
                    metadata.add_text('Comment', 'private metadata')
                    options = {'pnginfo': metadata}
                    if orientation is not None:
                        exif = Image.Exif()
                        exif[274] = orientation
                        options['exif'] = exif
                    raw = encode(source, **options)
                with patch.object(ImageOps, 'exif_transpose', side_effect=AssertionError('unneeded rotation copy')), \
                     patch.object(Image.Image, 'convert', side_effect=AssertionError('unneeded RGB copy')):
                    result = self.check(raw)
                with Image.open(io.BytesIO(result.data)) as output:
                    self.assertEqual(output.format, 'PNG')
                    self.assertEqual(output.size, (60, 1800))
                    self.assertEqual(output.getpixel((59, 1799)), (12, 34, 56))
                    self.assertFalse(output.info)
                    self.assertFalse(output.getexif())

    def test_all_exif_orientations_preserve_visual_pixels(self):
        with Image.new('RGB', (6, 2)) as source:
            source.putdata([(i * 15, i * 10, i * 5) for i in range(12)])
            for orientation in range(2, 9):
                with self.subTest(orientation=orientation):
                    exif = Image.Exif()
                    exif[274] = orientation
                    raw = encode(source, exif=exif)
                    with Image.open(io.BytesIO(raw)) as original:
                        with ImageOps.exif_transpose(original) as expected:
                            expected_size = expected.size
                            expected_pixels = list(expected.getdata())
                    with tracked_images() as created:
                        result = self.check(raw)
                    self.assert_closed(created)
                    with Image.open(io.BytesIO(result.data)) as output:
                        self.assertEqual(output.size, expected_size)
                        self.assertEqual(list(output.getdata()), expected_pixels)
                        self.assertFalse(output.getexif())

    def test_rgba_white_composite_and_all_buffers_closed(self):
        with Image.new('RGBA', (3, 1)) as source:
            source.putdata([(255, 0, 0, 0), (255, 0, 0, 128), (0, 0, 255, 255)])
            raw = encode(source)
        with tracked_images() as created:
            result = self.check(raw)
        self.assert_closed(created)
        with Image.open(io.BytesIO(result.data)) as output:
            self.assertEqual(output.mode, 'RGB')
            self.assertEqual(list(output.getdata()), [(255, 255, 255), (255, 127, 127), (0, 0, 255)])
            self.assertFalse(output.info)

    def test_palette_transparency_is_composited_and_closed(self):
        with Image.new('P', (3, 1)) as source:
            source.putpalette([0, 0, 0, 255, 0, 0] + [0] * 762)
            source.putdata([0, 1, 0])
            raw = encode(source, transparency=0)
        with tracked_images() as created:
            result = self.check(raw)
        self.assert_closed(created)
        with Image.open(io.BytesIO(result.data)) as output:
            self.assertEqual(list(output.getdata()), [(255, 255, 255), (255, 0, 0), (255, 255, 255)])

    def test_grayscale_conversion_is_closed(self):
        with Image.new('L', (3, 1), 67) as source:
            raw = encode(source)
        with tracked_images() as created:
            result = self.check(raw)
        self.assert_closed(created)
        with Image.open(io.BytesIO(result.data)) as output:
            self.assertEqual(output.getpixel((0, 0)), (67, 67, 67))

    def test_composite_and_encoding_failures_close_all_buffers(self):
        with Image.new('RGBA', (3, 1), (12, 34, 56, 128)) as source:
            exif = Image.Exif()
            exif[274] = 6
            raw = encode(source, exif=exif)
        for operation in ('paste', 'save'):
            with self.subTest(operation=operation):
                with tracked_images() as created, \
                     patch.object(Image.Image, operation, side_effect=OSError('injected failure')):
                    with self.assertRaises(UploadSafetyError):
                        self.check(raw)
                self.assert_closed(created)

    def test_validation_failure_closes_probe_without_decode(self):
        with Image.new('RGB', (3, 1)) as source:
            raw = encode(source)
        with tracked_images() as created, patch('app.upload_safety.MAX_PIXELS', 2):
            with self.assertRaises(UploadSafetyError):
                self.check(raw)
        self.assertEqual(len(created), 1)
        with self.assertRaises(ValueError):
            created[0].getpixel((0, 0))


if __name__ == '__main__':
    unittest.main()
