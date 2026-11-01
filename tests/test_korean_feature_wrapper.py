import unittest
from app.pipeline.product_page import product_body, product_text, ProductBodyUnavailable


class KoreanFeatureWrapperTests(unittest.TestCase):
    def test_animated_product_wrapper_keeps_features_and_conditions(self):
        html = '''<div class="newmodelsections-container">
        <div class="newmodelae-nav-container"><nav>AI 다른 제품 메뉴</nav>
        <div id="ae-design"><section id="section9">
        <h2>AI 기반 맞춤 설정</h2><p>사용자가 앱에서 새 사물을 정의하면 AI 기반 인식 기술로 사물을 구별합니다.</p>
        <p>이 기능은 OTA 업데이트 후 제공됩니다. 사용 전에 앱에서 장애물 정의를 입력해야 합니다.</p>
        </section></div><div class="recommendations">다른 모델의 AI 기능</div>
        <div class="reviews">리뷰에서 AI가 최고라고 평가함</div></div></div>
        <footer>공통 사이트 안내</footer>'''
        text = product_text(product_body(html, 'https://kr.roborock.com/pages/new-model'))
        for phrase in ('AI 기반 맞춤 설정', '사물을 구별', 'OTA 업데이트', '장애물 정의'):
            self.assertIn(phrase, text)
        for phrase in ('다른 제품 메뉴', '다른 모델', '리뷰에서', '공통 사이트'):
            self.assertNotIn(phrase, text)

    def test_navigation_without_product_sections_is_not_a_product(self):
        with self.assertRaises(ProductBodyUnavailable):
            product_body('<div class="ae-nav-container"><p>AI 기능이 있는 다른 제품의 목록을 찾아보세요.</p></div>',
                         'https://kr.roborock.com/pages/catalog')

    def test_unrelated_domain_does_not_gain_layout_exception(self):
        with self.assertRaises(ProductBodyUnavailable):
            product_body('<div class="newmodelsections-container"><div class="newmodelae-nav-container"><div id="ae-design"><p>AI 기능이 있는 다른 제품의 목록을 찾아보세요.</p></div></div></div>',
                         'https://example.org/pages/catalog')
