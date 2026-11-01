import unittest
from unittest.mock import patch
import httpx
from app.pipeline import lotteon, ingest, product_page, product_documents
from app import network_safety

URL = 'https://www.lotteon.com/p/product/LO2635377150'
DETAIL = 'https://llis-goodsdetail.lotteon.com/goodsdetail?model=itemdetail%2FLO%2F26%2F35%2F37%2F71%2F50%2FDSCRP_LO2635377150'
HTML = '<title>llis-goodsdetail</title><div id="llis-root"><p>AI 맞춤 세탁은 옷감과 무게를 감지하여 세탁합니다. 이 기능은 6kg 이하에서 사용합니다.</p><img src="https://cdn.example/detail.jpg"></div><aside>다른 제품 광고</aside>'


def response(html=HTML, url=DETAIL, status=200):
    return httpx.Response(status, text=html, headers={'content-type':'text/html'}, request=httpx.Request('GET',url))


class LotteDescriptionTests(unittest.TestCase):
    def test_canonical_id_maps_to_observed_public_document(self):
        self.assertEqual(DETAIL, lotteon.detail_url(URL+'?tracking=test'))
        self.assertEqual(DETAIL, lotteon.detail_url(URL.replace('/p/', '/m/')))
        self.assertNotEqual(DETAIL, lotteon.detail_url(URL.replace('7150','7151')))

    def test_other_hosts_credentials_and_unknown_formats_never_rewrite(self):
        for url in ('https://lotteon.com.evil.test/p/product/LO2635377150',
                    'https://user:password@www.lotteon.com/p/product/LO2635377150',
                    'https://www.lotteon.com:8443/p/product/LO2635377150',
                    'https://www.lotteon.com/p/product/ABC123',
                    'https://www.lotteon.com/p/product/LO2635377150/other'):
            self.assertIsNone(lotteon.detail_url(url))

    def test_description_keeps_conditions_and_actual_source(self):
        with patch.object(network_safety,'public_addresses'), patch.object(network_safety,'fetch_public',return_value=response()) as fetch:
            result=lotteon.read_product(URL,ocr_images=False)
        self.assertIn('6kg',result.text)
        self.assertNotIn('다른 제품 광고',result.text)
        self.assertEqual(URL,result.source_capture['source_page_url'])
        self.assertTrue(all(s['source_url']==DETAIL for s in result.source_capture['sections']))
        self.assertEqual({lotteon.HOST},fetch.call_args.kwargs['allowed_hosts'])

    def test_generic_empty_and_mismatched_document_rejected(self):
        cases=[response('<title>로그인</title>'),response('<div id="llis-root"></div>'),
               response(url=DETAIL.replace('7150','7151'))]
        for r in cases:
            with patch.object(network_safety,'public_addresses'), patch.object(network_safety,'fetch_public',return_value=r):
                with self.assertRaises(product_page.ProductBodyUnavailable):
                    lotteon.read_product(URL,ocr_images=False)

    def test_original_product_link_recovers_before_blocked_parent_request(self):
        with patch.object(network_safety,'public_addresses'), patch.object(network_safety,'fetch_public',return_value=response()), patch.object(ingest,'_fetch') as parent:
            result=ingest.from_url(URL,ocr_images=False,product_only=True)
        parent.assert_not_called()
        self.assertIn('6kg',result.text)

    def test_failed_public_document_returns_to_normal_flow(self):
        with patch.object(lotteon,'read_product',side_effect=product_page.ProductBodyUnavailable('empty')), patch.object(ingest,'_fetch',side_effect=httpx.ConnectError('unavailable')) as fetch, patch('app.pipeline.page_recovery.time.sleep'), patch('app.pipeline.rendered_page.render',return_value={'html':response().text,'url':DETAIL}) as render:
            result=ingest.from_url(URL,ocr_images=False,product_only=True)
        self.assertEqual(2,fetch.call_count)
        render.assert_called_once_with(URL)
        self.assertIn('6kg',result.text)

    def test_standalone_detail_url_is_recognized_without_browser(self):
        with patch.object(ingest,'_fetch',return_value=response()), patch('app.pipeline.rendered_page.render') as render:
            result=ingest.from_url(DETAIL,ocr_images=False,product_only=True)
        render.assert_not_called()
        self.assertIn('6kg',result.text)

    def test_explicit_frame_is_found_and_only_description_is_attached(self):
        html='<iframe id="llis-goodsdetail-frame" src="'+DETAIL+'"></iframe><iframe src="https://tracker.example/ad"></iframe>'
        with patch.object(ingest,'_fetch',return_value=response()) as fetch:
            prepared,docs=product_documents.prepare(html,URL,ingest._fetch)
        fetch.assert_called_once_with(DETAIL)
        self.assertEqual('read',docs[0]['status'])
        self.assertIn('6kg',product_page.product_text(product_page.product_body(prepared,URL)))

if __name__=='__main__': unittest.main()
