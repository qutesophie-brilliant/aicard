import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.pipeline import product_page, product_images, page_recovery


class OfficialProductModulesTests(unittest.TestCase):
    def test_shopify_image_only_detail_reaches_ocr_without_browser(self):
        html = '''<body class="template-product"><header><img src="/other.jpg"></header>
        <div class="product-media-and-text"><div class="list-item-banner"><picture>
        <source media="(max-width: 600px)" data-srcset="/small.jpg 1x">
        <source data-srcset="/detail.jpg 1x, /detail-large.jpg 2x">
        <img src="data:image/gif;base64,AA"></picture></div></div>
        <div class="related-products"><img src="/recommended.jpg"></div>
        <div class="shopify-section" id="shopify-section-x__product_specifications_1">Wi-Fi 2.4GHz 지원</div></body>'''
        with patch('app.pipeline.product_documents.prepare', return_value=(html, [])):
            acquired = page_recovery.acquire(html, 'https://shop.example/products/one',
                fetch=lambda u: None, renderer=lambda u: self.fail('Static images must reach OCR'))
        from PIL import Image
        import io
        b = io.BytesIO(); Image.new('RGB', (100, 200)).save(b, format='PNG')
        seen = []
        def fetch(url): seen.append(url); return b.getvalue()
        capture = {'sections': [], 'coverage': {}}
        product_images.capture_images(acquired[0], acquired[1], capture,
            fetch_bytes=fetch, reader=lambda data: SimpleNamespace(text='AI가 물체를 인식합니다.', engine='test'))
        self.assertEqual(seen, ['https://shop.example/detail-large.jpg'])
        self.assertTrue(capture['coverage']['image_text_read'])
        self.assertIn('Wi-Fi', product_page.product_text(product_page.product_body(html, acquired[1])))

    def test_dyson_features_and_caveats_exclude_review_and_other_products(self):
        html = '''<body class="catalog-product-view product-dyson-360-vis-nav">
        <div class="dyson_cms_row_config"><section class="rich-content-component">
        <p>카메라와 센서가 청소 경로를 확인합니다.</p><img src="/feature.jpg"></section></div>
        <div class="dyson_cms_row_config"><div class="promos">앱에 청소 지도를 저장합니다.</div></div>
        <div class="dyson_cms_row_config" id="productcaveat">Wi-Fi 연결이 필요합니다.</div>
        <div class="dyson_cms_row_config" id="discover">다른 AI 제품 추천</div>
        <div class="reviews">사용자 리뷰 AI 최고</div>
        <div class="dyson_cms_row_config" id="reasontobuy">공식몰 구매 혜택</div></body>'''
        body = product_page.product_body(html, 'https://www.dyson.co.kr/model')
        text = product_page.product_text(body)
        for word in ('카메라', '지도', 'Wi-Fi'): self.assertIn(word, text)
        for word in ('다른 AI', '사용자 리뷰', '구매 혜택'): self.assertNotIn(word, text)
        self.assertEqual(len(body.select('img')), 1)

    def test_product_modules_not_used_on_category_page(self):
        with self.assertRaises(product_page.ProductBodyUnavailable):
            product_page.product_body('<body class="template-collection"><div class="product-media-and-text"><img src="/other.jpg"></div></body>', 'https://shop.example/collections/all')

    def test_srcset_preserves_cdn_query_commas(self):
        from bs4 import BeautifulSoup
        im = BeautifulSoup('<img srcset="https://cdn.example/photo?fit=stretch,1&amp;wid=100, https://cdn.example/photo?fit=stretch,1&amp;wid=200 2x">', 'html.parser').img
        self.assertEqual(product_page.image_source(im), 'https://cdn.example/photo?fit=stretch,1&wid=200')
