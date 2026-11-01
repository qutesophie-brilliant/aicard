import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import main
from app.pipeline.product_page import ProductBodyUnavailable

class CheckRateReservationTests(unittest.TestCase):
    def setUp(self):
        self.saved=main._check_hits.copy()
        main._check_hits.clear()
    def tearDown(self):
        main._check_hits.clear(); main._check_hits.update(self.saved)
    def test_denials_do_not_extend_quota_or_grow_records(self):
        self.assertTrue(main._rate_ok('test',limit=1))
        for _ in range(20): self.assertFalse(main._rate_ok('test',limit=1))
        self.assertEqual(1,len(main._check_hits['test']))
    def test_failed_url_does_not_create_ocr_reservation(self):
        with patch.object(main,'_read_check_url',side_effect=ProductBodyUnavailable('source unavailable')), \
             patch.object(main,'_match_published',return_value=None):
            response=TestClient(main.app).post('/check',data={'url':'https://seller.example/item/1'})
        self.assertEqual(200,response.status_code)
        self.assertNotIn('testclient:ocr',main._check_hits)

    def test_analysis_continues_past_old_limits_for_url_upload_and_capture(self):
        import time
        from app.pipeline import ingest, browser_capture
        body='AI 카메라가 장애물을 인식하고 이동 경로를 조절합니다. 어두운 곳에서는 인식이 제한될 수 있습니다.'
        ing=ingest.Ingested('AI 카메라',body,[],source_capture={})
        for key in ('testclient','testclient:ocr','testclient:llm'):
            main._check_hits[key]=[time.time()]*40
        with patch.object(main,'_read_check_url',return_value=ing) as urls, \
             patch.object(main,'_read_check_uploads',return_value=([body],[])) as uploads, \
             patch.object(browser_capture,'read',return_value=ing) as captures, \
             patch.object(main,'_match_published',return_value=None), \
             patch.object(main,'_product_candidates',return_value=[]), \
             patch.object(main.config,'CHECK_LLM',False):
            client=TestClient(main.app)
            for _ in range(10):
                results=[
                    client.post('/check',data={'url':'https://seller.example/item/1','fresh':'true'}),
                    client.post('/check',files={'files':('detail.png',b'fixture','image/png')}),
                    client.post('/api/check',json={'url':'https://seller.example/item/1','product_html':'<p>'+body+'</p>'}),
                ]
                for r in results:
                    self.assertEqual(200,r.status_code)
                    self.assertIn('quick-scope',r.text)
                    self.assertIn('장애물',r.text)
            self.assertEqual(10,urls.call_count)
            self.assertEqual(10,uploads.call_count)
            self.assertEqual(10,captures.call_count)
        self.assertTrue(all(len(hits)==40 for hits in main._check_hits.values()))

    def test_upload_total_limit_rejects_before_decode_or_ai(self):
        with patch.object(main.config, 'CHECK_MAX_TOTAL_MB', 1), \
             patch.object(main, '_read_check_uploads') as reader:
            result = TestClient(main.app).post('/check', files=[
                ('files', ('a.jpg', b'x' * 600000, 'image/jpeg')),
                ('files', ('b.jpg', b'x' * 600000, 'image/jpeg')),
            ])
        self.assertEqual(200, result.status_code)
        self.assertIn('전체 용량은 1MB', result.text)
        reader.assert_not_called()

    def test_upload_after_blocked_url_does_not_revisit_seller(self):
        url='https://blocked-seller.example/item/1'
        with patch.object(main,'_read_check_url',side_effect=ProductBodyUnavailable('source unavailable')) as fetch, \
             patch.object(main,'_match_published',return_value=None), \
             patch.object(main,'_read_check_uploads',return_value=([
                 'AI 카메라가 장애물을 인식하고 이동 경로를 조절합니다. 어두운 곳에서는 장애물 인식이 제한될 수 있습니다.'
             ], [])):
            client=TestClient(main.app)
            failed=client.post('/check',data={'url':url})
            self.assertIn('사진·PDF로 계속하기',failed.text)
            result=client.post('/check',data={'url':url},files={'files':('detail.png',b'fixture','image/png')})
        self.assertEqual(fetch.call_count,1)
        self.assertEqual(result.status_code,200)
        self.assertIn('quick-scope',result.text)
        self.assertIn('장애물',result.text)

if __name__=='__main__': unittest.main()
