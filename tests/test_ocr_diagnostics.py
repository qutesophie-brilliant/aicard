import io
import unittest
from unittest.mock import patch

from PIL import Image

from app.pipeline import image_tiles, ingest, llm, ocr, product_images, product_ocr


class OcrDiagnosticTests(unittest.TestCase):
    def test_sdk_status_code_is_used_without_message_or_body(self):
        class SDKError(Exception):
            status_code = 429
        self.assertEqual('provider_http_429', product_ocr._provider_failure(SDKError('private body')))
        SDKError.status_code = 503
        self.assertEqual('provider_http_5xx', product_ocr._provider_failure(SDKError('private body')))
        SDKError.status_code = '429 private'
        self.assertEqual('provider_error', product_ocr._provider_failure(SDKError('private body')))

    def test_builtin_failure_families_are_distinct(self):
        for kind, expected in [
            (ImportError, 'dependency'), (ModuleNotFoundError, 'dependency'), (MemoryError, 'memory'),
            (TypeError, 'type'), (ValueError, 'value'), (AttributeError, 'attribute'),
            (NameError, 'name'), (UnboundLocalError, 'name'), (KeyError, 'lookup'),
            (IndexError, 'lookup'), (AssertionError, 'assertion'), (RuntimeError, 'runtime'), (OSError, 'system'),
        ]:
            with self.subTest(kind=kind.__name__):
                self.assertEqual('provider_' + expected, product_ocr._provider_failure(kind('private value')))

    def test_support_code_contains_only_provider_class_and_our_line(self):
        secret = 'private key address body'
        with patch.object(llm, 'provider', return_value='gemini'), \
             patch.object(llm, 'available', return_value=True), \
             patch.object(llm, 'ocr_images_indexed', side_effect=TypeError(secret)), \
             patch.object(ocr, '_shrink', side_effect=lambda data: data), \
             patch.object(ocr, 'read_images', return_value=ocr.OcrResult(texts=['local text'], engine='tesseract')), \
             self.assertLogs('uvicorn.error', level='WARNING') as logs:
            rows = product_ocr._read_images([b'image'])
        code = rows[0]['provider_diagnostics'][0]
        self.assertRegex(code, r'^gemini/TypeError/product_ocr:[1-9][0-9]*$')
        notes = ' '.join(product_ocr.provider_failure_notes(rows))
        self.assertIn('오류 확인 코드: ' + code, notes)
        for public_value in [code, notes, ' '.join(logs.output)]:
            self.assertNotIn(secret, public_value)
            self.assertNotIn('C:', public_value)
            self.assertNotIn('/Users/', public_value)

    def test_malformed_diagnostic_values_are_rejected_and_only_one_code_is_shown(self):
        notes = product_ocr.provider_failure_notes([{
            'provider_failure': 'provider_type',
            'provider_diagnostics': ['gemini/TypeError/llm:23', 'openai/ValueError/llm:42',
                                     'gemini/TypeError/C:/Users/private', 'secret key', 'gemini/TypeError/llm:23\nsecret'],
        }])
        codes = [note for note in notes if note.startswith('오류 확인 코드:')]
        self.assertEqual(['오류 확인 코드: gemini/TypeError/llm:23.'], codes)

    def test_unrecognized_provider_and_class_do_not_leak_arbitrary_strings(self):
        OddError = type('private\nkey', (Exception,), {})
        with patch.object(llm, 'provider', return_value='private-provider-value'):
            self.assertEqual('unknown/OtherError/external', product_ocr._provider_diagnostic(OddError('private body')))

    def test_long_image_and_url_path_keep_the_safe_support_code(self):
        rows = [{'text': 'local text', 'engine': 'tesseract', 'status': 'read', 'attempts': 2,
                 'provider_failure': 'provider_type', 'provider_diagnostics': ['gemini/TypeError/llm:23']}]
        with patch.object(image_tiles, 'split_image', return_value=[b'a', b'b']), \
             patch.object(product_ocr, '_read_images', return_value=rows * 2):
            merged = product_ocr.read_images([b'long'])
        self.assertEqual(['gemini/TypeError/llm:23'], merged[0]['provider_diagnostics'])

        stream = io.BytesIO()
        Image.new('RGB', (20, 20), 'white').save(stream, format='PNG')
        html = '<div class="product-description"><p>상품 설명</p><img src="/detail.png"></div>'
        real_capture = product_images.capture_images
        def capture(html, url, data):
            return real_capture(html, url, data, fetch_bytes=lambda _: stream.getvalue())
        with patch.object(product_images, 'capture_images', side_effect=capture), \
             patch.object(product_ocr, 'read_images', return_value=merged):
            result = ingest.from_product_html(html, 'https://example.com/product', ocr_images=True)
        self.assertIn('오류 확인 코드: gemini/TypeError/llm:23.', result.notes)


if __name__ == '__main__':
    unittest.main()
