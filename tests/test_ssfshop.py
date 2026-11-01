import io
import unittest
from unittest.mock import patch
import httpx
from PIL import Image
from app import network_safety
from app.pipeline import ssfshop, ingest, product_images, product_page, ocr

URL = 'https://m.ssfshop.com/DAROOM/GQWB25051512990/good'
DETAIL = 'https://m.ssfshop.com/public/goods/viewGoodsLazyLoading?godNo=GQWB25051512990'
PARENT = '<title>다룸 AI 선풍기</title><input id="godNo" value="GQWB25051512990">'
BODY = '''<div id="goodsInfoTabArea"><div class="prd-pinch-wrapper"><section>
<div id="detailImgDiv"><p>AI 모드로 주변 온도에 따라 풍속을 조절합니다.</p>
<img data-goods-lazy="https://cdn.example/detail.jpg" src="/transparent.png"></div>
</section></div><div id="similarUserGoodList">다른 제품은 AI 카메라로 사물을 인식합니다.</div></div>'''


class SSFShopTests(unittest.TestCase):
    def response(self, body=BODY, url=DETAIL):
        return httpx.Response(200, text=body, headers={'content-type': 'text/html'},
                              request=httpx.Request('GET', url))

    def test_product_identity_and_tracking(self):
        self.assertEqual(DETAIL, ssfshop.detail_url(URL+'?utm_source=search'))
        self.assertEqual(DETAIL.replace('12990','77777'), ssfshop.detail_url(URL.replace('12990','77777')))
        for url in [URL+'/other', URL.replace('m.ssfshop.com','evil.example'),
                    URL.replace('https://','https://user:pass@'), URL.replace('m.ssfshop.com','m.ssfshop.com:8000')]:
            self.assertIsNone(ssfshop.detail_url(url))

    def test_ingest_reads_lazy_description_before_browser_and_excludes_recommendations(self):
        with patch.object(ingest, '_fetch', return_value=self.response(PARENT, URL)), \
             patch.object(network_safety, 'fetch_public', return_value=self.response()) as fetch, \
             patch('app.pipeline.page_recovery.acquire') as generic:
            result = ingest.from_url(URL, product_only=True, ocr_images=False)
        generic.assert_not_called()
        self.assertIn('주변 온도', result.text)
        self.assertNotIn('카메라', result.text)
        self.assertEqual(URL, result.source_capture['source_url'])
        self.assertEqual(DETAIL, result.source_capture['documents'][0]['url'])
        self.assertEqual({'m.ssfshop.com'}, fetch.call_args.kwargs['allowed_hosts'])

    def test_wrong_product_or_missing_detail_rejected(self):
        for response in [self.response(url=DETAIL+'9'), self.response('<title>로그인</title>'),
                         self.response('<div class="product-description">추천상품만 있음</div>')]:
            with patch.object(network_safety, 'fetch_public', return_value=response):
                with self.assertRaises(product_page.ProductBodyUnavailable):
                    ssfshop.read_product(URL, PARENT, ocr_images=False)
        with patch.object(network_safety, 'fetch_public') as fetch:
            with self.assertRaises(product_page.ProductBodyUnavailable):
                ssfshop.read_product(URL, PARENT.replace('12990','77777'), ocr_images=False)
        fetch.assert_not_called()

    def test_lazy_detail_images_reach_shared_ocr(self):
        from bs4 import BeautifulSoup
        with patch.object(network_safety, 'fetch_public', return_value=self.response()), \
             patch.object(product_images, 'capture_images') as capture:
            ssfshop.read_product(URL, PARENT, ocr_images=True)
        image = BeautifulSoup(capture.call_args.args[0], 'html.parser').img
        self.assertEqual('https://cdn.example/detail.jpg', product_page.image_source(image))

    def test_42140_pixel_detail_is_not_discarded_before_ocr(self):
        with Image.new('RGB', (860,42140), 'white') as im:
            out = io.BytesIO(); im.save(out,format='PNG'); data=out.getvalue()
        capture = {'sections': [], 'coverage': {}}
        product_images.capture_images('<div class="product-description"><img src="/detail.jpg"></div>',
            URL, capture, fetch_bytes=lambda _: data,
            reader=lambda _: ocr.OcrResult(texts=['AI 모드로 주변 온도에 따라 풍속을 조절합니다.'],engine='test'))
        self.assertEqual('read', capture['images'][0]['status'])
        self.assertEqual(0, capture['coverage']['image_skipped_limit'])
        self.assertIn('주변 온도', capture['sections'][0]['text'])


if __name__ == '__main__': unittest.main()
