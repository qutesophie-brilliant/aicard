import unittest
from unittest.mock import patch
import httpx
from app import network_safety
from app.pipeline import public_description as desc, ingest, product_page

URL='https://www.skstoa.com/display/goods/61991034'
DETAIL='https://desc.skstoa.com/display/detail/goods/pc/describe/61991034'
BODY='<div class="pic1_info_detail"><p>AI가 물체를 인식해 청소 경로를 조정합니다. 빛이 부족하면 사물 인식에 제약이 있습니다.</p><img src="https://cdn.example/detail.jpg"></div>'


class PublicDescriptionTests(unittest.TestCase):
    def response(self, body=BODY, url=DETAIL):
        return httpx.Response(200,text=body,headers={'content-type':'text/html'},request=httpx.Request('GET',url))

    def test_exact_id_route_including_tracking(self):
        self.assertEqual(DETAIL,desc.detail_url(URL+'?utm_source=search'))
        self.assertEqual(DETAIL.replace('61991034','77889900'),desc.detail_url(URL.replace('61991034','77889900')))
        for url in [URL.replace('www.skstoa.com','evil.example'), URL+'/other',URL.replace('/61991034','/../private'),URL.replace('https://','https://user:pass@')]:
            self.assertIsNone(desc.detail_url(url))

    def test_public_document_read_before_parent_connection(self):
        with patch.object(network_safety,'public_addresses'), patch.object(network_safety,'fetch_public',return_value=self.response()) as fetch, patch.object(ingest,'_fetch') as parent:
            result=ingest.from_url(URL,product_only=True,ocr_images=False)
        parent.assert_not_called()
        self.assertIn('사물 인식',result.text)
        self.assertEqual(DETAIL,result.source_capture['source_url'])
        self.assertEqual(URL,result.source_capture['source_page_url'])
        self.assertEqual({desc.HOST},fetch.call_args.kwargs['allowed_hosts'])

    def test_wrong_product_empty_and_login_are_rejected(self):
        for response in [self.response(url=DETAIL+'9'),self.response('<title>Login</title>'),self.response('<div>generic page</div>')]:
            with patch.object(network_safety,'public_addresses'),patch.object(network_safety,'fetch_public',return_value=response):
                with self.assertRaises(product_page.ProductBodyUnavailable): desc.read_product(URL,ocr_images=False)

    def test_images_continue_to_ocr_in_shared_pipeline(self):
        from app.pipeline import product_images
        with patch.object(network_safety,'public_addresses'),patch.object(network_safety,'fetch_public',return_value=self.response()),patch.object(product_images,'capture_images') as ocr:
            desc.read_product(URL,ocr_images=True)
        self.assertEqual(1,ocr.call_count)
        self.assertIn('cdn.example/detail.jpg',ocr.call_args.args[0])


if __name__=='__main__': unittest.main()
