import unittest
from unittest.mock import patch
from urllib.parse import unquote
from app import main
from app.pipeline import product_page, browser_capture, ingest


URL = 'https://smartstore.naver.com/example/products/123456'
TEXT = 'AI 맞춤 운전은 실내 습도를 감지하여 가습량을 자동으로 조절합니다.'


class NaverProductCaptureTests(unittest.TestCase):
    def test_both_editors_preserve_description_and_lazy_images(self):
        for wrapper in ('<div id="pib_detail_page">{}</div>',
                        '<div class="editor_wrap se_smartstore_wrap"><div class="se-main-container">{}</div></div>'):
            html = wrapper.format('<p>' + TEXT + '</p><img src="data:image/png;base64,placeholder" data-src="https://cdn.example/detail.jpg">')
            html += '<div id="REVIEW"><div class="se-main-container">AI 리뷰 제외</div></div><div id="RECOMMEND">다른 상품 AI 설명 제외</div>'
            for host in ('smartstore.naver.com', 'm.smartstore.naver.com', 'brand.naver.com', 'm.brand.naver.com'):
                with self.subTest(wrapper=wrapper, host=host):
                    body = product_page.product_body(html, URL.replace('smartstore.naver.com', host))
                    self.assertIn(TEXT, product_page.product_text(body))
                    self.assertEqual('https://cdn.example/detail.jpg', product_page.image_source(body.img))
                    self.assertNotIn('리뷰 제외', str(body))
                    self.assertNotIn('다른 상품', str(body))

    def test_generic_smarteditor_and_other_site_are_not_product_evidence(self):
        with self.assertRaises(product_page.ProductBodyUnavailable):
            product_page.product_body('<div class="se-main-container">' + TEXT + '</div>', URL)
        for url in ('https://blog.naver.com/example/123456', 'https://smartstore.naver.com/example/category/123456'):
            with self.assertRaises(product_page.ProductBodyUnavailable):
                product_page.product_body('<div id="pib_detail_page">' + TEXT + '</div>', url)

    def test_capture_flows_to_ocr_without_account_data(self):
        html = '<div class="editor_wrap se_smartstore_wrap"><div class="se-main-container"><p>' + TEXT + '</p><img src="https://cdn.example/detail.jpg"><form>PRIVATE<input value="secret"></form></div></div><nav>ACCOUNT</nav><script>private()</script>'
        result = ingest.Ingested('x', TEXT, [], source_capture={})
        with patch.object(browser_capture, 'public_addresses'), patch.object(ingest, 'from_product_html', return_value=result) as parse:
            got = browser_capture.read(html, URL)
        self.assertTrue(parse.call_args.kwargs['ocr_images'])
        for secret in ('PRIVATE', 'secret', 'ACCOUNT', 'private()'):
            self.assertNotIn(secret, parse.call_args.args[0])
        self.assertEqual('user_browser_capture', got.source_capture['acquisition'][0]['stage'])

    def test_consumer_bookmarklet_contains_scoped_naver_editors(self):
        js = unquote(main._bookmarklet('https://ai.example', '/check/receive')[11:])
        self.assertNotIn('__NAVER', js)
        self.assertIn('naverProduct ?', js)
        self.assertIn('editor_wrap.se_smartstore_wrap:has(.se-main-container)', js)
        self.assertIn('data-src', js)

    def test_selected_capture_root_keeps_context_for_server_reparse(self):
        from bs4 import BeautifulSoup
        for wrapper in ('<div id="pib_detail_page">{}</div>',
                        '<div class="editor_wrap se_smartstore_wrap"><div class="se-main-container">{}</div></div>'):
            soup = BeautifulSoup(wrapper.format('<p>' + TEXT + '</p>'), 'html.parser')
            transferred = ''.join(str(n) for n in soup.select(product_page.NAVER_DETAIL_SELECTOR))
            self.assertIn(TEXT, product_page.product_text(product_page.product_body(transferred, URL)))

