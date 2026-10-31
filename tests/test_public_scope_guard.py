import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from app import db, main
from app.public_view import safe_public_card
from app.schema import new_card


class PublicScopeGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(db, 'DB_PATH', Path(self.tmp.name)/'isolated.sqlite3')
        self.db_patch.start()
        db.init_db()
        self.pid = db.create_product('점검용 제품', '점검용 제조사', 'TEST', '인덕션')
        self.card = new_card(db.get_product(self.pid))
        self.card['items']['remedy'] = {
            'status': '조건 확인',
            'summary': '품질보증기간 이내 해지 시 전액 환불되며 파손료는 분실료의 50%입니다.',
            'evidence': [{'doc_id': 1, 'chunk': 0, 'quote': '품질보증기간 이내 고객 해지 시 전액 환불', 'kind': '사양표'}]}
        self.card['easy'] = {'headline': '', 'items': {'remedy': '보증기간 안에는 전액 환불돼요.'}}
        db.save_card(self.pid, self.card, {'provider': 'test'})
        db.set_product_status(self.pid, 'approved', '기존 시험 승인')
        self.client = TestClient(main.app)

    def tearDown(self):
        self.db_patch.stop()
        self.tmp.cleanup()

    def test_no_mutation_to_stored_card_claims_or_approval(self):
        before = copy.deepcopy(self.card)
        approved = db.get_product(self.pid)['approved_at']
        result = safe_public_card(self.card)
        self.assertEqual(before, self.card)
        self.assertEqual('추가 확인 필요', result['items']['remedy']['status'])
        self.assertEqual(before['claims'], result['claims'])
        self.assertEqual(before['items']['remedy']['evidence'], result['items']['remedy']['evidence'])
        self.assertNotIn('전액', result['easy']['items']['remedy'])
        self.assertEqual(before, db.get_card(self.pid))
        self.assertEqual(approved, db.get_product(self.pid)['approved_at'])

    def test_public_card_compare_and_json_all_use_guard(self):
        other = db.create_product('두 번째 점검용 제품', '점검용 제조사', 'TEST-2', '인덕션')
        db.save_card(other, new_card(db.get_product(other)), {'provider': 'test'})
        db.set_product_status(other, 'approved', '기존 시험 승인')
        for url in (f'/card/{self.pid}', f'/compare?a={self.pid}&b={other}'):
            response = self.client.get(url)
            self.assertEqual(200, response.status_code)
            self.assertIn('환불·해지 안내의 적용 대상', response.text)
            self.assertNotIn('보증기간 안에는 전액 환불', response.text)
        card = self.client.get(f'/api/card/{self.pid}.json').json()
        cards = self.client.get('/api/cards.json').json()
        self.assertEqual('추가 확인 필요', card['items']['remedy']['status'])
        tested = next(row for row in cards if row['id'] == self.pid)
        self.assertEqual('추가 확인 필요', tested['card']['items']['remedy']['status'])
        self.assertEqual(self.card, db.get_card(self.pid))

    def test_reviewer_view_remains_original(self):
        with patch('app.config.REVIEW_TOKEN', 'local-test-only'):
            self.client.cookies.set('aicard_token', 'local-test-only')
            response = self.client.get(f'/card/{self.pid}?preview=1')
        self.assertEqual(200, response.status_code)
        self.assertIn(self.card['items']['remedy']['summary'], response.text)
        self.assertNotIn('환불·해지 요약의 적용 대상을 추가 점검', response.text)

    def test_frozen_card_storage_is_not_touched_by_display(self):
        with db.connect() as con:
            con.execute('UPDATE products SET id=268 WHERE id=?', (self.pid,))
            con.execute('UPDATE cards SET product_id=268 WHERE product_id=?', (self.pid,))
        before = db.get_card(268)
        result = self.client.get('/api/card/268.json').json()
        self.assertIn('display_scope_notice', result)
        self.assertEqual(before, db.get_card(268))

    def test_explicit_product_warranty_is_not_hidden(self):
        card = copy.deepcopy(self.card)
        card['items']['remedy']['summary'] = '제품 보증기간은 1년입니다. 제조사 고객센터에서 수리를 접수합니다.'
        self.assertEqual(card, safe_public_card(card))


if __name__ == '__main__':
    unittest.main()
