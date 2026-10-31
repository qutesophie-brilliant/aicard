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

if __name__=='__main__': unittest.main()
