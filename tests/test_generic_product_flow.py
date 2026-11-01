import io
import unittest
from unittest.mock import Mock, patch

import httpx
from PIL import Image
from app.pipeline import ingest, page_recovery, product_page, product_ocr
from app.pipeline.run import quick_check
from app import network_safety


class GenericProductFlowTests(unittest.TestCase):
    text = 'AI 카메라가 장애물을 인식하여 이동 경로를 조절합니다. 어두운 곳에서는 인식이 제한될 수 있습니다.'

    def test_unregistered_layouts_flow_from_url_to_actual_image_and_claims(self):
        stream = io.BytesIO()
        Image.new('RGB', (400, 500), 'white').save(stream, format='PNG')
        raw = stream.getvalue()
        variants = [
            '<main><h2>상품 상세정보</h2><div class="unknown-editor"><img data-src="/details.png"></div><h2>구매 후기</h2><p>AI 광고만 믿으세요</p></main>',
            '<article><div class="odd-title"><h2>Product description</h2></div><p><img src="/details.png"></p><h2>Recommended products</h2></article>',
            '<div role="region" aria-label="제품 특징"><img src="/details.png"></div>',
        ]
        for i, html in enumerate(variants):
            url = f'https://unregistered-{i}.example/sku/42'
            response = httpx.Response(200, text=html, headers={'content-type':'text/html'}, request=httpx.Request('GET',url))
            reading = {'text':self.text,'engine':'tesseract','status':'read','attempts':2,'recovered':True,'fallback_attempted':True}
            with self.subTest(url=url), patch.object(ingest,'_fetch',return_value=response), \
                 patch.object(network_safety,'fetch_public',return_value=Mock(content=raw)) as download, \
                 patch.object(product_ocr,'read_images',return_value=[reading]) as read:
                result = ingest.from_url(url, ocr_images=True, product_only=True)
                card, _ = quick_check('제품',url,result.text,source_capture=result.source_capture)
            self.assertTrue(card['claims'])
            self.assertIn('장애물',card['claims'][0]['text'])
            self.assertNotIn('광고만 믿으세요',result.text)
            self.assertTrue(read.call_args.kwargs['recover_unconfirmed'])
            self.assertEqual(download.call_args.args[0], f'https://unregistered-{i}.example/details.png')
            self.assertEqual(result.source_capture['coverage']['product_selection'],'labelled_product_section')
            self.assertTrue(result.source_capture['images'][0]['recovered'])

    def test_unknown_dynamic_page_uses_same_semantic_selection_after_render(self):
        url='https://new-seller.example/42'
        html='<main><h2>제품 설명</h2><p>'+self.text+'</p></main>'
        renderer=Mock(return_value={'html':html,'url':url})
        recovered,_,_,stages=page_recovery.acquire('<div id="app"></div>',url,Mock(),renderer)
        renderer.assert_called_once_with(url)
        self.assertIn('장애물',product_page.product_text(product_page.product_body(recovered,url)))
        self.assertEqual(stages[-1]['stage'],'browser_render')

    def test_empty_known_container_does_not_hide_labelled_description(self):
        body=product_page.product_body('<div class="product-description"></div><h2>상품 설명</h2><p>'+self.text+'</p>','https://new.example/42')
        self.assertIn('장애물',body.get_text())

    def test_peer_heading_and_recommendations_end_product_scope(self):
        html='<h2>상품 설명</h2><p>'+self.text+'</p><h2>구매 후기</h2><p>AI가 질병을 치료합니다.</p>'
        body=product_page.product_body(html,'https://new.example/42')
        self.assertNotIn('질병',body.get_text())
        self.assertNotIn('구매 후기',body.get_text())

    def test_no_product_heading_never_turns_whole_page_into_evidence(self):
        with self.assertRaises(product_page.ProductBodyUnavailable):
            product_page.product_body('<main><h2>추천 상품</h2><p>'+self.text+'</p><img src="/photo.png"></main>','https://new.example/')

    def test_aria_heading_boundary_is_respected(self):
        body=product_page.product_body('<div role="heading" aria-level="2">제품 특징</div><p>'+self.text+'</p><div role="heading" aria-level="2">다른 제품</div><p>AI 전혀 다른 기능</p>','https://new.example/42')
        self.assertNotIn('전혀 다른',body.get_text())

