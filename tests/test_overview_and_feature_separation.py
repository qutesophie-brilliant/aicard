import json
import unittest
from types import SimpleNamespace
from app.pipeline import page_recovery, product_documents, product_page
from app.pipeline.run import _other_quick_feature, quick_check


class OverviewRecoveryTests(unittest.TestCase):
    url = 'https://www.mi.com/kr/product/example-robot/buy/'
    overview = 'https://www.mi.com/kr/product/example-robot/'

    def test_explicit_feature_modules_keep_images_without_product_gallery(self):
        html = '<main><div class="gallery"><img src="/thumbnail.jpg"></div><div class="product-feature"><h2>AI 물체 인식</h2><img src="/description.jpg"><p>카메라로 장애물을 인식하고 회피합니다.</p></div><div class="recommend"><div class="product-feature">다른 제품 AI 음성 인식</div></div></main>'
        body = product_page.product_body(html, 'https://example.com/product/robot')
        self.assertEqual([im['src'] for im in body.select('img')], ['/description.jpg'])
        self.assertNotIn('다른 제품', body.get_text())

    def shell(self, target):
        return '<script>window.__PRELOADED_STATE__ = ' + json.dumps({'pageButton': [{'link': target}]}) + ';</script>'

    def test_buy_shell_recovers_published_same_product_overview(self):
        body = '<div class="product_v4"><main class="fly-birds-page_123"><p>AI 카메라가 물체를 인식하여 장애물을 피해 이동합니다.</p><img data-src="/detail.jpg"></main></div>'
        called = []
        def fetch(url):
            called.append(url)
            return SimpleNamespace(text=body, url=url, headers={'content-type': 'text/html'})
        html, url, _, stages = page_recovery.acquire(self.shell(self.overview), self.url, fetch,
            renderer=lambda _: self.fail('Valid overview must not need rendering'))
        self.assertEqual(called, [self.overview])
        self.assertEqual(url, self.overview)
        self.assertEqual(stages[0]['stage'], 'product_overview')
        self.assertEqual(len(product_page.product_body(html, url).select('img')), 1)

    def test_other_product_or_host_or_review_is_not_followed(self):
        for url in ('https://evil.example/kr/product/example-robot/',
                    'https://www.mi.com/kr/product/other/', self.overview + 'review'):
            self.assertIsNone(product_documents.overview_link(self.shell(url), self.url))

    def test_invalid_script_and_category_are_not_executed_or_accepted(self):
        self.assertIsNone(product_documents.overview_link('<script>window.__PRELOADED_STATE__ = alert(1)</script>', self.url))
        with self.assertRaises(product_page.ProductBodyUnavailable):
            product_page.product_body('<div class="product_v4"><main class="fly-birds-page_1">AI 카메라를 포함한 여러 제품을 소개하고 추천합니다.</main></div>', 'https://www.mi.com/kr/product-list/')


