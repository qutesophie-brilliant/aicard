import json
import unittest
from unittest.mock import Mock,patch
from app.pipeline import page_selector, product_page, page_recovery, llm

HTML='''<html><title>Newbrand Camera ZX 700</title><body><nav><p>Other AI products</p></nav>
<main><h1>Newbrand Camera ZX 700</h1><div class="a8x9"><p>AI detects people and pets using the camera sensor.</p>
<p>Detection requires adequate light and a network connection.</p><img src="https://cdn.example/detail700.jpg" alt="Camera ZX 700 detection conditions"></div>
<div class="reviews"><p>This AI camera never misses anything, says a reviewer.</p></div></main></body></html>'''


class PageSelectorTests(unittest.TestCase):
    @patch.object(llm,'available',return_value=True)
    def test_image_only_editor_paragraphs_survive_candidate_selection(self, _):
        html='''<title>한글 카메라 제품 설명</title><main><h1>한글 카메라 제품 설명</h1>
        <p><span><img src="/detail-1.jpg"></span></p>
        <figure><p><img data-src="/detail-2.jpg" src="data:image/gif;base64,AA"></p></figure>
        <div class="reviews"><p><img src="/review.jpg"></p></div>
        <div class="recommended-products"><p><img src="/other.jpg"></p></div></main>'''
        _, blocks=page_selector.candidates(html)
        self.assertEqual([['/detail-1.jpg'], ['/detail-2.jpg']], [b['images'] for b in blocks if b['images']])
        ids=[i for i,b in enumerate(blocks) if b['images']]
        with patch.object(llm,'ask_json',return_value={'block_ids':ids}):
            selected=page_selector.recover(html,'https://new-store.example/product/123')
        body=product_page.product_body(selected,'https://new-store.example/product/123')
        self.assertEqual(['/detail-1.jpg','/detail-2.jpg'],[product_page.image_source(i) for i in body.select('img')])
        self.assertNotIn('review.jpg',str(body))
        self.assertNotIn('other.jpg',str(body))

    @patch.object(llm,'available',return_value=True)
    def test_unknown_layout_uses_only_selected_original_blocks(self, _):
        with patch.object(llm,'ask_json',return_value={'block_ids':[1,2,3]}) as ask:
            selected=page_selector.recover(HTML,'https://unseen.example/products/zx700')
        body=product_page.product_body(selected,'https://unseen.example/products/zx700')
        text=product_page.product_text(body)
        self.assertIn('AI detects people and pets',text)
        self.assertIn('adequate light',text)
        self.assertNotIn('reviewer',text)
        self.assertNotIn('Other AI',text)
        self.assertEqual('https://cdn.example/detail700.jpg',body.img['src'])
        self.assertEqual('ai_selected_product_sections',body.section['data-aicard-selection'])
        ask.assert_called_once()
        prompt=ask.call_args.args[0]
        self.assertNotIn('reviewer',prompt)

    @patch.object(llm,'available',return_value=True)
    def test_generated_text_urls_and_invalid_ids_are_rejected(self,_):
        for result in ({'block_ids':[1], 'text':'AI fabricated feature'},
                       {'block_ids':[999]}, {'block_ids':[True]}, {'block_ids':['1']},
                       {'block_ids':[]}, {'block_ids':[1]*49}, ['1']):
            with self.subTest(result=result),patch.object(llm,'ask_json',return_value=result):
                self.assertIsNone(page_selector.recover(HTML,'https://unseen.example/products/zx700'))

    def test_unconfigured_provider_never_calls_model(self):
        with patch.object(llm,'available',return_value=False),patch.object(llm,'ask_json') as ask:
            self.assertIsNone(page_selector.recover(HTML,'https://unseen.example/products/zx700'))
        ask.assert_not_called()

    @patch.object(llm,'available',return_value=True)
    def test_provider_failure_preserves_original_recovery_path(self,_):
        with patch.object(llm,'ask_json',side_effect=RuntimeError('provider unavailable')):
            self.assertIsNone(page_selector.recover(HTML,'https://unseen.example/products/zx700'))

    def test_candidate_list_is_bounded_and_reviews_stop_collection(self):
        html='<title>Camera 700</title><main>'+''.join('<p>Feature description number '+str(i)+' with operational conditions.</p>' for i in range(300))
        html+='<h2>Customer Reviews</h2><p>AI reviewer says it sees through walls.</p></main>'
        _,blocks=page_selector.candidates(html)
        self.assertLessEqual(len(blocks),96)
        self.assertFalse(any('sees through walls' in b['text'] for b in blocks))

    def test_model_budget_allows_only_one_provider_request(self):
        with llm.bounded_call(12):
            self.assertLessEqual(llm._request_timeout(180),12)
            with self.assertRaises(llm.LLMError):llm._request_timeout(180)
        self.assertIsNone(llm._ocr_budget.get())

    @patch.object(llm,'available',return_value=True)
    def test_acquisition_uses_generic_selection_for_an_unknown_domain(self,_):
        with patch.object(llm,'ask_json',return_value={'block_ids':[1,2,3]}) as ask:
            renderer=Mock()
            html,_,_,stages=page_recovery.acquire(HTML,'https://unseen.example/products/zx700',Mock(),renderer)
        self.assertIn('AI detects people',product_page.product_text(product_page.product_body(html,'https://unseen.example/products/zx700')))
        self.assertEqual('source_block_selection',stages[-1]['stage'])
        ask.assert_called_once()
        renderer.assert_not_called()

    @patch.object(llm,'available',return_value=True)
    def test_failed_selection_not_repeated_after_rendering(self,_):
        with patch.object(llm,'ask_json',return_value={'block_ids':[]}) as ask:
            with self.assertRaises(product_page.ProductBodyUnavailable):
                page_recovery.acquire(HTML,'https://unseen.example/products/zx700',Mock(),
                    Mock(return_value={'html':HTML,'url':'https://unseen.example/products/zx700'}))
        ask.assert_called_once()

    @patch.object(llm,'available',return_value=True)
    def test_rendered_shell_can_use_selection_once(self,_):
        with patch.object(llm,'ask_json',return_value={'block_ids':[1,2,3]}) as ask:
            html,_,_,stages=page_recovery.acquire('<title>Camera ZX700</title><main></main>',
                'https://unseen.example/products/zx700',Mock(),
                Mock(return_value={'html':HTML,'url':'https://unseen.example/products/zx700'}))
        ask.assert_called_once()
        self.assertTrue(any(s['stage']=='source_block_selection' for s in stages))


if __name__=='__main__':unittest.main()
