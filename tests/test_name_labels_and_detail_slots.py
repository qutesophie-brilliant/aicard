import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bs4 import BeautifulSoup
from PIL import Image
from fastapi.testclient import TestClient
from app import db, main
from app.pipeline import ingest, marketplace_documents, product_images, ocr, run, source_capture


class ProductNameLabelTests(unittest.TestCase):
    url = 'https://www.e-himart.co.kr/app/goods/goodsDetail?goodsNo=0071621000'
    name = '코드제로 AI 로보킹 올인원 로봇청소기 B94AHB'

    def html(self):
        return (f'<meta property="eg:itemId" content="0071621000">'
                f'<meta property="eg:itemName" content="{self.name}">'
                f'<meta property="og:description" content="{self.name}">'
                f'<div class="hm-goods-name">{self.name}</div>'
                '<div class="product-description"><p>스마트 터보: 먼지와 구석, 카펫 감지</p><img src="/detail.jpg"></div>')

    def test_verified_ai_name_is_separate_from_function_claims(self):
        capture = source_capture.capture_product_html(self.html(), self.url)
        card, docs = run.quick_check(self.name, self.url, capture['text'], source_capture=capture)
        self.assertFalse(card['claims'])
        self.assertEqual(1, len(card['quick_name_labels']))
        label = card['quick_name_labels'][0]
        self.assertEqual(self.name, label['quote'])
        self.assertEqual(self.url, docs[label['doc_id']]['url'])

    def test_input_name_alone_never_becomes_verified_label(self):
        card, _ = run.quick_check(self.name, self.url, '스마트 터보: 먼지와 구석, 카펫 감지')
        self.assertFalse(card['quick_name_labels'])

    def test_page_distinguishes_product_label_and_absent_function_wording(self):
        result = ingest.from_product_html(self.html(), self.url)
        with tempfile.TemporaryDirectory() as d, patch.object(db, 'DB_PATH', Path(d)/'scratch.sqlite3'):
            db.init_db()
            with patch.object(main, '_read_check_url', return_value=result), patch.object(main, '_rate_ok', return_value=True):
                page = TestClient(main.app).post('/check', data={'url': self.url, 'fresh': 'true'})
        self.assertEqual(200, page.status_code)
        soup = BeautifulSoup(page.text, 'html.parser')
        self.assertIn('상품명에서 ‘AI’ 표시를 확인했습니다', soup.get_text())
        self.assertNotIn('읽어 온 자료에서 ‘AI’라고 표시한 문구를 찾지 못했습니다', soup.get_text())
        self.assertEqual(self.name, soup.select_one('.product-name-label q').get_text())
        self.assertFalse(soup.select('.claims li[id^="qc-"]'))


class Cafe24HeadingTests(unittest.TestCase):
    url = 'https://i-nic.co.kr/surl/P/133/product/list.html?cate_no=185'
    canonical = 'https://i-nic.co.kr/product/아이닉-가습기/133/'
    name = '아이닉 AI습도조절 UV살균 복합식 가습기 iH12 Pro'

    def html(self):
        product = {'@type': 'Product', 'name': self.name, 'sku': 'cafe24_inicinc_1_133',
                   'offers': {'@type': 'Offer', 'url': self.canonical}}
        return (f'<link rel="canonical" href="{self.canonical}"><meta property="og:title" content="{self.name}">'
                '<script type="application/ld+json">' + json.dumps(product) + '</script>'
                f'<div class="xans-product-detail"><div class="headingArea"><h1>{self.name}</h1></div></div>'
                '<div class="product-description"><img src="/detail.jpg"></div>')

    def test_identity_verified_functional_name_is_not_lost(self):
        capture = source_capture.capture_product_html(self.html(), self.url)
        card, _ = run.quick_check(self.name, self.url, capture['text'], source_capture=capture)
        self.assertTrue(card['claims'])
        self.assertTrue(any('AI습도조절' in c['text'] for c in card['claims']))
        self.assertTrue(all(not c['evidence'] for c in card['claims']))

    def test_mismatched_product_origin_name_or_ambiguous_schema_rejected(self):
        for html in (self.html().replace('cafe24_inicinc_1_133', 'cafe24_inicinc_1_999'),
                     self.html().replace(self.canonical, self.canonical.replace('i-nic.co.kr', 'other.example')),
                     self.html().replace(f'<h1>{self.name}</h1>', '<h1>다른 AI 제품</h1>'),
                     self.html() + '<script type="application/ld+json">{"@type":"Product"}</script>'):
            with self.subTest(html=html):
                self.assertEqual('', marketplace_documents.product_heading(BeautifulSoup(html, 'html.parser'), self.url))


class EmptyImageSlotTests(unittest.TestCase):
    url = 'https://example.com/product/example'

    def image(self, height):
        out = io.BytesIO()
        Image.new('RGB', (860, height), 'white').save(out, format='PNG')
        return out.getvalue()

    def test_empty_slot_replaced_without_increasing_ocr_count(self):
        html = '<div class="product-description">'+''.join(f'<img src="/{i}.jpg">' for i in range(4))+'</div>'
        capture = source_capture.capture_product_html(html, self.url)
        seen = []
        def read(image):
            seen.append(image)
            return ocr.OcrResult(texts=['AI 습도 조절'], engine='test')
        product_images.capture_images(html, self.url, capture,
            fetch_bytes=lambda url: self.image(1 if url.endswith('0.jpg') else 40), reader=read, limit=2)
        self.assertEqual(['no_readable_area', 'read', 'read', 'skipped_limit'], [r['status'] for r in capture['images']])
        self.assertEqual(2, len(seen))
        self.assertEqual(1, capture['coverage']['image_ignored_separator'])

    def test_all_empty_slots_have_bounded_downloads_and_no_ocr(self):
        html = '<div class="product-description">'+''.join(f'<img src="/{i}.jpg">' for i in range(10))+'</div>'
        capture = source_capture.capture_product_html(html, self.url)
        with patch.object(ocr, 'read_images') as read:
            product_images.capture_images(html, self.url, capture, fetch_bytes=lambda _: self.image(1), limit=2)
        read.assert_not_called()
        self.assertEqual(4, capture['coverage']['image_attempted'])
        self.assertEqual(6, capture['coverage']['image_skipped_limit'])


if __name__ == '__main__':
    unittest.main()
