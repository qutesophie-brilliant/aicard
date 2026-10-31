import gzip
import unittest
from unittest.mock import patch

import httpx

from app import network_safety as safety

Client = httpx.Client


def dns(host, port, **kwargs):
    return [(2, 1, 6, '', ('93.184.216.34', port))]


class NetworkSafetyTests(unittest.TestCase):
    def client_factory(self, handler):
        return lambda **kwargs: Client(transport=httpx.MockTransport(handler), **kwargs)

    def test_private_literal_and_credential_urls_rejected_without_connection(self):
        blocked = ['file:///etc/passwd', 'http://127.0.0.1/', 'http://10.0.0.1/',
                   'http://169.254.169.254/', 'http://[::1]/', 'http://[::ffff:127.0.0.1]/',
                   'http://localhost/', 'http://a.local/', 'http://user:pass@example.com/']
        for url in blocked:
            with self.subTest(url=url), patch.object(safety.httpx, 'Client') as client:
                with self.assertRaises(safety.PublicURLRejected):
                    safety.fetch_public(url)
                client.assert_not_called()

    def test_mixed_public_private_dns_rejected(self):
        rows = dns('example.com', 443) + [(2, 1, 6, '', ('192.168.1.1', 443))]
        with patch.object(safety.socket, 'getaddrinfo', return_value=rows):
            with self.assertRaises(safety.PublicURLRejected):
                safety.public_addresses('https://example.com/product')

    def test_connects_to_pinned_address_but_keeps_host_tls_and_source_url(self):
        seen = []
        def handler(request):
            seen.append(request)
            return httpx.Response(200, text='source text')
        with patch.object(safety.socket, 'getaddrinfo', side_effect=dns) as resolver, \
             patch.object(safety.httpx, 'Client', side_effect=self.client_factory(handler)):
            result = safety.fetch_public('https://example.com/product?id=3#part')
        self.assertEqual(resolver.call_count, 1)
        self.assertEqual(seen[0].url.host, '93.184.216.34')
        self.assertEqual(seen[0].headers['host'], 'example.com')
        self.assertEqual(seen[0].extensions['sni_hostname'], 'example.com')
        self.assertEqual(str(result.url), 'https://example.com/product?id=3')

    def test_private_redirect_never_contacted(self):
        seen = []
        def handler(request):
            seen.append(request)
            return httpx.Response(302, headers={'Location':'http://169.254.169.254/latest/meta-data/'})
        with patch.object(safety.socket, 'getaddrinfo', side_effect=dns), \
             patch.object(safety.httpx, 'Client', side_effect=self.client_factory(handler)):
            with self.assertRaises(safety.PublicURLRejected):
                safety.fetch_public('https://example.com/product')
        self.assertEqual(len(seen), 1)

    def test_renderer_redirect_is_returned_without_following(self):
        seen=[]
        def handler(request):
            seen.append(request)
            return httpx.Response(302,headers={'Location':'http://127.0.0.1/private'})
        with patch.object(safety.socket,'getaddrinfo',side_effect=dns), \
             patch.object(safety.httpx,'Client',side_effect=self.client_factory(handler)):
            response=safety.fetch_public('https://example.com/product',follow_redirects=False)
        self.assertEqual(302,response.status_code)
        self.assertEqual(1,len(seen))
        with self.assertRaises(safety.PublicURLRejected):
            safety.fetch_public(response.headers['location'],follow_redirects=False)

    def test_public_redirect_revalidated_and_original_source_kept(self):
        seen = []
        def handler(request):
            seen.append(request)
            return (httpx.Response(302, headers={'Location':'https://cdn.example.com/final'})
                    if len(seen) == 1 else httpx.Response(200, text='final source'))
        with patch.object(safety.socket, 'getaddrinfo', side_effect=dns) as resolver, \
             patch.object(safety.httpx, 'Client', side_effect=self.client_factory(handler)):
            result = safety.fetch_public('https://example.com/product')
        self.assertEqual(resolver.call_count, 2)
        self.assertEqual(seen[-1].extensions['sni_hostname'], 'cdn.example.com')
        self.assertEqual(str(result.url), 'https://cdn.example.com/final')

    def test_response_size_limit_and_image_content_type(self):
        for image_only, headers, body in [(False, {}, b'123456'), (True, {'Content-Type':'text/html'}, b'bad')]:
            with self.subTest(image_only=image_only), patch.object(safety.socket, 'getaddrinfo', side_effect=dns), \
                 patch.object(safety.httpx, 'Client', side_effect=self.client_factory(
                     lambda request: httpx.Response(200, headers=headers, content=body))):
                with self.assertRaises(safety.PublicURLRejected):
                    safety.fetch_public('https://example.com/file', max_bytes=5, image_only=image_only)

    def test_gzip_source_is_not_decoded_twice(self):
        raw = 'AI source with a 6kg condition'.encode()
        def handler(request):
            return httpx.Response(200, headers={'Content-Encoding':'gzip'}, content=gzip.compress(raw))
        with patch.object(safety.socket, 'getaddrinfo', side_effect=dns), \
             patch.object(safety.httpx, 'Client', side_effect=self.client_factory(handler)):
            result = safety.fetch_public('https://example.com/product')
        self.assertEqual(result.content, raw)

    def test_redirect_limit_and_image_host_allowlist(self):
        def handler(request):
            return httpx.Response(302, headers={'Location':'https://other.example.com/a'})
        with patch.object(safety.socket, 'getaddrinfo', side_effect=dns), \
             patch.object(safety.httpx, 'Client', side_effect=self.client_factory(handler)):
            for kwargs in [{'max_redirects':0}, {'allowed_hosts':{'example.com'}}]:
                with self.subTest(kwargs=kwargs), self.assertRaises(safety.PublicURLRejected):
                    safety.fetch_public('https://example.com/product', **kwargs)

    def test_https_image_cannot_redirect_to_http(self):
        def handler(request):
            return httpx.Response(302, headers={'Location':'http://example.com/a'})
        with patch.object(safety.socket, 'getaddrinfo', side_effect=dns), \
             patch.object(safety.httpx, 'Client', side_effect=self.client_factory(handler)):
            with self.assertRaises(safety.PublicURLRejected):
                safety.fetch_public('https://example.com/product', https_only=True)


if __name__ == '__main__':
    unittest.main()