class FeatureSeparationTests(unittest.TestCase):
    def test_english_continuation_quotes_same_paragraph_action(self):
        from app.pipeline import quick_english
        quote = 'The AI cleaner detects dirty areas on the floor.'
        following = 'It can mark these areas for repeated cleaning.'
        doc = {'id': 1, 'kind': '판매페이지', 'text': quote + ' ' + following,
               'chunks': [quote, following]}
        claim = {'text': quote, 'source': {'doc_id': 1}, 'evidence': []}
        result = quick_english.connect([claim], [doc])[0]
        self.assertEqual(result['evidence'][0]['quote'], following)
        self.assertEqual(result['evidence'][0]['chunk'], 1)

    def test_english_continuation_never_crosses_paragraph_or_borrows_other_feature(self):
        from app.pipeline import quick_english
        quote = 'The AI cleaner detects dirty areas on the floor.'
        for tail in ['\nIt can mark these areas for repeated cleaning.',
                     ' Another sensor can mark these areas.',
                     ' It can not mark these areas.',
                     ' It can control another product in this room.']:
            doc = {'id': 1, 'kind': '판매페이지', 'text': quote + tail,
                   'chunks': [quote, tail.strip()]}
            claim = {'text': quote, 'source': {'doc_id': 1}, 'evidence': []}
            self.assertFalse(quick_english.connect([claim], [doc])[0]['evidence'])

    def test_quick_preview_keeps_english_ai_function_source(self):
        from app.pipeline.extract import _rules_extract
        quote = 'The AI Instant Re-Mop is capable of detecting areas on the floor that require deep cleaning.'
        rows = [{'doc_id': 1, 'chunk': 0, 'quote': quote}]
        quick = _rules_extract(rows, 14, complete_quotes=True)
        self.assertEqual(quick[0]['text'], quote)
        self.assertEqual(quick[0]['source']['quote'], quote)
        self.assertEqual(_rules_extract(rows, 14), [])

    def test_english_checkout_and_non_ai_functions_are_not_claims(self):
        from app.pipeline.extract import _rules_extract
        rows = [{'doc_id': 1, 'chunk': i, 'quote': q} for i,q in enumerate([
            'AI cleaning discount coupon shipping offer',
            'The robot can clean the floor automatically.',
            'Test conditions: AI cleaning was tested in our laboratory.'
        ])]
        self.assertEqual(_rules_extract(rows, 14, complete_quotes=True), [])

    def test_audio_ai_description_survives_structured_source_to_preview(self):
        from app.pipeline.source_capture import capture_product_html
        quote = ('6-Mic AI Clear Calls: With a noise cancelling algorithm, a wind noise '
                 'reduction tech, and six microphones, enjoy clear calls wherever you are.')
        html = '<script type="application/ld+json">' + json.dumps({
            '@type': 'Product', 'name': 'Example earbuds', 'description': quote}) + '</script>'
        capture = capture_product_html(html, 'https://example.com/products/earbuds')
        card, _ = quick_check('Example earbuds', '', capture['text'], source_capture=capture)
        self.assertEqual([c['text'] for c in card['claims']], [quote])
        self.assertEqual(card['claims'][0]['source']['quote'], quote)
        from app.pipeline.extract import _rules_extract
        rows = [{'doc_id': 1, 'chunk': 0, 'quote': quote}]
        self.assertEqual(_rules_extract(rows, 14), [])  # Formal extraction unchanged.

    def test_audio_marketing_noise_is_not_promoted_to_ai_claim(self):
        from app.pipeline.extract import _rules_extract
        quotes = ['Six microphones provide clear calls with noise reduction.',
                  'Customer review: AI clear calls are great.',
                  'AI clear calls discount coupon shipping offer',
                  'Test conditions: AI noise reduction was tested in a lab.']
        rows = [{'doc_id': 1, 'chunk': i, 'quote': q} for i, q in enumerate(quotes)]
        self.assertEqual(_rules_extract(rows, 14, complete_quotes=True), [])

    def test_navigation_does_not_borrow_suction_trial(self):
        source = 'AI 카메라가 장애물을 인식하고 위치를 파악합니다.\n시험 조건: 싸이클론 흡입력은 독립시험기관에서 측정했습니다.'
        card, _ = quick_check('로봇청소기', '', source)
        self.assertTrue(card['claims'])
        for claim in card['claims']:
            if '카메라' in claim['text']:
                self.assertFalse(any('흡입력' in e['quote'] for e in claim['evidence']))
                self.assertNotIn('흡입력', claim.get('conditions', ''))

    def test_paraphrased_temperature_does_not_borrow_container_or_warranty(self):
        claim = '사용 패턴을 학습하여 냉장고의 내부 온도를 일정하게 유지합니다.'
        for quote in ('냄새케어 김치통의 메틸메르캅탄 농도를 측정합니다.',
                      '평생보증은 컴프레서 한정이며 제품 교환에는 적용되지 않습니다.'):
            self.assertTrue(_other_quick_feature(claim, quote))

    def test_matching_and_shared_prerequisites_remain(self):
        self.assertFalse(_other_quick_feature('AI 카메라가 장애물을 인식합니다.', '어두운 곳에서는 카메라의 장애물 인식이 제한됩니다.'))
        self.assertFalse(_other_quick_feature('AI 정온 모드', 'SmartThings 앱 설치 및 무선 네트워크 연결이 필요합니다.'))
        self.assertFalse(_other_quick_feature('AI 세탁', 'AI 세탁 기능은 6kg 이하에서 작동합니다.'))
