import os
import unittest
from unittest.mock import patch
from urllib.parse import unquote
from fastapi.testclient import TestClient
from app import main
from app.pipeline import browser_capture, ingest, product_page

URL='https://seller.example/product/1'
HTML='<title>AI 세탁기</title><div class="product-description"><h3>AI 맞춤 세탁</h3><p>옷감과 무게를 감지하여 세탁합니다.</p><p>AI 맞춤 세탁은 세탁물 6kg 이하에서 사용합니다.</p><img src="https://cdn.example/detail.jpg"><form>PRIVATE<input value="secret"></form><script>secret()</script></div>'


class BrowserCaptureTests(unittest.TestCase):
    def setUp(self):
        self.hits=main._check_hits.copy(); main._check_hits.clear()
    def tearDown(self):
        main._check_hits.clear(); main._check_hits.update(self.hits)
    def test_selected_source_images_are_sent_to_ocr_without_forms_or_scripts(self):
        result=ingest.Ingested('x','AI',[],source_capture={})
        with patch.object(browser_capture,'public_addresses'), patch.object(ingest,'from_product_html',return_value=result) as parse:
            got=browser_capture.read(HTML,URL)
        html,url=parse.call_args.args
        self.assertEqual(URL,url)
        self.assertTrue(parse.call_args.kwargs['ocr_images'])
        self.assertIn('cdn.example/detail.jpg',html)
        self.assertIn('6kg',html)
        self.assertNotIn('PRIVATE',html); self.assertNotIn('secret',html)
        self.assertEqual('user_browser_capture',got.source_capture['acquisition'][0]['stage'])
    def test_private_page_never_reaches_ocr(self):
        with patch.object(ingest,'from_product_html') as parse:
            with self.assertRaises(ValueError): browser_capture.read(HTML,'http://127.0.0.1/private')
            parse.assert_not_called()
    def test_unidentified_page_is_not_accepted_as_product(self):
        with patch.object(browser_capture,'public_addresses'):
            with self.assertRaises(product_page.ProductBodyUnavailable): browser_capture.read('<body>AI 개인정보가 들어 있는 일반 화면입니다.</body>',URL)
    def test_api_rejects_invalid_and_oversized_payload(self):
        client=TestClient(main.app)
        for payload in ([], {'product_html':{}}, {'url':1}):
            self.assertEqual(400,client.post('/api/check',json=payload).status_code)
        self.assertEqual(413,client.post('/api/check',content=b'x'*1600001).status_code)
    def test_api_failed_capture_does_not_create_quota_or_card(self):
        with patch.object(main,'_match_published',return_value=None), patch.object(browser_capture,'read',side_effect=ValueError('no product')), patch.object(main.db,'create_product') as add:
            r=TestClient(main.app).post('/api/check',json={'url':URL,'product_html':HTML})
        self.assertEqual(200,r.status_code); add.assert_not_called()
        self.assertIn('recovery-card',r.text)
        self.assertIn('분석을 완료한 카드가 아닙니다',r.text)
        self.assertNotIn('testclient:ocr',main._check_hits)
    def test_api_carries_evidence_and_capture_provenance(self):
        result=ingest.from_product_html(HTML,URL,ocr_images=False)
        result.notes=['사용자 브라우저에서 보낸 자료']
        with patch.object(main,'_match_published',return_value=None), patch.object(browser_capture,'read',return_value=result), patch.object(main.config,'CHECK_LLM',False):
            r=TestClient(main.app).post('/api/check',json={'url':URL,'title':'AI 세탁기','product_html':HTML})
        self.assertEqual(200,r.status_code); self.assertIn('사용자 브라우저',r.text); self.assertIn('6kg',r.text)

    @unittest.skipUnless(os.getenv('RUN_RENDERER_TESTS')=='1','requires Chromium')
    def test_bookmarklet_transfers_lazy_images_not_credentials_or_reviews(self):
        from playwright.sync_api import sync_playwright
        script=unquote(main._bookmarklet('https://ai.example','/check/receive')[11:])
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            try:
                page=browser.new_page()
                page.route('**/*',lambda route: route.fulfill(status=200,content_type='text/html',body=HTML.replace('https://cdn.example/detail.jpg','/placeholder.png').replace('<img src=', '<img data-src="https://cdn.example/detail.jpg" src=')+'<div class="reviews">BAD REVIEW</div>'))
                page.goto(URL)
                page.evaluate("""() => { window.open = () => { const w={postMessage:d=>window.captureResult=d}; setTimeout(()=>{const e=new Event('message'); Object.defineProperties(e,{source:{value:w},origin:{value:'https://ai.example'},data:{value:'aicard-ready'}});window.dispatchEvent(e)},10);return w; }; }""")
                page.evaluate(script)
                page.wait_for_function('window.captureResult')
                data=page.evaluate('window.captureResult')
                self.assertIn('https://cdn.example/detail.jpg',data['product_html'])
                self.assertNotIn('PRIVATE',data['product_html']); self.assertNotIn('secret',data['product_html']); self.assertNotIn('BAD REVIEW',data['product_html'])
                self.assertEqual(URL,data['url'])
                self.assertEqual({'type','url','title','product_html'},set(data))
            finally: browser.close()
