import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import db,main,schema


class ZeroClaimScopeCopyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.dbpatch=patch.object(db,'DB_PATH',Path(self.tmp.name)/'test.sqlite3');self.dbpatch.start();db.init_db()
        self.pid=db.create_product('검토용 로봇청소기','제조사','MODEL','로봇청소기')
        self.card=schema.new_card(db.get_product(self.pid));db.save_card(self.pid,self.card,{})
        db.set_product_status(self.pid,'approved','검토용 기록');self.client=TestClient(main.app)
    def tearDown(self):self.dbpatch.stop();self.tmp.cleanup()
    def test_zero_extraction_is_not_product_absence_across_public_pages(self):
        for url in ['/',f'/card/{self.pid}',f'/compare?a={self.pid}&b={self.pid}','/findings']:
            response=self.client.get(url);self.assertEqual(response.status_code,200)
            self.assertIn('찾지 못',response.text)
            self.assertNotIn('‘AI’를 내세우지 않는 제품',response.text)
            self.assertNotIn('제품 이름이나 광고에 ‘AI’를 쓰지 않는 제품',response.text)
        self.assertEqual(db.get_card(self.pid),self.card)
        self.assertEqual(self.client.get(f'/api/card/{self.pid}.json').json(),self.card)
    def test_explicit_source_scope_is_preserved_and_no_turn_off_advice_for_zero(self):
        card=copy.deepcopy(self.card);card['zero_claim_scope']='이미지를 읽지 못한 범위를 포함한 제한적 결과입니다.'
        db.save_card(self.pid,card,{})
        page=self.client.get(f'/card/{self.pid}').text
        self.assertIn(card['zero_claim_scope'],page)
        self.assertNotIn('끄면 어떤 모드로 동작하는지',page)
        self.assertEqual(schema.summarize(card)['short'],card['zero_claim_scope'])
        self.assertFalse(any(h['anchor']=='q-optout' for h in schema.heads_up(card)))
    def test_positive_claim_card_retains_missing_optout_advice(self):
        card=copy.deepcopy(self.card)
        card['claims']=[{'id':'c1','text':'AI 작동 설명','status':'자료 부족'}]
        self.assertTrue(any(h['anchor']=='q-optout' for h in schema.heads_up(card)))
        db.save_card(self.pid,card,{})
        self.assertIn('끄면 어떤 모드로 동작하는지',self.client.get(f'/card/{self.pid}').text)


if __name__=='__main__':unittest.main()
