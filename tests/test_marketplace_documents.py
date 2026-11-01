import json
import unittest
from unittest.mock import Mock
import httpx
from app.pipeline import product_documents, product_page, source_capture


class PublishedMarketplaceDescriptionTests(unittest.TestCase):
    gs_url = 'https://m.gsshop.com/prd/prd.gs?prdid=1068304374&rank=10'
    gs_detail = 'https://m.gsshop.com/prd/prdDesc.gs?prdid=1068304374'

    def gs_parent(self, product=1068304374, href='/prd/prdDesc.gs?prdid=1068304374'):
        return '<product-description></product-description><script>var renderJson = ' + json.dumps({
            'prd': {'prdCd': product, 'prdDescUrl': href},
            'prdrevw': {'description': 'WRONG_REVIEW_AI'}}) + ';</script>'

    def response(self, url, html):
        return httpx.Response(200, text=html, headers={'content-type': 'text/html'}, request=httpx.Request('GET', url))

    def gs_child(self):
        return '<div id="productDescription"></div><script>var renderJson = ' + json.dumps({
            'prd': {'prdImgDescd': '<p>AI 습도센서로 분무량을 자동 조절합니다.</p><img data-goods-lazy="/images/detail.jpg"><script>UNTRUSTED_SCRIPT</script>'},
            'prdrevw': {'detailHtml': '<p>WRONG_REVIEW_AI</p>'}}).replace('</script>', '<\\/script>') + ';</script>'

    def test_gs_declared_description_recovers_text_and_lazy_images(self):
        fetch = Mock(return_value=self.response(self.gs_detail, self.gs_child()))
        prepared, records = product_documents.prepare(self.gs_parent(), self.gs_url, fetch)
        fetch.assert_called_once_with(self.gs_detail)
        body = product_page.product_body(prepared, self.gs_url)
        self.assertIn('습도센서', body.get_text())
        self.assertNotIn('WRONG_REVIEW_AI', body.get_text())
        self.assertNotIn('UNTRUSTED_SCRIPT', body.get_text())
        self.assertEqual('https://m.gsshop.com/images/detail.jpg', product_page.image_source(body.img))
        self.assertEqual('read', records[0]['status'])

    def test_gs_wrong_product_or_non_description_never_fetched(self):
        for product, href in [
            (9999999999, '/prd/prdDesc.gs?prdid=1068304374'),
            (1068304374, '/prd/prdDesc.gs?prdid=9999999999'),
            (1068304374, '/prd/prdDesc.gs?prdid=1068304374&prdid=9999999999'),
            (1068304374, 'https://other.example/prd/prdDesc.gs?prdid=1068304374'),
            (1068304374, '/my/order.gs?prdid=1068304374')]:
            with self.subTest(href=href, product=product):
                fetch = Mock()
                product_documents.prepare(self.gs_parent(product, href), self.gs_url, fetch)
                fetch.assert_not_called()

    def test_gs_product_name_is_preserved_with_image_only_description(self):
        name = '아이닉 AI습도조절 가습기'
        html = self.gs_parent().replace('"prdCd":', '"prdNm": ' + json.dumps(name) + ', "prdCd":')
        html += '<meta property="og:title" content="[GS SHOP] ' + name + '">'
        fetch = Mock(return_value=self.response(self.gs_detail, self.gs_child()))
        prepared, _ = product_documents.prepare(html, self.gs_url, fetch)
        body = product_page.product_body(prepared, self.gs_url)
        self.assertIn(name, body.get_text())
        self.assertTrue(any(n.get('data-source-url') == self.gs_url for n in body.select('[data-source-url]')))
        captured = source_capture.capture_product_html(prepared, self.gs_url)
        names = [s for s in captured['sections'] if name in s['text']]
        explanations = [s for s in captured['sections'] if '습도센서' in s['text']]
        self.assertEqual([self.gs_url], [s['source_url'] for s in names])
        self.assertEqual([self.gs_detail], [s['source_url'] for s in explanations])
        mismatch = html.replace('[GS SHOP] ' + name, '[GS SHOP] 다른 상품')
        prepared, _ = product_documents.prepare(mismatch, self.gs_url, fetch)
        self.assertNotIn(name, product_page.product_body(prepared, self.gs_url).get_text())

    def test_gs_redirect_or_empty_script_is_not_success(self):
        for url, html in [
            (self.gs_detail.replace('1068304374', '9999999999'), self.gs_child()),
            (self.gs_detail, '<div id="productDescription"></div>'),
            (self.gs_detail, self.gs_child().replace(';</script>', ' + unknownValue;</script>'))]:
            with self.subTest(url=url, html=html):
                fetch = Mock(return_value=self.response(url, html))
                prepared, records = product_documents.prepare(self.gs_parent(), self.gs_url, fetch)
                self.assertEqual('unavailable', records[0]['status'])
                self.assertNotIn('습도센서', prepared)

    def eleven_parent(self):
        return '''<script>var _PRODUCT_DETAIL_URL_ = "http://www.11st.co.kr/product/SellerProductDetail.tmall?method=getSellerProductDetail&prdNo=";</script>
        <div id="ifrmDesc"><iframe id="prdDescIfrm" src="about:blank"></iframe></div>
        <script data-main="//www.11st.co.kr/js/ver/7ec6924/product.common.js?v=1"></script>'''

    def test_eleven_current_iframe_loads_images_not_legacy_parent_redirect(self):
        url = 'https://www.11st.co.kr/products/9594783865'
        detail = url + '/view-desc'
        fetch = Mock(return_value=self.response(detail, '<body><nav>WRONG_NAV</nav><div class="ifrm_prdc_detail"><p>AI로 장애물을 인식합니다.</p><img src="/detail.jpg"></div><div><img src="/late-detail.jpg"></div></body>'))
        prepared, records = product_documents.prepare(self.eleven_parent(), url, fetch)
        fetch.assert_called_once_with(detail)
        body = product_page.product_body(prepared, url)
        self.assertIn('장애물', body.get_text())
        self.assertNotIn('WRONG_NAV', body.get_text())
        self.assertEqual('https://www.11st.co.kr/detail.jpg', body.img['src'])
        self.assertEqual(2, len(body.select('img')))
        self.assertEqual('read', records[0]['status'])

    def test_eleven_other_item_redirect_and_unrelated_body_rejected(self):
        url = 'https://www.11st.co.kr/products/9594783865'
        for target, html in [
            ('https://www.11st.co.kr/products/9999999999/view-desc', '<div class="ifrm_prdc_detail">WRONG_PRODUCT</div>'),
            ('https://other.example/products/9594783865/view-desc', '<div class="ifrm_prdc_detail">WRONG_PRODUCT</div>'),
            ('https://other.example/seller-page', '<div class="ifrm_prdc_detail">WRONG_PRODUCT</div>'),
            (url + '/view-desc', '<body>WRONG_PRODUCT</body>')]:
            fetch = Mock(return_value=self.response(target, html))
            prepared, records = product_documents.prepare(self.eleven_parent(), url, fetch)
            self.assertNotIn('WRONG_PRODUCT', prepared)
            self.assertEqual('unavailable', records[0]['status'])

    def test_eleven_visible_ai_product_heading_not_lost_after_images_recovered(self):
        url = 'https://www.11st.co.kr/products/9594783865'
        name = 'LG HOM-BOT AI 로봇청소기 N95TWU'
        html = self.eleven_parent() + '<h1 class="title">' + name + '</h1><meta property="og:title" content="[11번가] ' + name + '">'
        fetch = Mock(return_value=self.response(url+'/view-desc', '<div class="ifrm_prdc_detail"><img src="/detail.jpg"></div>'))
        prepared, _ = product_documents.prepare(html, url, fetch)
        self.assertIn(name, product_page.product_body(prepared, url).get_text())
        captured = source_capture.capture_product_html(prepared, url)
        self.assertEqual([url], [s['source_url'] for s in captured['sections'] if name in s['text']])
