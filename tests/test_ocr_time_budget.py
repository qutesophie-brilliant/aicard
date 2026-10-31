import io
import unittest
from unittest.mock import patch

import httpx
from PIL import Image

from app import main
from app.pipeline import image_tiles, ingest, llm, ocr, product_images, product_ocr


def reading(text='', **extra):
    return dict(text=text, engine='llm', status='read' if text else 'provider_error', attempts=1, **extra)


class OcrTimeBudgetTests(unittest.TestCase):
    def test_small_input_keeps_28_second_initial_call_and_42_second_budget(self):
        clock = [100.0]
        def read(*args, **kwargs):
            self.assertEqual(28, kwargs['timeout_seconds'])
            clock[0] = 142.0
            return [None]
        with patch.object(image_tiles, 'split_image', return_value=[b'a']), \
             patch.object(ocr, '_shrink', side_effect=lambda data: data), \
             patch.object(llm, 'available', return_value=True), \
             patch.object(product_ocr.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(llm, 'ocr_images_indexed', side_effect=read) as model:
            rows = product_ocr.read_images([b'a'])
        self.assertEqual(1, model.call_count)
        self.assertEqual('time_budget', rows[0]['retry_skipped'])

    def test_long_image_gets_50_second_call_and_recovery_after_old_deadline(self):
        clock = [100.0]
        calls = []
        def read(images, **kwargs):
            calls.append((len(images), kwargs['timeout_seconds']))
            clock[0] = 151.0
            return [None] * len(images)
        with patch.object(image_tiles, 'split_image', return_value=[b'a', b'b', b'c']), \
             patch.object(ocr, '_shrink', side_effect=lambda data: data), \
             patch.object(llm, 'available', return_value=True), \
             patch.object(product_ocr.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(llm, 'ocr_images_indexed', side_effect=read), \
             patch.object(ocr, 'read_images', return_value=ocr.OcrResult(texts=['recovered'], engine='tesseract')) as local:
            rows = product_ocr.read_images([b'long'], recover_unconfirmed=True)
        self.assertEqual([(3, 50), (1, 12)], calls)
        self.assertEqual(2, local.call_count)
        self.assertEqual('partial', rows[0]['status'])
        self.assertTrue(all(call.kwargs['local_timeout_seconds'] <= 8 for call in local.call_args_list))

    def test_twenty_tiles_keep_five_initial_calls_and_one_shared_75_second_budget(self):
        calls = []
        def read(images, *args, **kwargs):
            calls.append((len(images), kwargs['deadline'], kwargs['initial_llm_timeout']))
            return [reading(data.decode()) for data in images]
        with patch.object(image_tiles, 'split_image', return_value=[str(i).encode() for i in range(20)]), \
             patch.object(llm, 'available', return_value=True), \
             patch.object(product_ocr.time, 'monotonic', return_value=100.0), \
             patch.object(product_ocr, '_read_images', side_effect=read):
            rows = product_ocr.read_images([b'long'])
        self.assertEqual([(4, 175.0, 50)] * 5, calls)
        self.assertEqual('\n'.join(str(i) for i in range(20)), rows[0]['text'])

    def test_http_failures_are_allowlisted_without_error_message_content(self):
        for code in (400, 401, 403, 404, 429, 500, 503, 599):
            expected = 'provider_http_' + ('5xx' if code >= 500 else str(code))
            self.assertEqual(expected, product_ocr._provider_failure(llm.LLMError(f'OCR provider HTTP {code}')))
        self.assertEqual('provider_runtime', product_ocr._provider_failure(RuntimeError('OCR provider HTTP 429')))
        for exc in (llm.LLMError('OCR provider HTTP 418'), llm.LLMError('OCR provider HTTP 429 secret-response')):
            self.assertEqual('provider_error', product_ocr._provider_failure(exc))
        self.assertEqual('timeout', product_ocr._provider_failure(httpx.ReadTimeout('secret-response')))

    def test_provider_http_failure_keeps_compatible_status_and_one_paid_call(self):
        with patch.object(ocr, '_shrink', side_effect=lambda data: data), \
             patch.object(llm, 'available', return_value=True), \
             patch.object(llm, 'ocr_images_indexed', side_effect=llm.LLMError('OCR provider HTTP 429')) as model, \
             patch.object(ocr, 'read_images', return_value=ocr.OcrResult(texts=['local text'], engine='tesseract')):
            rows = product_ocr._read_images([b'a'])
        self.assertEqual(1, model.call_count)
        self.assertEqual('read', rows[0]['status'])
        self.assertEqual('provider_error', rows[0]['initial_status'])
        self.assertEqual('provider_http_429', rows[0]['provider_failure'])

    def test_retry_timeout_keeps_two_paid_call_limit(self):
        with patch.object(ocr, '_shrink', side_effect=lambda data: data), \
             patch.object(llm, 'available', return_value=True), \
             patch.object(llm, 'ocr_images_indexed', side_effect=[[None], httpx.ReadTimeout('private')]) as model:
            rows = product_ocr._read_images([b'a'])
        self.assertEqual(2, model.call_count)
        self.assertEqual('timeout', rows[0]['status'])
        self.assertEqual('timeout', rows[0]['provider_failure'])

    def test_long_image_merges_failure_reasons_without_losing_source_text(self):
        parts = [reading('first', provider_failure='provider_http_429'),
                 reading('', provider_failure='timeout'), reading('last')]
        with patch.object(image_tiles, 'split_image', return_value=[b'a', b'b', b'c']), \
             patch.object(product_ocr, '_read_images', return_value=parts):
            rows = product_ocr.read_images([b'long'])
        self.assertEqual('first\nlast', rows[0]['text'])
        self.assertEqual(['provider_http_429', 'timeout'], rows[0]['provider_failures'])
        self.assertEqual(2, len(product_ocr.provider_failure_notes(rows)))

    def test_upload_notes_and_empty_upload_error_distinguish_timeout_and_rate_limit(self):
        with patch.object(product_ocr, 'read_images', return_value=[reading('local text', provider_failure='provider_http_429')]):
            result = main._read_upload_images([b'a'])
        self.assertIn('요청량 제한', ' '.join(result.notes))
        with patch.object(product_ocr, 'read_images', return_value=[reading('', provider_failure='timeout')]):
            with self.assertRaisesRegex(ValueError, '응답이 늦어져'):
                main._read_upload_images([b'a'])

    def test_url_capture_carries_safe_reason_into_consumer_notes(self):
        stream = io.BytesIO()
        Image.new('RGB', (20, 20), 'white').save(stream, format='PNG')
        html = '<div class="product-description"><p>상품 설명</p><img src="/detail.png"></div>'
        real_capture = product_images.capture_images
        def capture(html, url, data):
            return real_capture(html, url, data, fetch_bytes=lambda _: stream.getvalue())
        with patch.object(product_images, 'capture_images', side_effect=capture), \
             patch.object(product_ocr, 'read_images', return_value=[reading('actual text', provider_failures=['timeout'])]):
            result = ingest.from_product_html(html, 'https://example.com/product', ocr_images=True)
        self.assertIn('응답이 늦어져', ' '.join(result.notes))
        self.assertEqual(['timeout'], result.source_capture['images'][0]['provider_failures'])

    def test_notes_ignore_unknown_reason_strings(self):
        self.assertEqual([], product_ocr.provider_failure_notes([
            {'provider_failure': 'secret-response', 'provider_failures': ['https://private.example?key=secret']}]))


if __name__ == '__main__':
    unittest.main()
