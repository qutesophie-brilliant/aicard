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
    def test_failed_url_returns_ocr_reservation(self):
        with patch.object(main,'_read_check_url',side_effect=ProductBodyUnavailable('source unavailable')), \
             patch.object(main,'_match_published',return_value=None):
            response=TestClient(main.app).post('/check',data={'url':'https://seller.example/item/1'})
        self.assertEqual(200,response.status_code)
        self.assertEqual([],main._check_hits.get('testclient:ocr'))
    def test_refund_only_own_reservation(self):
        main._check_hits['key']=[1.0,2.0]
        main._refund_check_ocr('key',1.0)
        self.assertEqual([2.0],main._check_hits['key'])

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
