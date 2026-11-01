import asyncio
import time
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import check_budget, main
from app.pipeline import ingest


HTML = '<div class="productOverview"><div class="overContent"><p>AI 카메라는 사람을 인식하여 움직임을 추적합니다.</p><p>실내에서 전원과 인터넷에 연결하여 사용합니다.</p></div></div>'


class BudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_returns_real_partial_text_without_waiting_for_worker(self):
        state = check_budget.Budget(seconds=.04)
        def slow():
            ingest.from_product_html(HTML, 'https://example.com/item')
            time.sleep(.2)
            check_budget.checkpoint()
        start = time.monotonic()
        result = await check_budget.call(state, slow, fallback='url')
        self.assertLess(time.monotonic()-start, .15)
        self.assertIn('사람을 인식', result.text)
        self.assertTrue(result.source_capture['coverage']['time_limited'])
        self.assertTrue(state.stopped)

    async def test_no_source_does_not_fabricate_product_result(self):
        with self.assertRaises(TimeoutError):
            await check_budget.call(check_budget.Budget(seconds=.01), time.sleep, .05, fallback='url')

    async def test_slow_optional_model_keeps_source_based_card(self):
        def analyze(*args, use_llm=False, **kwargs):
            if use_llm:
                time.sleep(.2)
                check_budget.checkpoint()
            return {'claims': [{'text':'AI 센서가 사람을 감지합니다.'}], 'quick_llm':False}, {}
        with patch.object(main, 'quick_check', side_effect=analyze):
            card, _ = await main._quick_result('test', '', 'source', '', use_llm=True, seconds=.12)
        self.assertFalse(card['quick_llm'])
        self.assertEqual('AI 센서가 사람을 감지합니다.', card['claims'][0]['text'])

    async def test_upload_checkpoint_survives_later_file_failure(self):
        def read():
            check_budget.remember_uploads(['AI 센서가 사람을 감지합니다.'], ['파일 1: 읽음'])
            raise ValueError('file 2 failed')
        texts, notes = await check_budget.call(check_budget.Budget(), read, fallback='uploads')
        self.assertEqual(['AI 센서가 사람을 감지합니다.'], texts)
        self.assertIn(check_budget.PARTIAL_NOTE, notes)

    async def test_request_checkpoints_are_isolated(self):
        def read(value):
            check_budget.remember_uploads([value], [])
            raise ValueError('stop')
        a, b = await asyncio.gather(*[
            check_budget.call(check_budget.Budget(), read, value, fallback='uploads')
            for value in ('first user', 'second user')])
        self.assertEqual(['first user'], a[0])
        self.assertEqual(['second user'], b[0])


class ResultCardTests(unittest.TestCase):
    def setUp(self):
        self.saved = main._check_hits.copy()
        main._check_hits.clear()
    def tearDown(self):
        main._check_hits.clear(); main._check_hits.update(self.saved)

    def test_blocked_url_has_explicit_pending_card_and_recovery(self):
        with patch.object(main, '_match_published', return_value=None), patch.object(main, '_read_check_url', side_effect=ValueError('blocked')):
            result = TestClient(main.app).post('/check', data={'url':'https://example.com/item'})
        self.assertEqual(200, result.status_code)
        self.assertIn('자료 확인 상태 카드', result.text)
        self.assertIn('분석을 완료한 카드가 아닙니다', result.text)
        self.assertIn('사진·PDF로 계속하기', result.text)

    def test_page_capture_error_has_card_instead_of_api_500(self):
        with patch.object(main, '_match_published', return_value=None), patch('app.pipeline.browser_capture.read', side_effect=RuntimeError('failed')):
            result = TestClient(main.app).post('/api/check', json={'url':'https://example.com/item','title':'test','product_html':'<div>test</div>'})
        self.assertEqual(200, result.status_code)
        self.assertIn('자료 확인 상태 카드', result.text)

    def test_partial_capture_produces_analysis_not_empty_state(self):
        def read(url):
            ingest.from_product_html(HTML, url)
            raise TimeoutError('image slow')
        with patch.object(main, '_match_published', return_value=None), patch.object(main, '_read_check_url', side_effect=read):
            result = TestClient(main.app).post('/check', data={'url':'https://example.com/item'})
        self.assertIn('읽은 자료로 먼저 만든 부분 결과', result.text)
        self.assertIn('사람을 인식', result.text)
        self.assertNotIn('확인을 이어가 주세요', result.text)
