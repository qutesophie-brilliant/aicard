import unittest
from unittest.mock import Mock, patch
import httpx
from app.pipeline import page_recovery, product_page, ingest
from app import network_safety

URL = 'https://seller.example/product/42'
BODY = '<div class="product-description">AI가 무게와 옷감을 감지하여 세탁합니다. 이 기능은 세탁물 6kg 이하에서 사용합니다.</div>'


class PageRecoveryTests(unittest.TestCase):
    def test_static_product_does_not_launch_browser(self):
        renderer = Mock()
        html, url, docs, stages = page_recovery.acquire(BODY, URL, Mock(), renderer)
        renderer.assert_not_called()
        self.assertIn('6kg', html)
        self.assertEqual('read', stages[0]['status'])

    def test_empty_js_shell_automatically_recovers_description_and_images(self):
        renderer = Mock(return_value={'html': BODY+'<div class="product-description"><img src="/detail.jpg"></div>', 'url': URL})
        html, url, docs, stages = page_recovery.acquire('<div id="app"></div>', URL, Mock(), renderer)
        renderer.assert_called_once_with(URL)
        self.assertIn('/detail.jpg', html)
        self.assertEqual(['insufficient','read'], [s['status'] for s in stages])

    def test_empty_render_is_not_a_negative_ai_result(self):
        renderer = Mock(return_value={'html':'<h1>제품</h1>','url':URL})
        with self.assertRaises(product_page.ProductBodyUnavailable):
            page_recovery.acquire('<div id="app"></div>', URL, Mock(), renderer)

    def test_failed_frame_triggers_recovery_without_discarding_existing_copy(self):
        html=BODY.replace('</div>','<iframe src="/detail"></iframe></div>')
        renderer=Mock(side_effect=product_page.ProductBodyUnavailable('blocked'))
        fetch=Mock(side_effect=network_safety.PublicURLRejected('blocked'))
        result,_,docs,stages=page_recovery.acquire(html,URL,fetch,renderer)
        renderer.assert_called_once()
        self.assertIn('6kg',result)
        self.assertEqual('unavailable',stages[-1]['status'])
        self.assertEqual('unavailable',docs[0]['status'])

    def test_login_and_challenge_are_not_retried(self):
        for title in ('NAVER 로그인', 'Just a moment', 'Access Denied'):
            renderer = Mock()
            with self.assertRaises(product_page.ProductBodyUnavailable):
                page_recovery.acquire('<title>'+title+'</title>', URL, Mock(), renderer)
            renderer.assert_not_called()

    def test_rendered_reviews_cannot_become_product_evidence(self):
        renderer = Mock(return_value={'html':BODY+'<div class="reviews">AI는 무엇이든 가능합니다.</div>','url':URL})
        html, url, _, _ = page_recovery.acquire('<div id="app"></div>', URL, Mock(), renderer)
        text=product_page.product_text(product_page.product_body(html,url))
        self.assertNotIn('무엇이든',text)

    def test_transient_fetch_retried_once(self):
        expected = object()
        fetch = Mock(side_effect=[httpx.ReadTimeout('timeout'), expected])
        with patch.object(page_recovery.time,'sleep'):
            self.assertIs(expected, page_recovery.fetch_retry(URL, fetch))
        self.assertEqual(2, fetch.call_count)

    def test_denial_and_private_address_are_not_retried(self):
        errors = [network_safety.PublicURLRejected('private')]
        for status in (401,403,404,429):
            response=httpx.Response(status,request=httpx.Request('GET',URL))
            errors.append(httpx.HTTPStatusError('denied',request=response.request,response=response))
        for error in errors:
            fetch=Mock(side_effect=error)
            with self.assertRaises(type(error)): page_recovery.fetch_retry(URL,fetch)
            self.assertEqual(1,fetch.call_count)

    def test_acquisition_happens_before_single_ocr_pass(self):
        response=httpx.Response(200,text='<div id="app"></div>',request=httpx.Request('GET',URL))
        result=ingest.Ingested('product','AI',[],source_capture={})
        with patch.object(ingest,'_fetch',return_value=response), \
             patch.object(page_recovery,'acquire',return_value=(BODY,URL,[],[{'stage':'browser_render','status':'read'}])), \
             patch.object(ingest,'from_product_html',return_value=result) as parse:
            got=ingest.from_url(URL,ocr_images=True,product_only=True)
        parse.assert_called_once_with(BODY,URL,ocr_images=True)
        self.assertIn('acquisition',got.source_capture)

    def test_private_url_never_starts_worker(self):
        from app.pipeline import rendered_page
        with patch.object(rendered_page,'public_addresses',create=True):
            # Initial URL validation happens before a worker can start.
            with patch.object(rendered_page.subprocess,'Popen') as launch:
                with self.assertRaises(network_safety.PublicURLRejected): rendered_page.render('http://127.0.0.1/private')
                launch.assert_not_called()

    def test_memory_pressure_stops_worker_without_passing_api_keys(self):
        from app.pipeline import rendered_page
        proc=Mock(pid=999999, returncode=None)
        proc.poll.return_value=None
        with patch.object(network_safety,'public_addresses'), \
             patch.dict(rendered_page.os.environ,{'GEMINI_API_KEY':'secret-test-only','CHECK_RENDER_FALLBACK':'1'}), \
             patch.object(rendered_page.subprocess,'Popen',return_value=proc) as launch, \
             patch.object(rendered_page.subprocess,'run'), \
             patch.object(rendered_page.os,'killpg',create=True), \
             patch.object(rendered_page,'memory_pressure',return_value=True):
            with self.assertRaisesRegex(product_page.ProductBodyUnavailable,'서버 처리 한도'):
                rendered_page.render(URL)
        self.assertNotIn('GEMINI_API_KEY',launch.call_args.kwargs['env'])
        proc.wait.assert_called_once()


if __name__=='__main__': unittest.main()
