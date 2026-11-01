import unittest
from unittest.mock import Mock
import httpx
from app.pipeline import product_page, product_documents, page_recovery, extract


class ProductDetailRecoveryTests(unittest.TestCase):
    def test_unfamiliar_tab_target_recovers_copy_and_lazy_images(self):
        html = '''<nav><button role="tab" aria-controls="pane-xyz">상품 상세정보</button></nav>
        <div id="pane-xyz"><p>AI가 온도와 사용 패턴을 분석하여 냉각을 조절합니다. 이 기능은 문이 닫힌 상태에서 작동합니다.</p><img data-src="/long-detail.jpg"></div>
        <div class="reviews">AI 후기에서 다른 제품을 추천합니다.</div>'''
        body = product_page.product_body(html, 'https://unseen.example/item/99')
        self.assertIn('냉각', body.get_text())
        self.assertEqual('/long-detail.jpg', product_page.image_source(body.img))
        self.assertNotIn('후기', body.get_text())

    def test_tab_target_frame_is_fetched_as_product_description(self):
        url = 'https://unseen.example/item/99'
        html = '<button aria-controls="pane-xyz">상품 설명</button><div id="pane-xyz"><iframe src="/description"></iframe></div>'
        detail = '<p>AI가 온도와 사용 패턴을 분석하여 냉각을 조절합니다. 이 기능은 문이 닫힌 상태에서 작동합니다.</p>'
        fetch = Mock(return_value=httpx.Response(200, text=detail, headers={'content-type':'text/html'}, request=httpx.Request('GET',url+'/description')))
        prepared, records = product_documents.prepare(html, url, fetch)
        self.assertEqual(records[0]['status'], 'read')
        self.assertIn('냉각', product_page.product_body(prepared, url).get_text())

    def test_review_controls_and_page_wide_targets_are_not_evidence(self):
        for html in [
            '<button aria-controls="pane">상품 후기</button><div id="pane">AI 후기는 이 제품이 모든 문제를 해결한다고 이야기합니다. 다른 제품도 추천합니다.</div>',
            '<div id="pane"><button aria-controls="pane">상품 설명</button>AI 후기는 이 제품이 모든 문제를 해결한다고 이야기합니다. 다른 제품도 추천합니다.</div>',
            '<button aria-controls="pane">상품 설명</button><div id="pane" class="reviews">AI 후기는 이 제품이 모든 문제를 해결한다고 이야기합니다. 다른 제품도 추천합니다.</div>',
        ]:
            with self.subTest(html=html), self.assertRaises(product_page.ProductBodyUnavailable):
                product_page.product_body(html, 'https://unseen.example/item/99')

    def test_structured_summary_does_not_stop_dynamic_recovery(self):
        url = 'https://unseen.example/item/99'
        summary = '<script type="application/ld+json">{"@type":"Product","description":"A home appliance with automatic operation and modern features for daily use."}</script>'
        detail = '<div class="product-description">AI가 무게와 옷감을 감지하여 세탁합니다. 이 기능은 세탁물 6kg 이하에서 사용합니다.</div>'
        renderer = Mock(return_value={'html':summary+detail,'url':url})
        result, _, _, stages = page_recovery.acquire(summary, url, Mock(), renderer)
        renderer.assert_called_once_with(url)
        self.assertIn('6kg', product_page.product_body(result,url).get_text())
        self.assertEqual(stages[-1]['status'], 'read')

    def test_summary_survives_blocked_or_empty_dynamic_recovery(self):
        url = 'https://unseen.example/item/99'
        summary = '<script type="application/ld+json">{"@type":"Product","description":"A home appliance with automatic operation and modern features for daily use."}</script>'
        for renderer in [Mock(side_effect=product_page.ProductBodyUnavailable('blocked')),
                         Mock(return_value={'html':'<div id="app"></div>','url':url}),
                         Mock(return_value={'html':summary,'url':url})]:
            with self.subTest(renderer=renderer):
                result, _, _, stages = page_recovery.acquire(summary,url,Mock(),renderer)
                self.assertIn('automatic operation', product_page.product_body(result,url).get_text())
                self.assertNotEqual(stages[-1]['status'], 'read')

    def test_full_features_win_over_short_structured_summary(self):
        html = '''<script type="application/ld+json">{"@type":"Product","description":"An AI computer with a fast processor and long battery life."}</script>
        <div id="CMD" class="features"><p>AI 노이즈 캔슬링은 머신 러닝으로 주변 소음을 제거합니다. 화상 회의에서 사람의 목소리를 구분하여 전달합니다.</p></div>
        <aside class="recommendations">AI 추천 상품</aside>'''
        body = product_page.product_body(html, 'https://new.example/product/123')
        self.assertIn('머신 러닝', body.get_text())
        self.assertNotIn('추천 상품', body.get_text())
        self.assertNotIn('fast processor', body.get_text())

    def test_short_url_follows_published_same_product_canonical(self):
        url = 'https://new.example/c-p/123'
        target = url + '/full-product-name'
        shell = '<link rel="canonical" href="'+target+'"><div id="app"></div>'
        detail = '<div id="see-all-benefits"><p>인공 지능을 기반으로 하며 사용자의 압력과 움직임을 분석합니다. 사용자의 양치 습관에 맞는 안내를 제공합니다.</p></div>'
        fetch = Mock(return_value=httpx.Response(200, text=detail, headers={'content-type':'text/html'}, request=httpx.Request('GET',target)))
        renderer = Mock()
        html, final, _, stages = page_recovery.acquire(shell, url, fetch, renderer)
        self.assertEqual(final, target)
        fetch.assert_called_once_with(target)
        renderer.assert_not_called()
        self.assertIn('압력', product_page.product_body(html,final).get_text())
        self.assertEqual(stages[0]['stage'], 'canonical_product_detail')

    def test_canonical_cannot_switch_origin_or_product(self):
        url='https://new.example/product/123'
        for target in ['https://evil.example/product/123/full', 'https://new.example/',
                       'https://new.example/product/124/full', 'http://new.example/product/123/full']:
            with self.subTest(target=target):
                self.assertIsNone(product_documents.canonical_detail_link('<link rel="canonical" href="'+target+'">',url))

    def test_unavailable_canonical_still_uses_rendered_recovery(self):
        url='https://new.example/product/123'
        shell='<link rel="canonical" href="/product/123/full">'
        detail='<div class="product-description">AI가 무게와 옷감을 감지하여 세탁물에 맞게 조절합니다. 이 기능은 세탁물 무게가 6kg 이하일 때 사용합니다.</div>'
        render=Mock(return_value={'url':url,'html':detail})
        _,_,_,stages=page_recovery.acquire(shell,url,Mock(side_effect=httpx.ReadTimeout('timeout')),render)
        render.assert_called_once()
        self.assertEqual(stages[0]['status'],'unavailable')
        self.assertEqual(stages[-1]['status'],'read')

    def test_spaced_ai_is_recognized_only_in_preview_with_quote_preserved(self):
        quote='필립스 소닉케어 앱은 인공 지능을 기반으로 하며 칫솔과 완벽한 조화를 이룹니다.'
        rows=[{'quote':quote,'doc_id':1,'chunk':0}]
        quick=extract._rules_extract(rows,14,complete_quotes=True)
        self.assertEqual(quick[0]['text'],quote)
        self.assertEqual(quick[0]['source']['quote'],quote)
        self.assertEqual(extract._rules_extract(rows,14),[])


if __name__ == '__main__': unittest.main()
