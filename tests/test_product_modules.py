import unittest
from unittest.mock import Mock
import httpx
from app.pipeline.product_page import product_body, product_text, ProductBodyUnavailable
from app.pipeline.product_documents import prepare, published_seller_frame
from bs4 import BeautifulSoup


class ProductModuleTests(unittest.TestCase):
    def test_labelled_feature_list_excludes_neighbouring_product(self):
        html = '''<main><div><div><p data-testid="target-text">Key features</p></div>
        <div><ul><li>AI selects wash settings based on the detected fabric.</li>
        <li>This function requires a load below 6 kg.</li></ul></div></div>
        <div class="recommendations">Another AI dryer dries in ten minutes.</div></main>'''
        text = product_text(product_body(html, 'https://unseen.example/p/washer'))
        self.assertIn('6 kg', text)
        self.assertIn('detected fabric', text)
        self.assertNotIn('dryer', text)

    def test_roborock_feature_module_keeps_caveats_not_reviews(self):
        html = '''<main><div class="Saro10R_product-container"><section>
        <h2>AI obstacle recognition</h2><p>Recognises objects using the camera.</p>
        <p>Recognition depends on lighting conditions.</p></section>
        <div class="reviews">AI works perfectly in total darkness.</div></div>
        <div class="recommendations">Another model climbs stairs.</div></main>'''
        text = product_text(product_body(html, 'https://global.roborock.com/pages/robot'))
        self.assertIn('lighting conditions', text)
        self.assertNotIn('total darkness', text)
        self.assertNotIn('climbs', text)

    def test_soundcore_feature_slides_exclude_promotion_carousel(self):
        html = '''<div class="swiper-slide">New AI headphones on sale.</div><main>
        <div class="swiper-slide"><div class="text-pretty">Six mics use AI noise reduction for calls.</div></div>
        <div class="sensor-lists"><p>Measured under laboratory conditions; results vary.</p></div>
        <div class="reviews">AI hears every voice flawlessly.</div></main>'''
        text = product_text(product_body(html, 'https://www.soundcore.com/products/earbuds'))
        self.assertIn('Six mics', text)
        self.assertIn('results vary', text)
        self.assertNotIn('on sale', text)
        self.assertNotIn('flawlessly', text)

    def test_narwal_modules_exclude_buy_box_and_customer_reviews(self):
        html = '''<div id="main-content"><section class="shopify-section product-buy-box-section">Other robot model AI deal</section>
        <div class="shopify-section"><section class="ndev-x30-product-parameters"><p>AI identifies objects with dual cameras.</p></section></div>
        <div class="shopify-section"><section class="ndev-x30-install-environment"><p>Install on a hard floor with 30 cm clearance.</p></section></div>
        <div class="shopify-section"><div class="reviews">AI never gets stuck.</div></div></div>'''
        text = product_text(product_body(html, 'https://us.narwal.com/products/robot'))
        self.assertIn('dual cameras', text)
        self.assertIn('30 cm', text)
        self.assertNotIn('Other robot', text)
        self.assertNotIn('never gets stuck', text)

    def test_published_frame_uses_current_product_and_keeps_image_source(self):
        url = 'https://www.11st.co.kr/products/12345'
        base = 'http://www.11st.co.kr/product/SellerProductDetail.tmall?method=getSellerProductDetail&prdNo='
        html = '<script>var _PRODUCT_DETAIL_URL_ = "'+base+'";</script><div id="ifrmDesc"><iframe id="prdDescIfrm" src="about:blank"></iframe></div>'
        response = httpx.Response(200, text='<p>AI detects motion and follows people within view.</p><img src="/detail.jpg">',
            headers={'content-type':'text/html'},request=httpx.Request('GET',base+'12345'))
        fetch = Mock(return_value=response)
        prepared, records = prepare(html,url,fetch)
        fetch.assert_called_once_with(base+'12345')
        body=product_body(prepared,url)
        self.assertIn('within view', body.get_text())
        self.assertEqual('http://www.11st.co.kr/detail.jpg',body.img['src'])
        self.assertEqual(base+'12345',records[0]['url'])

    def test_frame_rejects_ambiguous_or_unrelated_prefix(self):
        for base in ('http://evil.example/product/SellerProductDetail.tmall?method=getSellerProductDetail&prdNo=',
                     'http://www.11st.co.kr/product/SellerProductDetail.tmall?method=review&prdNo=',
                     'http://www.11st.co.kr/product/SellerProductDetail.tmall?method=getSellerProductDetail&prdNo=99',
                     'http://127.0.0.1/product/SellerProductDetail.tmall?method=getSellerProductDetail&prdNo='):
            with self.subTest(base=base):
                soup=BeautifulSoup('<script>var _PRODUCT_DETAIL_URL_ = "'+base+'";</script><div id="ifrmDesc"><iframe id="prdDescIfrm" src="about:blank"></iframe></div>','html.parser')
                self.assertIsNone(published_seller_frame(soup,'https://www.11st.co.kr/products/12345'))

    def test_soft_404_is_not_a_product_even_with_recommendations(self):
        html='<main><h1>Page Not Found</h1><div class="product-description">Try this other AI vacuum with automatic cleaning.</div></main>'
        fetch=Mock()
        with self.assertRaises(ProductBodyUnavailable):prepare(html,'https://seller.example/pages/old',fetch)
        fetch.assert_not_called()

    def test_detail_redirect_to_parent_does_not_import_shop_page(self):
        url='https://seller.example/products/123'
        html='<div class="product-description"><iframe src="/old-detail"></iframe></div>'
        response=httpx.Response(200,text='<h1>Shop</h1><p>Another AI product on sale.</p>',
            headers={'content-type':'text/html'},request=httpx.Request('GET',url))
        prepared,records=prepare(html,url,Mock(return_value=response))
        self.assertEqual('unavailable',records[0]['status'])
        self.assertNotIn('Another AI product',prepared)


if __name__ == '__main__': unittest.main()
