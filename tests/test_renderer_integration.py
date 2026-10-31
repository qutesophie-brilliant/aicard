"""Opt-in real-engine test. All retailer responses are controlled fixtures.

No external retailer, AI provider, cookies or user browser session is accessed.
"""
import os
import unittest
import httpx
from app.pipeline import rendered_page, product_page


@unittest.skipUnless(os.getenv('RUN_RENDERER_TESTS') == '1', 'requires installed Chromium')
class RendererIntegrationTests(unittest.TestCase):
    def test_dynamic_get_description_with_source_image_and_no_post(self):
        seen=[]
        html='''<html><head><title>제품 설명</title></head><body><div id="app"></div>
        <script>
        fetch('/detail.json').then(r=>r.json()).then(d=>{
            document.querySelector('#app').innerHTML='<div class="product-description">'+d.html+'</div>';
        });
        fetch('/purchase', {method:'POST',body:'must not send'}).catch(()=>{});
        new WebSocket('wss://seller.example/private');
        </script></body></html>'''
        def fetch(url,**kw):
            seen.append(url)
            if url == 'https://seller.example/product/42':
                return httpx.Response(200,text=html,headers={'content-type':'text/html; charset=utf-8'},request=httpx.Request('GET',url))
            if url == 'https://seller.example/detail.json':
                return httpx.Response(200,json={'html':'<p>AI가 무게와 옷감을 감지하여 세탁합니다. 세탁물 6kg 이하에서 사용합니다.</p><img data-src="https://cdn.example/detail.jpg" src="/placeholder.png">'},request=httpx.Request('GET',url))
            raise AssertionError('unexpected network request: '+url)
        result=rendered_page.run_browser('https://seller.example/product/42',fetch)
        body=product_page.product_body(result['html'], result['url'])
        self.assertIn('6kg', product_page.product_text(body))
        self.assertEqual('https://cdn.example/detail.jpg',product_page.image_source(body.img))
        self.assertNotIn('https://seller.example/purchase',seen)
        self.assertEqual(2,len(seen))

    def test_rendered_description_frame_preserves_source(self):
        from app.pipeline.source_capture import capture_product_html
        def fetch(url,**kw):
            if url.endswith('/42'):
                html='<html><body><iframe id="detailIfrm" src="/description"></iframe></body></html>'
            elif url.endswith('/description'):
                html='<html><body><script>document.body.innerHTML="<p>AI가 무게와 옷감을 감지하여 세탁합니다. 세탁물 6kg 이하에서 사용합니다.</p>"</script></body></html>'
            else: raise AssertionError(url)
            return httpx.Response(200,text=html,headers={'content-type':'text/html; charset=utf-8'},request=httpx.Request('GET',url))
        result=rendered_page.run_browser('https://seller.example/product/42',fetch)
        capture=capture_product_html(result['html'],result['url'])
        self.assertIn('6kg',capture['text'])
        self.assertEqual('https://seller.example/description',capture['sections'][0]['source_url'])


if __name__=='__main__': unittest.main()
