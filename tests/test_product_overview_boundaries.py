import unittest
from app.pipeline import ingest
from app.pipeline.run import quick_check


class ProductOverviewBoundaries(unittest.TestCase):
    def test_html_quote_does_not_end_inside_noise_word(self):
        quote='Full HD, 60 fps 웹캠과 AI 소음 제거 기술을 탑재한 마이크가 배경 소리를 줄여줍니다.'
        html=f'<div class="productOverview"><div class="overContent"><p>{quote}</p></div></div>'
        result=ingest.from_product_html(html,'https://seller.example/product')
        card,_=quick_check('웹캠','',result.text,source_capture=result.source_capture)
        self.assertIn(quote,[c['text'] for c in card['claims']])

    def test_product_category_and_control_app_are_not_security_support(self):
        from app.pipeline.quick_coverage import _matches
        self.assertFalse(_matches('safety','2K 회전형 Wi-Fi 보안 카메라'))
        self.assertFalse(_matches('safety','소프트웨어는 마이크 설정을 제어할 수 있습니다.'))
        self.assertTrue(_matches('safety','보안 패치를 제공합니다.'))
        self.assertTrue(_matches('safety','펌웨어 업데이트를 지원합니다.'))

    def test_nested_product_overviews_are_collected_without_reviews(self):
        layouts=[('productOverview','overContent'),('product-info','overview-content')]
        for outer,inner in layouts:
            html=f'''<div class="{outer}"><div class="{inner}">
            <h2>스마트 AI 감지 및 알림</h2><p>스마트 AI는 사람과 차량을 식별하여 사용자에게 알려줍니다.</p>
            </div><div class="reviews">AI 고객 후기입니다.</div></div>
            <div class="overview-content">다른 제품의 AI 추천입니다.</div>'''
            result=ingest.from_product_html(html,'https://seller.example/product')
            self.assertIn('사람과 차량을 식별',result.text)
            self.assertNotIn('고객 후기',result.text)
            self.assertNotIn('다른 제품',result.text)

    def test_feature_header_keeps_prior_battery_spec_out_of_next_claim(self):
        html='''<header>사이트 메뉴</header><div id="see-all-benefits">
        <p>작동 시간 120분</p><section class="feature-card">
        <h3 class="feature-card-header">스마트 감지 시스템</h3>
        <p>AI는 센서로 공간을 인식하여 청소 경로를 조절합니다.</p>
        </section><section class="reviews"><h3>고객 후기</h3>AI 후기입니다.</section></div>'''
        result=ingest.from_product_html(html,'https://seller.example/product')
        card,_=quick_check('청소기','',result.text,source_capture=result.source_capture)
        self.assertIn('스마트 감지 시스템',result.text)
        self.assertNotIn('사이트 메뉴',result.text)
        self.assertNotIn('고객 후기',result.text)
        self.assertTrue(card['claims'])
        self.assertTrue(all('120분' not in c['text'] for c in card['claims']))
        section=next(s for s in result.source_capture['sections'] if '공간을 인식' in s['text'])
        self.assertEqual('스마트 감지 시스템',section['feature_scope'])


if __name__=='__main__': unittest.main()
