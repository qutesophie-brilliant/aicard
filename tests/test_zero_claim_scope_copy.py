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
        self.other_pid=db.create_product('비교용 로봇청소기','다른 제조사','OTHER','로봇청소기')
        db.save_card(self.other_pid,schema.new_card(db.get_product(self.other_pid)),{})
        db.set_product_status(self.other_pid,'approved','검토용 기록')
    def tearDown(self):self.dbpatch.stop();self.tmp.cleanup()
    def test_zero_extraction_is_not_product_absence_across_public_pages(self):
        for url in ['/',f'/card/{self.pid}',f'/compare?a={self.pid}&b={self.other_pid}','/findings']:
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

    def test_correction_form_remains_available_in_easy_mode(self):
        from bs4 import BeautifulSoup
        page=BeautifulSoup(self.client.get(f'/card/{self.pid}').text,'html.parser')
        form=page.select_one('details.report')
        self.assertIsNotNone(form)
        self.assertNotIn('plain-only',form.get('class',[]))
        self.assertIsNotNone(form.select_one('input[name="note"]'))

    def test_comparison_keeps_full_conditions_and_original_source_link(self):
        from bs4 import BeautifulSoup
        quote='판매페이지에는 제품 보증과 모터의 보증 범위가 다르다고 안내합니다.'
        did=db.add_document(self.pid,'판매페이지','공식 제품 안내','https://example.test/product',quote,[quote])
        card=copy.deepcopy(self.card)
        claim='AI 기능은 카메라 센서로 사물을 구별하며 바닥과 주변 조명, 물체의 형태에 따라 인식 결과가 달라질 수 있으므로 해당 기능의 사용 범위를 원문에서 확인해야 합니다.'
        condition='이 기능을 사용하려면 앱 설치 및 기기 연동이 필요하고, 주변 조도와 설치 환경의 영향을 받을 수 있으며, 사전에 지도 그리기와 구역 편집을 모두 완료해야 합니다. 이 설정은 최초 사용 전에 확인해야 합니다.'
        card['claims']=[{'id':'c1','text':claim,'status':'조건 확인','conditions':condition,
                         'evidence':[{'doc_id':did,'chunk':0,'quote':quote,'kind':'판매페이지'}]}]
        card['items']['remedy'].update(summary=quote,evidence=[{'doc_id':did,'chunk':0,'quote':quote,'kind':'판매페이지'}])
        db.save_card(self.pid,card,{})
        page=BeautifulSoup(self.client.get(f'/compare?a={self.pid}&b={self.other_pid}').text,'html.parser')
        texts=page.get_text(' ',strip=True)
        self.assertIn(claim,texts)
        self.assertIn(condition,texts)
        original=page.select_one('details.ev a[href="https://example.test/product"]')
        self.assertIsNotNone(original)
        self.assertEqual('_blank',original.get('target'))
        self.assertIn('noopener',original.get('rel',[]))
        claim_source=page.select_one('#compare-what .claims details.ev a')
        self.assertIsNotNone(claim_source)
        self.assertEqual('https://example.test/product',claim_source['href'])
        self.assertEqual(db.get_card(self.pid),card)

    def test_comparison_rejects_duplicate_or_different_category_pairs(self):
        from bs4 import BeautifulSoup
        another=db.create_product('비교용 냉장고','제조사','FRIDGE','냉장고')
        db.save_card(another,schema.new_card(db.get_product(another)),{})
        db.set_product_status(another,'approved','검토용 기록')
        for right in [self.pid,another]:
            page=BeautifulSoup(self.client.get(f'/compare?a={self.pid}&b={right}').text,'html.parser')
            self.assertIsNone(page.select_one('#compare-what'))
            self.assertIn('같은 품목의 서로 다른 제품 두 개',page.select_one('.notice').text)


if __name__=='__main__':unittest.main()
