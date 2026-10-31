import io
import unittest
from unittest.mock import patch

import pymupdf
from PIL import Image, ImageDraw, PngImagePlugin

from app.pipeline import image_tiles, ocr, product_ocr
from app.upload_safety import check_upload
from app import main, upload_safety


def picture(size=(320, 4000), metadata=False):
    image = Image.new('RGB', size, 'white')
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, size[0] - 1, 99), fill='red')
    draw.rectangle((0, size[1] // 2, size[0] - 1, size[1] // 2 + 99), fill='green')
    draw.rectangle((0, size[1] - 100, size[0] - 1, size[1] - 1), fill='blue')
    info = PngImagePlugin.PngInfo()
    if metadata:
        info.add_text('Comment', 'private source metadata')
    out = io.BytesIO()
    image.save(out, format='PNG', pnginfo=info)
    image.close()
    return out.getvalue()


def reading(text, status=None):
    return {'text': text, 'engine': 'test', 'status': status or ('read' if text else 'no_text'), 'attempts': 1}


class LongImageTests(unittest.TestCase):
    def test_sanitization_preserves_long_image_width_and_strips_metadata(self):
        raw = picture(metadata=True) + b'private trailing data'
        result = check_upload('detail.png', 'image/png', raw, max_bytes=8 * 1024 * 1024)
        self.assertNotIn(b'private', result.data)
        with Image.open(io.BytesIO(result.data)) as image:
            self.assertEqual((320, 4000), image.size)
            self.assertFalse(image.info)
            self.assertEqual((255, 0, 0), image.getpixel((10, 10)))
            self.assertEqual((0, 0, 255), image.getpixel((10, 3999)))

    def test_tiles_cover_beginning_middle_end_without_downscaling_letters(self):
        tiles = image_tiles.split_image(picture())
        self.assertEqual(3, len(tiles))
        images = [Image.open(io.BytesIO(data)) for data in tiles]
        try:
            self.assertTrue(all(image.width == 320 and image.height <= 1600 for image in images))
            self.assertEqual((255, 0, 0), images[0].getpixel((10, 10)))
            self.assertEqual((0, 128, 0), images[1].getpixel((10, 2000 - 1480)))
            self.assertEqual((0, 0, 255), images[-1].getpixel((10, images[-1].height - 1)))
        finally:
            for image in images:
                image.close()

    def test_landscape_tiles_preserve_final_content(self):
        raw = picture((4000, 320))
        tiles = image_tiles.split_image(raw)
        self.assertEqual(3, len(tiles))
        with Image.open(io.BytesIO(tiles[-1])) as image:
            self.assertEqual((1040, 320), image.size)
            self.assertEqual((0, 0, 255), image.getpixel((1039, 319)))

    def test_partial_long_image_does_not_shift_next_original_source(self):
        inputs = [picture(), picture((30, 30))]
        rows = [reading('first source beginning'), reading(''), reading('first source end'), reading('second source')]
        with patch.object(product_ocr, '_read_images', return_value=rows) as reader:
            result = product_ocr.read_images(inputs, allow_llm=False)
        self.assertEqual(2, len(result))
        self.assertEqual('first source beginning\nfirst source end', result[0]['text'])
        self.assertEqual('partial', result[0]['status'])
        self.assertEqual(3, result[0]['tile_count'])
        self.assertEqual(1, result[0]['unconfirmed_tiles'])
        self.assertEqual('second source', result[1]['text'])
        self.assertEqual('read', result[1]['status'])
        self.assertEqual(4, len(reader.call_args.args[0]))

    def test_ordinary_images_keep_existing_reader_and_order(self):
        inputs = [picture((20, 30)), picture((20, 40))]
        expected = [reading('first'), reading('second')]
        with patch.object(product_ocr, '_read_images', return_value=expected) as reader:
            self.assertIs(expected, product_ocr.read_images(inputs, allow_llm=False))
        self.assertEqual(inputs, reader.call_args.args[0])

    def test_excessive_length_fails_before_ocr_instead_of_dropping_tail(self):
        with patch.object(product_ocr, '_read_images') as reader:
            with self.assertRaisesRegex(ValueError, '나누어'):
                product_ocr.read_images([picture((40, 40000))], allow_llm=False)
        reader.assert_not_called()

    def test_only_exact_overlap_lines_are_removed(self):
        self.assertEqual('처음\n조건 6kg\n마지막', image_tiles.join_texts(['처음\n조건 6kg', '조건 6kg\n마지막']))
        self.assertEqual('조건 6kg\n조건 8kg', image_tiles.join_texts(['조건 6kg', '조건 8kg']))

    def test_seven_small_images_share_one_deadline_and_keep_all_sources(self):
        inputs = [picture((20, 30)) for _ in range(7)]
        calls = []
        def reader(images, *args, **kwargs):
            start = sum(size for size, _ in calls)
            calls.append((len(images), kwargs['deadline']))
            return [reading(f'source {start + i}') for i in range(len(images))]
        with patch.object(product_ocr, '_read_images', side_effect=reader):
            rows = product_ocr.read_images(inputs, allow_llm=False)
        self.assertEqual([4, 3], [size for size, _ in calls])
        self.assertEqual(calls[0][1], calls[1][1])
        self.assertEqual([f'source {i}' for i in range(7)], [row['text'] for row in rows])
        self.assertTrue(all(row['tile_count'] == 1 for row in rows))

    def test_pdf_edge_rounding_does_not_create_a_second_tile(self):
        raw = picture((400, 1601))
        self.assertEqual([raw], image_tiles.split_image(raw))
        with Image.open(io.BytesIO(ocr._shrink(raw))) as image:
            self.assertEqual(1600, max(image.size))

    def test_combined_pixel_budget_rejects_before_decode_or_ocr(self):
        raw = picture((20, 30))
        payloads = [('first.png', 'image/png', raw), ('second.png', 'image/png', raw)]
        with patch.object(upload_safety, 'MAX_PIXELS', 1000), \
             patch.object(main, 'check_upload') as decode, \
             patch.object(main, '_read_upload_images') as reader, \
             patch.object(Image.Image, 'load', side_effect=AssertionError('decoded too early')):
            with self.assertRaisesRegex(upload_safety.UploadSafetyError, '전체 해상도'):
                main._read_check_uploads(payloads)
        decode.assert_not_called()
        reader.assert_not_called()


class LongPdfTests(unittest.TestCase):
    def test_long_scan_clips_are_bounded_and_rejoined_before_next_page(self):
        with pymupdf.open() as document:
            document.new_page(width=352, height=4000)
            document.new_page(width=352, height=400)
            raw = document.tobytes()
        sizes = []
        def reader(images, allow_llm):
            for data in images:
                with Image.open(io.BytesIO(data)) as image:
                    sizes.append(image.size)
            return ocr.OcrResult(texts=[f'tile {i}' for i in range(len(images))], engine='test')
        result = ocr.read_pdf(raw, allow_llm=False, image_reader=reader)
        self.assertEqual(2, len(result.texts))
        self.assertGreater(len(sizes), 2)
        self.assertTrue(all(max(size) <= ocr.MAX_SIDE + 1 for size in sizes), sizes)
        self.assertGreater(min(sizes[0]), 600)
        self.assertEqual(f'tile {len(sizes) - 1}', result.texts[1])
        self.assertIn(f'tile {len(sizes) - 2}', result.texts[0])
        self.assertNotIn(result.texts[1], result.texts[0])

    def test_excessive_pdf_length_fails_before_external_ocr(self):
        with pymupdf.open() as document:
            document.new_page(width=352, height=40000)
            raw = document.tobytes()
        with patch.object(ocr, 'read_images') as reader:
            with self.assertRaisesRegex(ValueError, '나누어'):
                ocr.read_pdf(raw, allow_llm=False)
        reader.assert_not_called()


if __name__ == '__main__':
    unittest.main()
