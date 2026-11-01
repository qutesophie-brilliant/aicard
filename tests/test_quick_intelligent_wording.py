import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
from app import db, main
from app.pipeline import extract
from app.pipeline.run import quick_check

HEADING = '지능형 자동 습도 조절 센서로'
CONTROL = '자동모드에서 원하는 습도를 설정하면, 목표 습도에 맞춰 가습량을 자동으로 조절합니다.'
TEXT = HEADING + '\n건조함도 과습도 걱정 없이\n' + CONTROL


class QuickIntelligentWordingTests(unittest.TestCase):
    def test_intelligent_claim_links_actual_humidity_control(self):
        card, docs = quick_check('다룸 퓨어 AI 가습기', '', TEXT)
        self.assertEqual(1, len(card['claims']))
        self.assertEqual(HEADING, card['claims'][0]['text'])
        self.assertEqual('지능형', card['claims'][0]['label'])
        self.assertTrue(card['quick_has_intelligent_wording'])
        self.assertIn(CONTROL, [e['quote'] for e in card['claims'][0]['evidence']])
        for e in card['claims'][0]['evidence']:
            self.assertIn(e['quote'], docs[e['doc_id']]['text'])

    def test_plain_automation_does_not_become_ai_claim(self):
        card, _ = quick_check('자동 가습기', '', CONTROL)
        self.assertEqual([], card['claims'])
        self.assertFalse(card['quick_has_intelligent_wording'])

    def test_control_line_wraps_preserve_source_and_sensor_basis(self):
        text = TEXT.replace(', 목표', ',\n목표')
        card, _ = quick_check('다룸 퓨어 AI 가습기', '', text)
        claim = card['claims'][0]
        self.assertEqual('sensor', claim['basis'])
        self.assertTrue(any(CONTROL in e['quote'] for e in claim['evidence']))

    def test_control_fallback_rejects_negation_other_heading_and_other_document(self):
        from app.pipeline import quick_controls
        def check(lines, extra=None):
            claim={'text':HEADING,'source':{'doc_id':1,'chunk':0},'evidence':[], 'basis_state':'없음'}
            docs=[{'id':1,'kind':'판매페이지','chunks':lines}]+(extra or [])
            return quick_controls.connect([claim],docs)[0]['evidence']
        self.assertEqual([],check([HEADING,CONTROL.replace('조절합니다','조절하지 못합니다')]))
        self.assertEqual([],check([HEADING,'AI 온도 조절',CONTROL]))
        self.assertEqual([],check([HEADING], [{'id':2,'kind':'판매페이지','chunks':[CONTROL]}]))

    def test_functional_context_required_and_formal_rules_unchanged(self):
        for text in ['지능형 디자인과 아름다운 색상', '지능형 제품 할인 쿠폰 받기']:
            card, _ = quick_check('제품', '', text)
            self.assertEqual([], card['claims'])
        source = [{'quote': HEADING, 'doc_id': 1, 'chunk': 0}]
        self.assertEqual([], extract._rules_extract(source,14))
        self.assertEqual(HEADING, extract._rules_extract(source,14,complete_quotes=True)[0]['text'])

    def test_result_names_intelligent_wording_without_asserting_ai_technology(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(db,'DB_PATH',Path(temp)/'isolated.sqlite3'), \
             patch.object(main,'_rate_ok',return_value=True):
            db.init_db()
            response = TestClient(main.app).post('/check',data={'text':'다룸 퓨어 AI 가습기\n'+TEXT})
            self.assertEqual(200,response.status_code)
            soup=BeautifulSoup(response.text,'html.parser')
            self.assertIn('AI·지능형 표시 문구',soup.select_one('.lb-verdict').get_text())
            self.assertIn('AI 기술의 적용 여부를 판단하지는 않습니다',soup.get_text())
            self.assertIn(CONTROL,soup.select_one('.claims').get_text())
            self.assertEqual([],db.list_products())


if __name__=='__main__': unittest.main()
