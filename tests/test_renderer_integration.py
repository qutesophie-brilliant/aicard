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

    def test_opens_description_once_without_clicking_purchase(self):
        html='''<html><body><button onclick="document.querySelector('#productDescription').innerHTML=document.querySelector('#productDescription').innerHTML?'':'AI가 온도와 사용 패턴을 분석하여 냉각을 조절합니다. 문을 닫은 상태에서 작동합니다.'">상품 상세정보</button>
        <button onclick="document.body.innerHTML='PURCHASE_CLICKED'">구매하기</button>
        <div id="productDescription"></div></body></html>'''
        def fetch(url,**kw):
            return httpx.Response(200,text=html,headers={'content-type':'text/html; charset=utf-8'},request=httpx.Request('GET',url))
        result=rendered_page.run_browser('https://seller.example/product/42',fetch)
        text=product_page.product_text(product_page.product_body(result['html'],result['url']))
        self.assertIn('냉각',text)
        from bs4 import BeautifulSoup
        self.assertNotIn('PURCHASE_CLICKED',BeautifulSoup(result['html'],'html.parser').get_text())


if __name__=='__main__': unittest.main()
