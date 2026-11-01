import unittest
from unittest.mock import Mock, patch
import httpx
from app.network_safety import PublicURLRejected
from app.pipeline import public_reader, ingest, product_page

URL = 'https://seller.example/product/model-42?itemId=42'
HTML = '<title>제품 42</title><div class="product-description"><h1>제품 42</h1><p>AI가 세탁물의 무게와 옷감을 감지하여 세탁합니다. 세탁물 6kg 이하에서 작동합니다.</p></div>'


class PublicReaderTests(unittest.TestCase):
    def payload(self, **changes):
        data = dict(url=URL, finalUrl=URL, html=HTML, httpStatus=200)
        data.update(changes)
        return {'code': 200, 'data': data}

    def read(self, payload, url=URL):
        response = Mock()
        response.json.return_value = payload
        with patch.object(public_reader, 'public_addresses', side_effect=lambda u: (httpx.URL(u), ['93.184.216.34'])), patch.object(public_reader, 'fetch_public', return_value=response) as fetch:
            result = public_reader.read(url)
        return result, fetch

    def test_returns_actual_html_with_original_source_url(self):
        result, fetch = self.read(self.payload())
        self.assertEqual(HTML, result.text)
        self.assertEqual(URL, str(result.url))
        args = fetch.call_args.kwargs
        self.assertEqual('html', args['headers']['X-Return-Format'])
        self.assertEqual('1', args['headers']['DNT'])
        self.assertEqual(0, args['max_redirects'])
        self.assertNotIn('Authorization', args['headers'])

    def test_other_product_or_login_redirect_rejected(self):
        for dest in ('https://seller.example/product/model-43?itemId=43', 'https://seller.example/login', 'https://127.0.0.1/product/42'):
            with self.subTest(dest=dest), self.assertRaises(product_page.ProductBodyUnavailable):
                self.read(self.payload(finalUrl=dest))

    def test_provider_http_success_is_not_source_success(self):
        for code in (401,403,404,429,None):
            with self.subTest(code=code), self.assertRaises(product_page.ProductBodyUnavailable):
                self.read(self.payload(httpStatus=code))

    def test_challenge_html_cannot_become_a_card(self):
        for body in ('<title>Access Denied</title>', '<h1>접속이 잠시 제한되었습니다</h1>', '<h1>Verify you are human</h1>'):
            with self.subTest(body=body), self.assertRaises(product_page.ProductBodyUnavailable):
                self.read(self.payload(html=body+' '*200))

    def test_generated_content_without_html_is_rejected(self):
        with self.assertRaises(product_page.ProductBodyUnavailable):
            self.read(self.payload(html=None,content='AI가 6kg 이하에서 동작합니다.'))

    def test_sensitive_urls_never_leave_server(self):
        for url in ('https://seller.example/product?token=abc', 'https://seller.example/login', 'http://seller.example/product/42', 'https://seller.example/', 'https://seller.example/product?X-Amz-Signature=abc'):
            with patch.object(public_reader,'public_addresses',return_value=(httpx.URL(url),['93.184.216.34'])), patch.object(public_reader,'fetch_public') as fetch:
                with self.assertRaises(product_page.ProductBodyUnavailable): public_reader.read(url)
                fetch.assert_not_called()

    def test_private_address_never_leaves_server(self):
        with patch.object(public_reader,'public_addresses',side_effect=PublicURLRejected('private')), patch.object(public_reader,'fetch_public') as fetch:
            with self.assertRaises(PublicURLRejected): public_reader.read('https://127.0.0.1/product')
            fetch.assert_not_called()

    def test_missing_budget_does_not_start_request(self):
        with patch.object(public_reader,'public_addresses',return_value=(httpx.URL(URL),['93.184.216.34'])), patch.object(public_reader.check_budget,'remaining',return_value=2), patch.object(public_reader,'fetch_public') as fetch:
            with self.assertRaises(product_page.ProductBodyUnavailable): public_reader.read(URL)
            fetch.assert_not_called()

    def test_403_recovery_passes_through_existing_extraction(self):
        denied=httpx.Response(403,request=httpx.Request('GET',URL))
        error=httpx.HTTPStatusError('denied',request=denied.request,response=denied)
        recovered=httpx.Response(200,text=HTML,request=httpx.Request('GET',URL))
        with patch.object(ingest,'_fetch',side_effect=error),patch.object(public_reader,'read',return_value=recovered):
            result=ingest.from_url(URL,ocr_images=False,product_only=True)
        self.assertIn('6kg',result.text)
        self.assertEqual(URL,result.source_capture['source_url'])
        self.assertEqual('public_html_reader',result.source_capture['acquisition'][0]['stage'])
        self.assertIn('Jina Reader',result.notes[0])

    def test_authentication_and_rate_limits_not_retried(self):
        for status in (401,429):
            response=httpx.Response(status,request=httpx.Request('GET',URL))
            error=httpx.HTTPStatusError('denied',request=response.request,response=response)
            with patch.object(ingest,'_fetch',side_effect=error),patch.object(public_reader,'read') as read:
                with self.assertRaises(httpx.HTTPStatusError): ingest.from_url(URL,product_only=True)
                read.assert_not_called()

    def test_reader_failure_preserves_original_error(self):
        response=httpx.Response(403,request=httpx.Request('GET',URL))
        error=httpx.HTTPStatusError('denied',request=response.request,response=response)
        with patch.object(ingest,'_fetch',side_effect=error),patch.object(public_reader,'read',side_effect=ValueError('invalid')):
            with self.assertRaises(httpx.HTTPStatusError) as found: ingest.from_url(URL,product_only=True)
            self.assertIs(error,found.exception)

if __name__=='__main__': unittest.main()
