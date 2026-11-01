import json
import unittest

from app.pipeline.product_page import product_body, product_text, ProductBodyUnavailable


class EmbeddedFeatureTests(unittest.TestCase):
    def page(self, suffix='', extra=None):
        data = {'productFeatures': {'productFeatures': [{
            'galleryList': [{'altStr': '광고 장면 AI 설명', 'contentList': [{
                'headlines': '<p>멀티디바이스 동기화</p>',
                'description': '<p>Smart Share는 AI 가상 센서로 스마트폰을 연결합니다.</p><p>사용하려면 앱이 실행 중이어야 합니다.</p>',
                'ctaStr': '관련 상품 구매'}]}]}]},
            'recommendations': {'description': '다른 제품 AI 기능'},
            'techSpecs': extra}
        return '<script>var $pdpAllData = ' + json.dumps(data) + suffix + '</script>'

    def test_literal_features_keep_conditions_exclude_other_copy(self):
        text = product_text(product_body(self.page(';'), 'https://www.lenovo.com/kr/ko/p/laptops/model123'))
        self.assertIn('AI 가상 센서', text)
        self.assertIn('앱이 실행 중이어야', text)
        for excluded in ('다른 제품', '관련 상품 구매', '광고 장면'):
            self.assertNotIn(excluded, text)

    def test_no_javascript_evaluation_or_foreign_host(self):
        for html, url in ((self.page(' + malicious()'), 'https://www.lenovo.com/kr/ko/p/model'),
                          (self.page(), 'https://example.com/p/model'),
                          (self.page() + self.page(), 'https://www.lenovo.com/kr/ko/p/model')):
            with self.assertRaises(ProductBodyUnavailable):
                product_body(html, url)
