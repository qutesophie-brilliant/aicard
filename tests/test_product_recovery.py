import io
import json
import unittest
from unittest.mock import patch
import httpx
from PIL import Image
from app.pipeline import product_page, product_documents, product_images, source_capture, ingest, ocr
from app.network_safety import PublicURLRejected

URL = 'https://seller.example/item/1'

def png(width=100, height=100):
    with io.BytesIO() as out:
        Image.new('RGB', (width,height),'white').save(out,format='PNG')
        return out.getvalue()

class ProductRecoveryTests(unittest.TestCase):
    def test_other_samsung_store_uses_identified_features_not_reviews(self):
        html='<article id="compGoodsFeatures">AI가 무게와 옷감에 맞춰 세탁합니다. 해당 기능의 사용 조건은 세탁물 6kg 이하입니다.</article><div class="reviews">AI가 모든 것을 해결합니다.</div>'
        text=product_page.product_text(product_page.product_body(html,URL))
        self.assertIn('6kg',text); self.assertNotIn('모든 것을',text)

    def test_embedded_product_json_excludes_recommendations(self):
        data={'props':{'pageProps':{'product':{'detailHtml':'<p>AI가 온도를 분석하여 냉장실을 조절합니다. 문을 닫고 사용합니다.</p>'},'recommendations':{'product':{'detailHtml':'다른 제품 AI 설명'}}}}}
        html='<script id="__NEXT_DATA__" type="application/json">'+json.dumps(data)+'</script>'
        text=product_page.product_text(product_page.product_body(html,URL))
        self.assertIn('냉장실',text); self.assertNotIn('다른 제품',text)

    def test_single_product_schema_only(self):
        product={'@type':'Product','description':'AI가 실내 온도에 맞춰 냉방합니다. 문을 닫은 환경에서 사용합니다.'}
        html='<script type="application/ld+json">'+json.dumps({'@graph':[product]})+'</script>'
        self.assertIn('냉방',product_page.product_text(product_page.product_body(html,URL)))
        multi='<script type="application/ld+json">'+json.dumps([product,product])+'</script>'
        with self.assertRaises(product_page.ProductBodyUnavailable): product_page.product_body(multi,URL)

    def test_lazy_srcset_chooses_one_real_image(self):
        from bs4 import BeautifulSoup
        node=BeautifulSoup('<img src="data:image/png;base64,AA" data-srcset="/small.jpg 400w, /large.jpg 1200w">','html.parser').img
        self.assertEqual('/large.jpg',product_page.image_source(node))

    def test_description_iframe_keeps_actual_source_not_tracking_frame(self):
        html='<iframe src="https://tracking.test/pixel"></iframe><iframe id="detailIfrm" src="https://detail.example/item/1"></iframe>'
        response=httpx.Response(200,headers={'content-type':'text/html'},text='<html><body><p>AI가 무게와 옷감을 감지하여 세탁합니다. 6kg 이하에서 사용합니다.</p><img src="/detail.jpg"><nav>다른 상품 AI</nav></body></html>',request=httpx.Request('GET','https://detail.example/item/1'))
        with patch.object(ingest,'_fetch',return_value=response) as fetch:
            processed, records=product_documents.prepare(html,'https://www.11st.co.kr/products/1',ingest._fetch)
        fetch.assert_called_once_with('https://detail.example/item/1')
        capture=source_capture.capture_product_html(processed,'https://www.11st.co.kr/products/1')
        self.assertEqual('https://detail.example/item/1',capture['sections'][0]['source_url'])
        self.assertNotIn('다른 상품',capture['text']); self.assertEqual('read',records[0]['status'])
        self.assertIn('https://detail.example/detail.jpg',processed)

    def test_frame_private_address_rejection_does_not_discard_parent_text(self):
        html='<div class="product-description">AI가 무게와 옷감을 감지하여 세탁합니다. 6kg 이하에서 사용합니다.<iframe src="http://127.0.0.1/private"></iframe></div>'
        with patch.object(ingest,'_fetch',side_effect=PublicURLRejected('private')):
            processed, records=product_documents.prepare(html,URL,ingest._fetch)
        self.assertEqual('unavailable',records[0]['status'])
        self.assertIn('6kg',product_page.product_text(product_page.product_body(processed,URL)))

    def test_cross_host_images_still_use_public_address_guard(self):
        html='<div class="product-description"><img src="http://127.0.0.1/private.png"></div>'
        capture={'sections':[],'coverage':{}}
        product_images.capture_images(html,URL,capture)
        self.assertEqual('download_failed',capture['images'][0]['status'])
        self.assertEqual(0,len(capture['sections']))

    def test_fragmented_page_reads_more_than_six_images(self):
        html='<div class="product-description">'+''.join(f'<img src="https://cdn.example/{i}.png">' for i in range(15))+'</div>'
        capture={'sections':[],'coverage':{}}
        product_images.capture_images(html,URL,capture,fetch_bytes=lambda _:png(),reader=lambda _:ocr.OcrResult(texts=['AI가 온도를 감지합니다.'],engine='test'))
        self.assertEqual(15,len(capture['sections']))
        self.assertEqual(0,capture['coverage']['image_skipped_limit'])

    def test_tile_limit_preserves_preceding_and_later_small_images(self):
        html='<div class="product-description"><img src="/a.png"><img src="/large.png"><img src="/b.png"></div>'
        capture={'sections':[],'coverage':{}}
        product_images.capture_images(html,URL,capture,fetch_bytes=lambda u:png(800,32890) if 'large' in u else png(),reader=lambda _:ocr.OcrResult(texts=['AI가 온도를 감지합니다.'],engine='test'))
        # 1 + 23 consumes 24 tiles; third image is explicitly unprocessed.
        self.assertEqual(['read','read','skipped_limit'],[r['status'] for r in capture['images']])
        self.assertEqual(2,len(capture['sections']))

    def test_login_response_is_not_an_ai_absence_result(self):
        with self.assertRaisesRegex(product_page.ProductBodyUnavailable,'로그인 화면'):
            product_documents.prepare('<title>NAVER 로그인</title>','https://nid.naver.com/nidlogin.login',lambda _:None)

    def test_escaped_generic_description_is_decoded(self):
        body=product_page.product_body('<div id="sit_inf_explan">&lt;img src="https://cdn.example/detail.jpg"&gt;</div>',URL)
        self.assertEqual('https://cdn.example/detail.jpg',product_page.image_source(body.img))

if __name__=='__main__': unittest.main()
