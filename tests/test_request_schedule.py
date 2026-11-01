import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import db, main, schema
from app.pipeline.product_page import ProductBodyUnavailable


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

    def test_failed_link_can_be_requested_without_files_name_or_fetch(self):
        url = 'https://seller.example/item/1?variant=blue'
        with patch.object(main, '_read_check_url', side_effect=ProductBodyUnavailable('본문을 가져오지 못했습니다.')) as fetch, \
             patch.object(main, '_match_published', return_value=None):
            failed = self.client.post('/check', data={'url': url})
            self.assertIn('관리자에게 링크 확인 요청', failed.text)
            from bs4 import BeautifulSoup
            page = BeautifulSoup(failed.text, 'html.parser')
            section = page.select_one('#link-review-request')
            self.assertTrue(section.has_attr('open'))
            form = section.find('form')
            self.assertIsNone(form.find_parent('form'))
            self.assertEqual(form.select_one('[name=url]')['value'], url)
            response = self.client.post('/check/request', data={
                'url': url, 'request_kind': 'link_review',
            })
            self.assertEqual(fetch.call_count, 1)
        self.assertEqual(response.status_code, 200)
        self.assertIn('링크 확인 요청을 받았습니다', response.text)
        self.assertIn('접수번호는', response.text)
        self.assertIn('2026년 12월 말부터', response.text)
        pending = db.list_requests('new')
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]['url'], url)
        self.assertIn('[링크 확인 요청]', pending[0]['note'])
        self.assertEqual(db.get_product(self.pid), self.before)
        self.assertEqual(db.get_card(self.pid), self.card)
        # The same existing queue is rendered on the protected reviewer screen.
        with patch.object(main.config, 'REVIEW_TOKEN', ''):
            review = self.client.get('/review')
        self.assertEqual(review.status_code, 200)
        self.assertIn('[링크 확인 요청]', review.text)

    def test_link_request_rejects_invalid_schemes_credentials_and_long_url(self):
        for url in ('', 'javascript:alert(1)', 'file:///etc/passwd',
                    'https://user:password@seller.example/item',
                    'https://seller.example/' + 'a' * 500):
            with self.subTest(url=url):
                response = self.client.post('/check/request', data={
                    'url': url, 'request_kind': 'link_review',
                })
                self.assertEqual(response.status_code, 400)
        self.assertEqual(db.list_requests(), [])


if __name__ == '__main__':
    unittest.main()
