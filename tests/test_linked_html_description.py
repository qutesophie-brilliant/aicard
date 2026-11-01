import unittest
from unittest.mock import Mock
import httpx
from app.pipeline import product_documents, product_page, page_recovery, ingest
from app.network_safety import PUBLIC_USER_AGENT


URL = 'https://shop.example/product/123'
DETAIL = 'https://description.example/describe/123'


class LinkedHtmlDescriptionTests(unittest.TestCase):
    def html(self, target='.pic1', method='GET', endpoint=DETAIL):
        return '''<title>AI 세탁기</title><div class="goods_info"><div class="pic1"></div></div>
        <aside class="reviews"><div id="reviews"></div></aside><div id="page"></div>
        <script>commonAjax('%s', '', 'html', '%s', false)
        .done(function(data, textStatus, jqXHR){ $("%s").append(data); });</script>''' % (endpoint, method, target)

    def test_literal_detail_load_recovers_real_text_and_lazy_image(self):
        response = httpx.Response(200, text='<p>AI가 무게와 옷감을 감지하여 세탁합니다. 이 기능은 세탁물 6kg 이하에서 사용합니다.</p><img data-src="/actual-long.jpg"><nav>AI 추천상품</nav>',
                                  headers={'content-type': 'text/html'}, request=httpx.Request('GET', DETAIL))
        fetch = Mock(return_value=response)
        html, records = product_documents.prepare(self.html(), URL, fetch)
        fetch.assert_called_once_with(DETAIL)
        body = product_page.product_body(html, URL)
        self.assertIn('6kg', body.get_text())
        self.assertNotIn('추천상품', body.get_text())
        self.assertEqual('https://description.example/actual-long.jpg', body.img['src'])
        self.assertEqual([{'url': DETAIL, 'status': 'read'}], records)

    def test_get_load_applies_to_an_unfamiliar_domain(self):
        html = '<div id="product-description"></div><script>$("#product-description").load("/content/123");</script>'
        from bs4 import BeautifulSoup
        self.assertEqual([URL.split('/product')[0]+'/content/123'],
                         product_documents.linked_html_descriptions(BeautifulSoup(html, 'html.parser'), URL))

    def test_post_review_and_pagewide_loads_are_excluded(self):
        from bs4 import BeautifulSoup
        for html in (self.html(method='POST'), self.html(target='#reviews'), self.html(target='#page'),
                     self.html(endpoint='javascript:alert(1)')):
            with self.subTest(html=html):
                self.assertEqual([], product_documents.linked_html_descriptions(BeautifulSoup(html, 'html.parser'), URL))

    def test_access_denial_is_not_inserted_as_product_copy(self):
        response = httpx.Response(200, text='<title>Access Denied</title>', headers={'content-type':'text/html'}, request=httpx.Request('GET',DETAIL))
        html, records = product_documents.prepare(self.html(), URL, Mock(return_value=response))
        self.assertEqual('unavailable', records[0]['status'])
        self.assertNotIn('Access Denied', html)

    def test_page_client_identifies_our_service(self):
        self.assertEqual(PUBLIC_USER_AGENT, ingest.UA)
        self.assertIn('AIInfoCard', ingest.UA)
        self.assertNotIn('Chrome/', ingest.UA)

    def test_connection_failure_reaches_browser_before_giving_up(self):
        snapshot = {'url': URL, 'html': '<div class="product-description">실제 제품 설명</div>'}
        renderer = Mock(return_value=snapshot)
        response, rendered = page_recovery.fetch_initial(URL, Mock(side_effect=httpx.ConnectError('reset')), renderer)
        self.assertEqual(snapshot, rendered)
        self.assertEqual(snapshot['html'], response.text)
        renderer.assert_called_once_with(URL)

    def test_denial_does_not_trigger_browser_retry(self):
        for status in (401, 403, 429):
            response = httpx.Response(status, request=httpx.Request('GET', URL))
            renderer = Mock()
            with self.assertRaises(httpx.HTTPStatusError):
                page_recovery.fetch_initial(URL, Mock(side_effect=httpx.HTTPStatusError('denied', request=response.request, response=response)), renderer)
            renderer.assert_not_called()


if __name__ == '__main__': unittest.main()
