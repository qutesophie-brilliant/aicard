import unittest
from unittest.mock import Mock

import httpx
from app.pipeline import product_documents, product_page


class HimartDescriptionTests(unittest.TestCase):
    url = 'https://www.e-himart.co.kr/app/goods/goodsDetail?goodsNo=0066950871'
    detail = 'https://www.e-himart.co.kr/app/goods/getGoodsDescContMob?goodsNo=0066950871'

    def parent(self, src=None):
        return ('<title>한일 선풍기</title><iframe id="goodsDescCont" title="상품상세설명" src="'
                + (src or self.detail) + '"></iframe>')

    def response(self, url=None):
        return httpx.Response(200, text='<p>AI 모드가 온도에 따라 바람을 조절합니다.</p>'
                              '<img data-src="/images/detail.jpg"><nav>WRONG_NAV</nav>',
                              headers={'content-type': 'text/html'},
                              request=httpx.Request('GET', url or self.detail))

    def test_declared_same_product_frame_recovers_description_and_images(self):
        fetch = Mock(return_value=self.response())
        prepared, records = product_documents.prepare(self.parent(), self.url, fetch)
        fetch.assert_called_once_with(self.detail)
        body = product_page.product_body(prepared, self.url)
        self.assertIn('온도에 따라', body.get_text())
        self.assertNotIn('WRONG_NAV', body.get_text())
        self.assertEqual('https://www.e-himart.co.kr/images/detail.jpg', body.img['src'])
        self.assertEqual('read', records[0]['status'])
        self.assertTrue(body.select('[data-source-url="' + self.detail + '"]'))

    def test_mismatched_id_host_route_or_ambiguous_query_is_not_fetched(self):
        for src in (self.detail.replace('0066950871', '999'),
                    self.detail + '&goodsNo=999', self.detail + '&other=1',
                    self.detail.replace('www.e-himart.co.kr', 'other.example'),
                    self.detail.replace('getGoodsDescContMob', 'goodsDetail'),
                    self.detail + '#other', self.detail.replace('https:', 'http:')):
            with self.subTest(src=src):
                fetch = Mock()
                product_documents.prepare(self.parent(src), self.url, fetch)
                fetch.assert_not_called()

    def test_redirect_to_other_product_is_not_imported(self):
        fetch = Mock(return_value=self.response(self.detail.replace('0066950871', '999')))
        prepared, records = product_documents.prepare(self.parent(), self.url, fetch)
        self.assertEqual('unavailable', records[0]['status'])
        self.assertNotIn('온도에 따라', prepared)

    def test_frame_id_is_scoped_to_valid_himart_product_pages(self):
        for url in ('https://other.example/app/goods/goodsDetail?goodsNo=0066950871',
                    self.url + '&goodsNo=999', self.url.replace('goodsDetail', 'review')):
            with self.subTest(url=url):
                fetch = Mock()
                product_documents.prepare(self.parent(), url, fetch)
                fetch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
