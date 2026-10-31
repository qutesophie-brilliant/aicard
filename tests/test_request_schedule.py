import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import db, main, schema


class RequestScheduleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dbpatch = patch.object(db, 'DB_PATH', Path(self.tmp.name) / 'requests.sqlite3')
        self.dbpatch.start()
        self.ratepatch = patch.object(main, '_rate_ok', return_value=True)
        self.ratepatch.start()
        db.init_db()
        self.pid = db.create_product('공개된 시험용 제품', '시험 제조사', 'TEST', '세탁기')
        self.card = schema.new_card(db.get_product(self.pid))
        db.save_card(self.pid, self.card, {})
        db.set_product_status(self.pid, 'approved', '시험용 승인')
        self.before = db.get_product(self.pid)
        self.client = TestClient(main.app)

    def tearDown(self):
        self.ratepatch.stop()
        self.dbpatch.stop()
        self.tmp.cleanup()

    def test_new_request_shows_schedule_without_publishing_or_changing_cards(self):
        response = self.client.post('/check/request', data={
            'url': 'https://example.test/new-product', 'name': '신규 요청 제품', 'note': '조건 확인 요청',
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn('2026년 12월 말부터', response.text)
        self.assertIn('승인된 카드부터 순차적으로 공개', response.text)
        self.assertEqual(db.get_product(self.pid), self.before)
        self.assertEqual(db.get_card(self.pid), self.card)
        self.assertEqual(len(db.list_products('approved')), 1)

    def test_correction_receipt_does_not_defer_corrections_to_december(self):
        response = self.client.post('/check/request', data={
            'url': f'/card/{self.pid}', 'name': '공개된 시험용 제품', 'note': '원문을 대조해 주세요.',
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn('정정 요청을 받았습니다', response.text)
        self.assertIn('수정 여부를 검토', response.text)
        self.assertNotIn('12월', response.text)
        self.assertEqual(db.get_product(self.pid), self.before)
        self.assertEqual(db.get_card(self.pid), self.card)


if __name__ == '__main__':
    unittest.main()
