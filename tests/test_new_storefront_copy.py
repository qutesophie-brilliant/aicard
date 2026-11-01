import unittest

from app.pipeline import extract
from app.pipeline.product_page import product_body, product_text, ProductBodyUnavailable


class NewStorefrontCopyTests(unittest.TestCase):
    def test_shopify_product_overview_keeps_copy_not_other_store_content(self):
        html = '''<main><section class="shopify-section--custom-html">
        <div class="overview-content"><section><h2>스마트 AI 감지</h2>
        <p>스마트 AI가 얼굴, 사람, 반려동물, 차량을 식별하고 사용자에게 알림을 보냅니다.</p>
        <img src="https://cdn.example/detail.jpg"></section>
        <div class="reviews">다른 AI 제품도 좋다는 후기</div></div></section>
        <div class="recommendations">다른 제품 AI 할인</div></main>'''
        body = product_body(html, 'https://store.example/products/camera')
        self.assertIn('반려동물, 차량을 식별', product_text(body))
        self.assertEqual(body.img['src'], 'https://cdn.example/detail.jpg')
        self.assertNotIn('다른', product_text(body))

    def test_generic_overview_without_product_container_is_not_accepted(self):
        with self.assertRaises(ProductBodyUnavailable):
            product_body('<main><div class="overview-content">스마트 AI가 얼굴과 사람을 식별하고 알림을 보냅니다. 사이트 전체 소개입니다.</div></main>', 'https://store.example/')

    def test_explicit_ai_operated_product_introduction_is_retained(self):
        quote = 'AI로 작동하는 지능형 면도기는 실시간 압력 피드백을 제공하여 피부를 보호합니다.'
        chunks = [{'doc_id':1, 'chunk':0, 'quote':quote}]
        found = extract._rules_extract(chunks, 14, complete_quotes=True)
        self.assertEqual([quote], [c['text'] for c in found])

    def test_explicit_ai_does_not_restore_shopping_or_name_only_copy(self):
        for quote in ['AI로 작동하는 면도기를 할인 가격에 구매하세요.', 'AI 면도기 S9000']:
            self.assertEqual([], extract._rules_extract([{'doc_id':1, 'chunk':0, 'quote':quote}], 14, complete_quotes=True))
