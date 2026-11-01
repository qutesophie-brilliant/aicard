import json
import unittest

from app.pipeline.product_page import product_body, product_text, ProductBodyUnavailable


class EmbeddedFeatureTests(unittest.TestCase):
    def test_feature_translation_and_app_prerequisite(self):
        from app.pipeline.quick_coverage import _matches
        self.assertFalse(_matches('optout', '고성능 그래픽을 통해 더 빠른 편집과 같은 AI 경험을 해제할 수 있습니다.'))
        self.assertTrue(_matches('optout', '설정에서 AI 기능을 해제할 수 있습니다.'))
        self.assertTrue(_matches('conditions', '*탭하여 공유 기능을 사용하려면 Smart Share 앱이 실행 중이어야 합니다.'))

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
