import unittest
from app.pipeline import product_page


class OhouseProductSectionTests(unittest.TestCase):
    url = 'https://store.ohou.se/goods/4276514'
    html = '''<main>
    <section><header><h2>상품정보</h2></header><div>
    <img src="/seller/detail.jpg"><table><tr><th>품명 및 모델명</th><td>가습기 VVHU-003WH</td></tr>
    <tr><th>작동 조건</th><td>자동 습도 조절은 물을 채운 상태에서 사용합니다.</td></tr></table>
    </div></section>
    <section><header><h2>리뷰</h2></header><p>WRONG_REVIEW AI 최고</p><img src="/review.jpg"></section>
    <section><header><h2>추천</h2></header><p>WRONG_RECOMMEND AI 청소기</p><img src="/other.jpg"></section>
    </main>'''

    def test_product_information_preserves_images_and_specs_without_reviews(self):
        body = product_page.product_body(self.html, self.url)
        self.assertEqual(['/seller/detail.jpg'], [i['src'] for i in body.select('img')])
        self.assertIn('VVHU-003WH', body.get_text())
        self.assertIn('물을 채운 상태', body.get_text())
        self.assertNotIn('WRONG_', body.get_text())

    def test_no_whole_page_recovery_on_other_hosts_or_non_product_routes(self):
        for url in ('https://other.example/goods/4276514', 'https://store.ohou.se/brands/4276514'):
            with self.subTest(url=url):
                with self.assertRaises(product_page.ProductBodyUnavailable):
                    product_page.product_body(self.html, url)

    def test_multiple_product_information_sections_are_not_guessed(self):
        with self.assertRaises(product_page.ProductBodyUnavailable):
            product_page.product_body(self.html + self.html, self.url)


if __name__ == '__main__':
    unittest.main()
