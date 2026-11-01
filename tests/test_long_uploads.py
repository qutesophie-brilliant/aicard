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
                product_ocr.read_images([picture((40, 65000))], allow_llm=False)
        reader.assert_not_called()

    def test_retail_sheet_57288_pixels_keeps_first_and_last_tiles(self):
        # Match the height of a real Sony overview, without allocating its
        # entire 68 MP fixture in the regression suite.
        raw = picture((40, 57288))
        tiles = image_tiles.split_image(raw)
        self.assertEqual(39, len(tiles))
        with Image.open(io.BytesIO(tiles[0])) as first, Image.open(io.BytesIO(tiles[-1])) as last:
            self.assertEqual((255, 0, 0), first.getpixel((10, 10)))
            self.assertEqual((0, 0, 255), last.getpixel((10, last.height - 1)))
        def reader(batch, *args, **kwargs):
            return [reading('visible text') for _ in batch]
        with patch.object(product_ocr, '_read_images', side_effect=reader):
            rows = product_ocr.read_images([raw], allow_llm=False)
        self.assertEqual(39, rows[0]['tile_count'])
        self.assertEqual(0, rows[0]['unconfirmed_tiles'])
        self.assertEqual('read', rows[0]['status'])

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

    def test_combined_pixels_no_longer_reject_valid_individual_files(self):
        raw = picture((20, 30))
        payloads = [('first.png', 'image/png', raw), ('second.png', 'image/png', raw)]
        sizes = []
        def read(images, **kwargs):
            sizes.append(len(images))
            return ocr.OcrResult(texts=['first', 'second'], engine='test')
        with patch.object(upload_safety, 'MAX_PIXELS', 1000), \
             patch.object(main, '_read_upload_images', side_effect=read):
            texts, _ = main._read_check_uploads(payloads)
        self.assertEqual(['first', 'second'], texts)
        self.assertEqual([2], sizes)

    def test_per_file_pixel_limit_still_rejects_before_decode(self):
        raw = picture((20, 60))
        with patch.object(upload_safety, 'MAX_PIXELS', 1000), \
             patch.object(main, 'check_upload') as decode, \
             patch.object(main, '_read_upload_images') as reader:
            with self.assertRaises(upload_safety.UploadSafetyError):
                main._read_check_uploads([('large.png', 'image/png', raw)])
        decode.assert_not_called()
        reader.assert_not_called()

    def test_long_files_are_sequential_and_mixed_pdf_order_is_preserved(self):
        with pymupdf.open() as doc:
            doc.new_page().insert_text((50, 50), 'PDF middle')
            pdf = doc.tobytes()
        payloads = [('a.png', 'image/png', picture()), ('b.pdf', 'application/pdf', pdf),
                    ('c.png', 'image/png', picture()), ('d.png', 'image/png', picture())]
        sizes = []
        def reader(images, **kwargs):
            sizes.append(len(images))
            return ocr.OcrResult(texts=['image ' + str(len(sizes))], engine='test', notes=['확인 대상 이미지 1장 중 1장에서 글자를 읽었습니다.'])
        with patch.object(main, '_read_upload_images', side_effect=reader), \
             patch.object(ocr, 'read_pdf', return_value=ocr.OcrResult(texts=['PDF middle'], engine='pdf')):
            texts, notes = main._read_check_uploads(payloads)
        self.assertEqual(3, len([n for n in notes if '확인 대상 이미지' in n]))
        self.assertTrue(any(n.startswith('파일 4:') for n in notes))
        self.assertEqual([1, 1, 1], sizes)
        self.assertEqual('image 1', texts[0])
        self.assertIn('PDF middle', texts[1])
        self.assertEqual(['image 2', 'image 3'], texts[2:])



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
