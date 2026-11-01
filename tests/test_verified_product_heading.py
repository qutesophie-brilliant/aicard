import unittest
from app.pipeline import product_page, source_capture


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


if __name__ == '__main__':
    unittest.main()
