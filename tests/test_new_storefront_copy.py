import unittest

from app.pipeline import extract
from app.pipeline.product_page import product_body, product_text, ProductBodyUnavailable


class NewStorefrontCopyTests(unittest.TestCase):
    def test_product_carousel_layout_is_not_navigation_or_media_review(self):
        html = '''<body class="template-product"><main>
        <div class="header-content">Site navigation AI deals</div>
        <section class="common-section-container new-common-product-swiper-section new-swiper-navigation">
        <div class="header-content"><p>The AI camera detects small obstacles.</p>
        <img src="https://cdn.example/feature.jpg"><p>Only in suitable lighting.</p></div></section>
        <section class="common-section-container"><p>Reviewer says AI is great.</p></section>
        <section class="common-section-container"><p>Compare another model.</p></section>
        </main></body>'''
        body = product_body(html, 'https://store.example/products/robot')
        text = product_text(body)
        self.assertIn('AI camera detects small obstacles.', text)
        self.assertIn('Only in suitable lighting.', text)
        self.assertEqual(len(body.select('img')), 1)
        for noise in ('Site navigation', 'Reviewer', 'another model'):
            self.assertNotIn(noise, text)

    def test_product_key_features_and_image_details_are_both_collected(self):
        html = '''<div class="product-info"><div class="ndev-product-key-features">
        <p>Obstacle avoidance: Dual AI cameras.</p><p>Recognizes objects in sufficient light.</p>
        </div></div><section class="shopify-section shopify-ndev-narwal-main">
        <img data-src="https://cdn.example/detail.jpg"><p>Cleaning features.</p></section>
        <div class="recommendations">Other model AI function.</div>'''
        body = product_body(html, 'https://store.example/products/robot')
        self.assertIn('Dual AI cameras.', product_text(body))
        self.assertIn('sufficient light.', product_text(body))
        self.assertEqual(len(body.select('img')), 1)
        self.assertNotIn('Other model', product_text(body))

    def test_product_cms_feature_modules_keep_images_and_conditions_only(self):
        html = '''<main><div id="ipc-listing"><div class="ipc_template">
        <div id="GraphicOverlay_123"><section><h3>AI.See Obstacle Avoidance</h3>
        <h4>Advanced AI and RGB visual recognition.</h4>
        <img data-src="https://cdn.example/obstacles.jpg">
        <p>Remove wires or other small-sized obstacles.</p></section></div>
        <div id="Faq_456"><p>Other model E28 navigation settings.</p></div>
        <div id="Specs_789">Other model AI comparison</div>
        <div id="SelectStore_123">AI shopping benefits</div>
        <div class="reviews">Customer AI review</div>
        </div></div></main>'''
        body = product_body(html, 'https://store.example/products/robot')
        text = product_text(body)
        self.assertIn('Advanced AI and RGB visual recognition.', text)
        self.assertIn('Remove wires or other small-sized obstacles.', text)
        self.assertEqual(body.img['data-src'], 'https://cdn.example/obstacles.jpg')
        for noise in ('Other model', 'shopping benefits', 'Customer AI'):
            self.assertNotIn(noise, text)

    def test_server_rendered_product_editor_preserves_accessible_detail_copy(self):
        html = '''<h2>제품 상세</h2><div>카메라 이름과 공유하기</div>
        <div id="tab01Ssr" style="display:none" aria-hidden="true">
        <div class="prd_detail prd_editor"><div class="eventPage">
        <img src="https://cdn.example/feature.jpg"><div class="ir-hidden">
        <h3>딥러닝 피사체 추적</h3><p>딥러닝 기술은 피사체의 특성을 인식하여 자세가 바뀌어도 안정적으로 추적합니다.</p>
        <p>피사체에 따라 검출하지 못할 수 있습니다.</p></div></div></div></div>
        <div class="recommendations">다른 카메라 AI 할인</div>'''
        body = product_body(html, 'https://camera.example/product/detail/123')
        text = product_text(body)
        self.assertIn('딥러닝 기술은 피사체의 특성을', text)
        self.assertIn('피사체에 따라 검출하지 못할 수', text)
        self.assertNotIn('공유하기', text)
        self.assertNotIn('다른 카메라', text)
        self.assertEqual(body.img['src'], 'https://cdn.example/feature.jpg')

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
