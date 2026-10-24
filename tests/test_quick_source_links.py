import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
from app import db, main


TEXT = '시험용 AI 세탁기\nAI 세탁은 오염도를 감지해 세탁 시간을 조절합니다.\n* AI 세탁은 세탁량 6kg 이하에서 동작합니다.'
URL = 'https://maker.example/product/washer'


class QuickSourceLinksTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = patch.object(db, 'DB_PATH', Path(self.temp.name) / 'isolated.sqlite3')
        self.database.start()
        db.init_db()
        self.rate = patch.object(main, '_rate_ok', return_value=True)
        self.rate.start()
        self.client = TestClient(main.app)

    def tearDown(self):
        self.rate.stop()
        self.database.stop()
        self.temp.cleanup()

    def test_claim_and_condition_excerpts_link_to_their_document(self):
        page = self.client.post('/check', data={'url': URL, 'text': TEXT})
        self.assertEqual(200, page.status_code)
        soup = BeautifulSoup(page.text, 'html.parser')
        quotes = soup.select('blockquote')
        self.assertGreater(len(quotes), 0)
        self.assertTrue(all(q.select_one('cite a[href="' + URL + '"]') for q in quotes))
        self.assertTrue(any('6kg' in q.get_text() for q in quotes))
        self.assertEqual([], db.list_products())

    def test_pasted_text_without_url_never_invents_a_source_link(self):
        page = self.client.post('/check', data={'text': TEXT})
        self.assertEqual(200, page.status_code)
        soup = BeautifulSoup(page.text, 'html.parser')
        self.assertTrue(soup.select('blockquote'))
        self.assertEqual([], soup.select('blockquote cite a'))

