import unittest
from unittest.mock import Mock
from bs4 import BeautifulSoup
from app import check_budget
from app.pipeline import ingest, product_page, marketplace_documents, page_recovery


class HimartHeadingTests(unittest.TestCase):
    url = 'https://www.e-himart.co.kr/app/goods/goodsDetail?goodsNo=0071621000'
    name = '코드제로 AI 로보킹 올인원 로봇청소기 B94AHB'

    def html(self):
        return ('<title>롯데하이마트</title>'
                '<meta property="og:title" content="롯데하이마트">'
                '<meta property="eg:itemId" content="0071621000">'
                f'<meta property="eg:itemName" content="{self.name}">'
                f'<meta property="og:description" content="{self.name}">'
                f'<div class="hm-goods-name">{self.name}</div>'
                '<div class="product-description"><img src="/detail.jpg"></div>')

    def test_verified_name_survives_image_only_body_and_generic_title(self):
        result = ingest.from_product_html(self.html(), self.url)
        self.assertEqual(self.name, result.title)
        self.assertIn(self.name, result.text)
        self.assertNotIn('롯데하이마트', result.text)
        self.assertEqual(self.url, result.source_capture['sections'][0]['source_url'])

    def test_wrong_product_and_disagreeing_names_are_not_evidence(self):
        for html in (self.html().replace('content="0071621000"', 'content="999"'),
                     self.html().replace(f'<div class="hm-goods-name">{self.name}', '<div class="hm-goods-name">다른 제품'),
                     self.html() + '<meta property="eg:itemId" content="999">'):
            self.assertEqual('', marketplace_documents.product_heading(BeautifulSoup(html, 'html.parser'), self.url))

    def test_name_without_detail_does_not_pretend_to_read_description(self):
        html = self.html().replace('<div class="product-description"><img src="/detail.jpg"></div>', '')
        with self.assertRaises(product_page.ProductBodyUnavailable):
            ingest.from_product_html(html, self.url)

    def test_country_block_is_not_product_title_or_analysis_input(self):
        state = check_budget.Budget()
        token = check_budget._current.set(state)
        fetch, render = Mock(), Mock()
        try:
            with self.assertRaises(product_page.ProductBodyUnavailable):
                page_recovery.acquire('<title>Access blocking country info</title><p>Access denied</p>',
                                      'https://i-nic.co.kr/surl/P/133', fetch, renderer=render)
            self.assertNotIn('title', state.snapshot)
            fetch.assert_not_called()
            render.assert_not_called()
        finally:
            check_budget._current.reset(token)


if __name__ == '__main__':
    unittest.main()
