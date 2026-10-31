import hashlib
import io
import unittest
from unittest.mock import patch

import httpx
from PIL import Image

from app.pipeline import cjonstyle, ingest, product_images, product_ocr
from app.pipeline.ocr import OcrResult
from app.pipeline.product_page import ProductBodyUnavailable


PAGE = 'https://display.cjonstyle.com/p/item/2074044916?srsltid=test'
API = 'https://display.cjonstyle.com/c/rest/item/2074044916/explainAreaInfo?channelCode=30002002&isMyzone=false&isEmployee=false'
IMAGE = 'https://image.cjonstyle.net/cjupload/htmledit/ven_img/example.jpg'


def response(payload):
    return httpx.Response(200, json=payload, request=httpx.Request('GET', API))


class CjOnStyleIngestTests(unittest.TestCase):
    def test_known_page_only_and_numeric_product_identity(self):
        self.assertEqual(API, cjonstyle.detail_api_url(PAGE))
        self.assertEqual(API, cjonstyle.detail_api_url(PAGE.split('?')[0] + '/itemExplainAreaInfo'))
        for value in ('https://evil.test/p/item/2074044916',
                      'https://display.cjonstyle.com.evil.test/p/item/2074044916',
                      'https://display.cjonstyle.com/p/item/../2074044916',
                      'https://display.cjonstyle.com/p/item/not-numeric'):
            self.assertIsNone(cjonstyle.detail_api_url(value))

    def test_dynamic_page_reads_public_detail_fields_and_retains_provenance(self):
        detail = response({'code': 1, 'result': {
            'displayItemName': '시험용 AI 제품',
            'itemSpecHtmlInfo': [{'html': '<html><head><title>배너</title></head><body><p>AI가 사용자의 설정을 분석하여 온도를 조절합니다.</p></body></html>'}],
            'itemSpecTableInfo1': [{'html': '<p>이 기능을 사용하려면 Wi-Fi 연결과 계정 등록이 필요합니다.</p>'}],
            'itemSpecTableInfo2': [],
            'textPromotions': [{'html': 'AI 추천상품 할인'}],
            'itemImages': ['https://evil.test/never-fetch.jpg'],
        }})
        shell = httpx.Response(200, text='<title>CJ</title><div>불러오는 중</div>',
                               request=httpx.Request('GET', PAGE))
        with patch.object(ingest, '_fetch', return_value=shell), \
             patch('app.network_safety.fetch_public', return_value=detail) as fetch:
            result = ingest.from_url(PAGE, ocr_images=False, product_only=True)
        fetch.assert_called_once()
        self.assertEqual(API, fetch.call_args.args[0])
        self.assertEqual({'display.cjonstyle.com'}, fetch.call_args.kwargs['allowed_hosts'])
        self.assertTrue(fetch.call_args.kwargs['https_only'])
        self.assertEqual('시험용 AI 제품', result.title)
        self.assertIn('Wi-Fi 연결', result.text)
        self.assertNotIn('할인', result.text)
        self.assertNotIn('배너', result.text)
        digest = hashlib.sha256(detail.content).hexdigest()
        self.assertEqual(PAGE, result.source_capture['source_page_url'])
        for section in result.source_capture['sections']:
            self.assertEqual(API, section['source_url'])
            self.assertEqual(digest, section['snapshot_sha256'])

    def test_unavailable_or_other_product_details_do_not_become_findings(self):
        for payload in ({'code': 0, 'result': {}}, {'code': 1, 'result': {
            'itemSpecHtmlInfo': [{'html': '<p>AI가 영상을 분석하여 화질을 조절합니다.</p>',
                                  'texplainEntity': {'itemCd': 'OTHER'}}],
        }}):
            with patch('app.network_safety.fetch_public', return_value=response(payload)):
                with self.assertRaises(ProductBodyUnavailable):
                    cjonstyle.read_product(PAGE, ocr_images=False)

    def test_cj_image_hosts_are_scoped_and_http_is_upgraded(self):
        data = io.BytesIO()
        Image.new('RGB', (50, 50), 'white').save(data, format='PNG')
        html = ('<div class="product-description"><img src="' + IMAGE.replace('https:', 'http:')
                + '"><img src="https://evil.test/unrelated.jpg"></div>')
        downloaded = []
        def download(url):
            downloaded.append(url)
            return data.getvalue()
        for page, expected in ((API, [IMAGE]), ('https://example.test/item', [])):
            downloaded.clear()
            capture = {'sections': [], 'coverage': {}}
            product_images.capture_images(html, page, capture, fetch_bytes=download,
                                          reader=lambda _: OcrResult(texts=['AI 기능 설명을 읽었습니다.'], engine='test'))
            self.assertEqual(expected, downloaded)
        self.assertEqual([], capture['images'])

    def test_partial_long_image_is_not_reported_as_complete(self):
        data = io.BytesIO()
        Image.new('RGB', (50, 50), 'white').save(data, format='PNG')
        capture = {'sections': [], 'coverage': {}}
        rows = [{'text': 'AI가 온도를 조절합니다.', 'engine': 'llm', 'status': 'partial',
                 'attempts': 1, 'tile_count': 5, 'unconfirmed_tiles': 2}]
        with patch.object(product_ocr, 'read_images', return_value=rows):
            product_images.capture_images('<div class="product-description"><img src="' + IMAGE + '"></div>',
                                          API, capture, fetch_bytes=lambda _: data.getvalue())
        self.assertEqual(1, capture['coverage']['image_ocr_unconfirmed'])
        self.assertEqual(2, capture['images'][0]['unconfirmed_tiles'])
        self.assertEqual(5, capture['images'][0]['tile_count'])


if __name__ == '__main__':
    unittest.main()
