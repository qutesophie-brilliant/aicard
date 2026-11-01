import unittest
import json
from app.pipeline import product_page, source_capture, run


class VerifiedProductHeadingTests(unittest.TestCase):
    url = 'https://www.29cm.co.kr/products/3601340'
    name = '[다룸] 퓨어 AI 가열식 가습기 6L'

    def html(self, *, name=None, canonical=None, detail=True):
        return ('<link rel="canonical" href="' + (canonical or self.url) + '">'
                '<meta property="og:title" content="' + self.name + ' - 모든 취향 곁에, 29CM">'
                '<h2 id="pdp_product_name">' + (name or self.name) + '</h2>'
                '<div class="recommendations">다른 제품 AI 기능 설명</div>'
                + ('<div class="product-description"><img src="/detail.jpg"></div>' if detail else ''))

    def test_visible_name_is_retained_separately_from_image_description(self):
        body = product_page.product_body(self.html(), self.url)
        self.assertIn(self.name, body.get_text())
        self.assertNotIn('다른 제품', body.get_text())
        self.assertEqual('/detail.jpg', body.img['src'])
        capture = source_capture.capture_product_html(self.html(), self.url)
        self.assertEqual(self.url, capture['sections'][0]['source_url'])
        self.assertEqual('상품명', capture['sections'][0]['feature_scope'])
        self.assertIn(''.join(self.name.split()), ''.join(capture['text'].split()))

    def test_conflicting_canonical_or_title_does_not_become_evidence(self):
        for html in (self.html(canonical=self.url.replace('3601340', '999')),
                     self.html(canonical=self.url.replace('www.29cm.co.kr', 'other.example')),
                     self.html(name='다른 제품 AI 가습기'),
                     self.html().replace('<h2 id="pdp_product_name">', '<h2>')):
            with self.subTest(html=html):
                body = product_page.product_body(html, self.url)
                self.assertNotIn(self.name, body.get_text())

    def test_title_only_page_remains_unavailable(self):
        with self.assertRaises(product_page.ProductBodyUnavailable):
            product_page.product_body(self.html(detail=False), self.url)

    def test_already_selected_name_is_not_duplicated(self):
        html = self.html().replace('<img src="/detail.jpg">', '<p>' + self.name + '</p><img src="/detail.jpg">')
        body = product_page.product_body(html, self.url)
        self.assertEqual(1, body.get_text().count(self.name))

    def streamed(self, sku='3601340', ident='3601340'):
        html = self.html().replace('<h2 id="pdp_product_name">' + self.name + '</h2>', '')
        return html + '<script type="application/ld+json">' + json.dumps({
            '@type': 'Product', '@id': 'urn:29cm:product:' + ident,
            'sku': sku, 'name': self.name}) + '</script>'

    def test_published_item_json_retains_name_before_heading_hydration(self):
        body = product_page.product_body(self.streamed(), self.url)
        self.assertIn(self.name, body.get_text())
        self.assertEqual(1, len(body.select('img')))

    def test_wrong_structured_item_or_ambiguous_products_are_rejected(self):
        for html in (self.streamed(sku='999'), self.streamed(ident='999'),
                     self.streamed() + '<script type="application/ld+json">{"@type":"Product","name":"Other AI"}</script>'):
            with self.subTest(html=html):
                self.assertNotIn(self.name, product_page.product_body(html, self.url).get_text())

    def test_structured_name_without_description_stays_unavailable(self):
        with self.assertRaises(product_page.ProductBodyUnavailable):
            product_page.product_body(self.streamed().replace('<div class="product-description"><img src="/detail.jpg"></div>', ''), self.url)

    def test_quick_card_keeps_functional_title_without_inventing_explanation(self):
        for name in ('[다룸] 퓨어 대용량 올 스텐 AI 살균 통세척 가열식 가습기 6L',
                     '보아르 플로업 13L 가열식 가습기 대용량 Ai 자동습도조절'):
            with self.subTest(name=name):
                html = self.streamed().replace(self.name, name).replace(
                    json.dumps(self.name), json.dumps(name))
                capture = source_capture.capture_product_html(html, self.url)
                card, docs = run.quick_check(name, self.url, capture['text'], source_capture=capture)
                self.assertTrue(card['claims'])
                for claim in card['claims']:
                    self.assertIn(claim['text'].replace('\n', ' '), name)
                    self.assertFalse(claim['evidence'])
                    self.assertEqual(self.url, docs[claim['source']['doc_id']]['url'])

    def test_functional_title_does_not_borrow_general_safety_or_manual_modes(self):
        name = '[다룸] 퓨어 AI 살균 통세척 가열식 가습기 6L'
        html = self.streamed().replace(self.name, name).replace(json.dumps(self.name), json.dumps(name))
        html = html.replace('<img src="/detail.jpg">',
                            '<h2>안전 기능</h2><p>기울임 감지부터 과열 방지 기능까지 꼼꼼하게 설계했습니다.</p>'
                            '<h2>수동 조작</h2><p>가습량 조절 및 세척 모드 설정 가능. 수면, 표준, 쾌속, 세척 순서입니다.</p>')
        capture = source_capture.capture_product_html(html, self.url)
        card, _ = run.quick_check(name, self.url, capture['text'], source_capture=capture)
        self.assertTrue(card['claims'])
        title = next(c for c in card['claims'] if 'AI 살균' in c['text'])
        self.assertFalse(title['evidence'])

    def test_quick_card_still_excludes_bare_brand_model_and_category(self):
        for name in ('Bespoke AI 스팀 울트라', 'AI 가습기 6L', 'ThinQ 스마트 가습기'):
            with self.subTest(name=name):
                card, _ = run.quick_check(name, self.url, name)
                self.assertFalse(card['claims'])


if __name__ == '__main__':
    unittest.main()
