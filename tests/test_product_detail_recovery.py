import unittest
from unittest.mock import Mock
import httpx
from app.pipeline import product_page, product_documents, page_recovery, extract


class ProductDetailRecoveryTests(unittest.TestCase):
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
