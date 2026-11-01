import unittest
from unittest.mock import Mock
import httpx
from app.pipeline import product_documents, product_page


class MarketplaceDetailTests(unittest.TestCase):
    def test_coupang_detail_images_are_kept_with_summary(self):
        html = '''<div class="product-description">AI 가습기 상품 요약</div>
        <div class="product-detail-content-inside"><img data-src="/detail.jpg">
        <p>AI 습도조절은 센서로 실내 습도를 감지하여 분무량을 조절합니다.</p></div>
        <aside class="related">다른 상품 AI 냉장고</aside>'''
        body = product_page.product_body(html, 'https://www.coupang.com/vp/products/123')
        self.assertIn('실내 습도', body.get_text())
        self.assertEqual('/detail.jpg', product_page.image_source(body.img))
        self.assertNotIn('냉장고', body.get_text())

    def test_gmarket_published_frame_is_read_for_same_item(self):
        url = 'https://item.gmarket.co.kr/Item?goodsCode=123'
        src = 'https://item.gmarket.co.kr/Item/ItemDetailV2?goodsCode=123&isNew=true'
        html = '<section id="vip-tab_detail"><iframe id="detail1" src="'+src+'"></iframe></section>'
        detail = '<div id="basic_detail_html"><p>AI 카메라로 장애물을 인식하고 회피합니다. 조명이 있는 실내에서 사용합니다.</p><img src="/detail.jpg"></div>'
        fetch = Mock(return_value=httpx.Response(200, text=detail, headers={'content-type':'text/html'}, request=httpx.Request('GET', src)))
        prepared, records = product_documents.prepare(html, url, fetch)
        fetch.assert_called_once_with(src)
        self.assertEqual('read', records[0]['status'])
        self.assertIn('장애물', product_page.product_body(prepared, url).get_text())

    def test_gmarket_other_product_or_origin_is_not_fetched(self):
        url = 'https://item.gmarket.co.kr/Item?goodsCode=123'
        for src in ['/Item/ItemDetail?goodsCode=124',
                    '/Item/ItemDetail?goodsCode=123&goodsCode=124',
                    '/account?goodsCode=123',
                    'https://other.example/Item/ItemDetail?goodsCode=123']:
            with self.subTest(src=src):
                fetch = Mock()
                html = '<section id="vip-tab_detail"><iframe id="detail1" src="'+src+'"></iframe></section>'
                product_documents.prepare(html, url, fetch)
                fetch.assert_not_called()

    def test_gmarket_redirect_to_another_item_is_not_evidence(self):
        url = 'https://item.gmarket.co.kr/Item?goodsCode=123'
        html = '<section id="vip-tab_detail"><iframe id="detail1" src="/Item/ItemDetailV2?goodsCode=123"></iframe></section>'
        fetch = Mock(return_value=httpx.Response(200, text='<div id="basic_detail_html">WRONG_PRODUCT</div>',
                     headers={'content-type':'text/html'}, request=httpx.Request('GET','https://item.gmarket.co.kr/Item/ItemDetailV2?goodsCode=124')))
        prepared, records = product_documents.prepare(html, url, fetch)
        self.assertNotIn('WRONG_PRODUCT', prepared)
        self.assertNotEqual('read', records[0]['status'])
