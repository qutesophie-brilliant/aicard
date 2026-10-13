import copy
import unittest
from unittest.mock import patch

from app.schema import basis_counts, effective_status, new_card, public_status, summarize
from app.pipeline.coverage import _pack, _fill_rules
from app.pipeline.easy import make_easy
from app.pipeline.link import _apply
from app.presentation import display_card


class EvidenceCopyTests(unittest.TestCase):
    def claim(self, **extra):
        return {'text': 'AI 기능', 'id': 'c1', 'status': '조건 확인', 'basis_state': '있음',
                'conditions_noted': True, 'evidence': [{'quote': '센서 설명'}], **extra}

    def test_condition_only_is_not_counted_as_operating_evidence(self):
        c = self.claim(basis_kind='none')
        self.assertEqual(effective_status(c), '조건 확인')
        self.assertEqual(public_status(c), '추가 확인 필요')
        card = new_card({}); card['claims'] = [c]
        self.assertEqual(summarize(card)['linked'], 0)
        self.assertEqual(sum(basis_counts(card).values()), 0)

    def test_unknown_kind_stays_in_total(self):
        card=new_card({}); card['claims']=[self.claim(basis_kind=None)]
        self.assertEqual(basis_counts(card)['unknown'],1)
        self.assertEqual(sum(basis_counts(card).values()), summarize(card)['linked'])

    def test_invalid_evidence_indices_never_select_unrelated_text(self):
        cands=[{'doc_id':1,'chunk':0,'quote':'원문','kind':'사양표'}]
        self.assertEqual(_pack(cands,[-1,True,0.2,'0',99]), [])
        self.assertEqual(len(_pack(cands,[0,0])),1)

    def test_link_indices_reject_negative_boolean_float(self):
        claim={'text':'AI 기능','source':{}}
        cands=[{'doc_id':1,'chunk':0,'quote':'센서로 물체를 감지합니다.','kind':'사양표'}]
        _apply(claim,cands,{'status':'근거 확인','evidence':[-1,True,0.5,'0']})
        self.assertEqual(claim['evidence'],[])
        self.assertEqual(claim['basis_state'],'없음')

    def test_easy_preserves_storage_exception_and_feature_scope(self):
        card=new_card({})
        for key, text in [('data','녹화 영상은 클라우드에 24시간 보관하며 스마트폰에 내려받을 수 있어요.'),
                          ('conditions','Wi-Fi에 연결하지 않으면 음성 인식이 작동하지 않을 수 있어요.')]:
            card['items'][key]={'status':'조건 확인','summary':text,'evidence':[]}
        card['easy']={'items':{'data':'하루 뒤 모든 영상이 삭제돼요.'}}
        self.assertEqual(make_easy(card)['items']['data'],card['items']['data']['summary'])
        self.assertEqual(make_easy(card)['items']['conditions'],card['items']['conditions']['summary'])

    def test_rule_quote_is_not_cut_before_exception(self):
        quote='개인정보 데이터 저장 ' + '관련 안내 ' * 40 + '스마트폰 저장본은 삭제하지 않습니다.'
        out=_fill_rules('data',[{'quote':quote,'doc_id':1,'chunk':0,'kind':'설명서'}])
        self.assertEqual(out['summary'],quote)

    def test_display_does_not_mutate_stored_evaluation(self):
        card=new_card({});card['claims']=[self.claim(basis_kind='none')]
        before=copy.deepcopy(card)
        self.assertEqual(display_card(card)['claims'][0]['display_status'],'추가 확인 필요')
        self.assertEqual(card,before)

    def test_rebuild_requires_review_and_easy_follows_reviewed_edits(self):
        from app.pipeline import run
        old=new_card({})
        old['items']['data']={'status':'조건 확인','summary':'검토자가 고친 조건','reviewed':True,'evidence':[]}
        docs=[{'id':1,'kind':'판매페이지','title':'test','url':'','chunks':['AI 기능']}]
        with patch.object(run.db,'get_product',return_value={'id':1,'status':'approved'}), \
             patch.object(run.db,'get_card',return_value=old), \
             patch.object(run.db,'list_documents',return_value=docs), \
             patch.object(run.db,'save_card') as save, \
             patch.object(run.db,'set_product_status') as status, \
             patch.object(run.extract,'extract_claims',return_value=[]), \
             patch.object(run.link,'link_claims',return_value=[]), \
             patch.object(run.coverage,'fill_coverage',return_value=new_card({})['items']):
            result=run.build_card(1)
        status.assert_called_once_with(1,'pending','카드 재생성 후 원문 검토 대기')
        self.assertEqual(result['easy']['items']['data'],'검토자가 고친 조건')


if __name__ == '__main__':
    unittest.main()
