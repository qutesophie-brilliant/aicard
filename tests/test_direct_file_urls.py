import io
import unittest
from unittest.mock import patch

import httpx
import pymupdf
from PIL import Image

from app.pipeline import ingest, product_ocr
from app.pipeline.product_page import ProductBodyUnavailable


def image_bytes(fmt='PNG', size=(320, 4000)):
    output = io.BytesIO()
    with Image.new('RGB', size, 'white') as image:
        image.save(output, format=fmt)
    return output.getvalue()


def row(text, status='read'):
    return dict(text=text, status=status, engine='llm', attempts=1)


def response(data, content_type, url):
    return httpx.Response(200, content=data, headers={'content-type': content_type},
                          request=httpx.Request('GET', url))


class DirectFileUrlTests(unittest.TestCase):
    def test_long_jpg_uses_consumer_reader_and_retains_actual_source_url(self):
        url = 'https://example.com/detail.jpg?version=1'
        text = 'AI 자동온도 케어시스템\n온도 센서가 12도에서 냉각을 중지하고 17도에서 재가동합니다.'
        with patch.object(ingest, '_fetch', return_value=response(image_bytes('JPEG'), 'image/jpeg', url)), \
             patch.object(product_ocr, 'read_images', return_value=[row(text)]) as reader, \
             patch.object(ingest, 'from_image') as legacy:
            result = ingest.from_url(url, product_only=True)
        legacy.assert_not_called()
        with Image.open(io.BytesIO(reader.call_args.args[0][0])) as image:
            self.assertEqual((320, 4000), image.size)
        self.assertEqual(8, reader.call_args.kwargs['local_timeout_seconds'])
        self.assertTrue(reader.call_args.kwargs['recover_unconfirmed'])
        self.assertTrue(all(section['source_url'] == url for section in result.source_capture['sections']))
        self.assertEqual(url, result.source_capture['images'][0]['url'])
        self.assertEqual('read', result.source_capture['images'][0]['status'])
        from app.pipeline.run import quick_check
        card, docs = quick_check(result.title, url, result.text, source_capture=result.source_capture)
        self.assertTrue(card['claims'])
        self.assertTrue(all(doc['url'] == url for doc in docs.values()))

    def test_png_without_filename_extension_uses_response_url(self):
        actual = 'https://cdn.example.com/image?id=123'
        with patch.object(ingest, '_fetch', return_value=response(image_bytes(size=(30, 30)), 'image/png', actual)), \
             patch.object(product_ocr, 'read_images', return_value=[row('AI 기능은 온도를 감지하여 냉각을 조절합니다.')]):
            result = ingest.from_url('https://example.com/redirect', product_only=True)
        self.assertEqual(actual, result.source_capture['sections'][0]['source_url'])

    def test_partial_image_retains_status_and_warning(self):
        partial = dict(row('읽힌 제품 설명', 'partial'), tile_count=3, unconfirmed_tiles=1)
        url = 'https://example.com/detail.png'
        with patch.object(ingest, '_fetch', return_value=response(image_bytes(), 'image/png', url)), \
             patch.object(product_ocr, 'read_images', return_value=[partial]):
            result = ingest.from_url(url, product_only=True)
        self.assertEqual('partial', result.source_capture['images'][0]['status'])
        self.assertEqual(1, result.source_capture['coverage']['image_ocr_unconfirmed'])
        self.assertIn('일부 이미지 또는 구간', ' '.join(result.notes))

    def test_pdf_unread_page_does_not_shift_following_page_source(self):
        with pymupdf.open() as document:
            for _ in range(3):
                document.new_page(width=300, height=400)
            data = document.tobytes()
        url = 'https://example.com/manual.pdf?download=1'
        with patch.object(ingest, '_fetch', return_value=response(data, 'application/pdf', url)), \
             patch.object(product_ocr, 'read_images', return_value=[row('첫 번째 페이지의 AI 기능'), row('', 'no_text'), row('세 번째 페이지의 사용 설명')]) as reader, \
             patch.object(ingest, 'from_pdf') as legacy:
            result = ingest.from_url(url, product_only=True)
        legacy.assert_not_called()
        self.assertEqual(3, len(reader.call_args.args[0]))
        sections = result.source_capture['sections']
        self.assertEqual([0, 2], [section['upload_unit'] for section in sections])
        self.assertEqual([url + '#page=1', url + '#page=3'], [section['source_url'] for section in sections])
        self.assertEqual([], result.source_capture['images'])
        self.assertIn('일부 이미지 또는 구간', ' '.join(result.notes))
        from app.pipeline.run import quick_check
        _, docs = quick_check(result.title, url, result.text, source_capture=result.source_capture)
        self.assertEqual({url + '#page=1', url + '#page=3'}, {doc['url'] for doc in docs.values()})

    def test_long_pdf_is_clipped_and_reader_stops_after_twelve_pages(self):
        with pymupdf.open() as document:
            document.new_page(width=300, height=2200)
            for _ in range(12):
                document.new_page(width=300, height=400)
            data = document.tobytes()
        sizes = []
        def read(images, **kwargs):
            for payload in images:
                with Image.open(io.BytesIO(payload)) as image:
                    sizes.append(image.size)
            return [row(f'제품 설명 구간 {i}') for i in range(len(images))]
        url = 'https://example.com/manual.pdf'
        with patch.object(ingest, '_fetch', return_value=response(data, 'application/pdf', url)), \
             patch.object(product_ocr, 'read_images', side_effect=read):
            result = ingest.from_url(url, product_only=True)
        self.assertTrue(all(max(size) <= 1601 for size in sizes))
        self.assertGreater(len(sizes), 12)
        self.assertEqual(11, max(section['upload_unit'] for section in result.source_capture['sections']))
        self.assertIn('12쪽까지만', ' '.join(result.notes))

    def test_pdf_with_embedded_script_is_rejected_before_ocr(self):
        with pymupdf.open() as document:
            document.new_page()
            xref = document.get_new_xref()
            document.update_object(xref, '<< /S /JavaScript /JS (app.alert) >>')
            document.xref_set_key(document.pdf_catalog(), 'OpenAction', f'{xref} 0 R')
            data = document.tobytes()
        url = 'https://example.com/manual.pdf'
        with patch.object(ingest, '_fetch', return_value=response(data, 'application/pdf', url)), \
             patch.object(product_ocr, 'read_images') as reader:
            with self.assertRaises(ProductBodyUnavailable):
                ingest.from_url(url, product_only=True)
        reader.assert_not_called()

    def test_empty_ocr_result_keeps_failure_explanation(self):
        failed = dict(row('', 'provider_error'), provider_failure='provider_http_429')
        url = 'https://example.com/detail.png'
        with patch.object(ingest, '_fetch', return_value=response(image_bytes(size=(30, 30)), 'image/png', url)), \
             patch.object(product_ocr, 'read_images', return_value=[failed]):
            with self.assertRaisesRegex(ProductBodyUnavailable, '요청량 제한'):
                ingest.from_url(url, product_only=True)

    def test_ocr_disabled_never_calls_image_reader(self):
        url = 'https://example.com/detail.png'
        with patch.object(ingest, '_fetch', return_value=response(image_bytes(size=(30, 30)), 'image/png', url)), \
             patch.object(product_ocr, 'read_images') as reader:
            with self.assertRaisesRegex(ProductBodyUnavailable, '읽지 않았습니다'):
                ingest.from_url(url, product_only=True, ocr_images=False)
        reader.assert_not_called()

    def test_formal_image_and_pdf_paths_remain_unchanged(self):
        for content_type, method, suffix in [('image/png', 'from_image', 'png'), ('application/pdf', 'from_pdf', 'pdf')]:
            url = 'https://example.com/source.' + suffix
            expected = ingest.Ingested('formal', 'original', ['original'])
            with patch.object(ingest, '_fetch', return_value=response(b'original', content_type, url)), \
                 patch.object(ingest, method, return_value=expected) as legacy, \
                 patch.object(product_ocr, 'read_images') as reader:
                self.assertIs(expected, ingest.from_url(url, product_only=False))
            legacy.assert_called_once()
            reader.assert_not_called()


if __name__ == '__main__':
    unittest.main()
