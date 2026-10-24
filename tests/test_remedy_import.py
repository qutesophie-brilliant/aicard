import copy
import json
import re
import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
from app import config, db, revalidation, revalidation_import, revalidation_batch
from app.draft_precheck import digest
from app.main import app


class RemedyImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.old=db.DB_PATH
        db.DB_PATH=Path(self.tmp.name)/'test.sqlite';db.init_db()
        self.pid=db.create_product('제품 A')
        self.card={'product':{'name':'제품 A'},'claims':[{'id':'c1','text':'AI 기능 설명','status':'근거 확인','reviewer_note':'기존 메모'}],
                   'items':{'remedy':{'summary':'범위가 빠진 환불','status':'근거 확인','evidence':[]},'data':{'summary':'기존 데이터 안내','reviewed':False}},
                   'easy':{'items':{'remedy':'기존 쉬운 말','data':'그대로'}},'public_db':{},'sources':[],'checked_at':'2026-10-08'}
        db.save_card(self.pid,self.card,{'provider':'stored-test'});db.set_product_status(self.pid,'approved','기존 승인')
        self.approved_at=db.get_product(self.pid)['approved_at']
    def tearDown(self):
        db.DB_PATH=self.old;self.tmp.cleanup()
    def package(self):
        card=copy.deepcopy(self.card);q='구독케어 무상수리서비스는 품질보증기간 이내 해지 시 전액 환불합니다.'
        card['items']['remedy'].update(summary=q,evidence=[{'doc_id':-1,'quote':q,'kind':'판매페이지'}],reviewed=False)
        card['easy']['items']['remedy']='구독 서비스 해지에 관한 환불입니다.'
        return {'version':1,'title':'문의·보증 부분 검토','held':[],
            'rows':[{'product_id':self.pid,'expected_card_digest':digest(self.card),'card':card,
                'source':{'kind':'판매페이지','title':'현재 원문','url':'https://example.com/product','fetched_at':'2026-10-24T00:00:00Z','text':q*6},
                'summary':'문의·보증만 수정','changes':[],'checks':[]}]}
    def test_stage_is_pending_and_same_upload_reuses_batch(self):
        a=revalidation_import.stage(self.package());b=revalidation_import.stage(self.package())
        self.assertEqual(a['url'],b['url']);self.assertTrue(b['reused'])
        self.assertEqual(db.get_card(self.pid),self.card);self.assertEqual(db.list_documents(self.pid),[])
    def test_partial_approval_preserves_claims_other_items_and_whole_card_approval(self):
        result=revalidation_import.stage(self.package());batch=revalidation_batch.get(result['id'])
        revalidation_batch.approve(result['id'],batch['csrf'],'yes')
        actual=db.get_card(self.pid)
        self.assertEqual(actual['claims'],self.card['claims'])
        self.assertEqual(actual['items']['data'],self.card['items']['data'])
        self.assertEqual(actual['easy']['items']['data'],'그대로')
        self.assertEqual(db.get_product(self.pid)['approved_at'],self.approved_at)
        self.assertEqual(actual['checked_at'],self.card['checked_at'])
        self.assertTrue(actual['items']['remedy']['reviewed'])
        with db.connect() as con:
            row=con.execute("SELECT detail FROM review_log WHERE action='human_item_reapproval'").fetchone()
        self.assertEqual(json.loads(row['detail'])['review_scope'],'remedy')
    def test_unrelated_changes_and_unknown_scope_rejected(self):
        p=self.package();p['rows'][0]['card']['claims'][0]['reviewer_note']='바꾸면 안 됨'
        with self.assertRaises(ValueError):revalidation_import.stage(p)
        p=self.package();p['rows'][0]['card']['items']['data']['reviewed']=True
        with self.assertRaises(ValueError):revalidation_import.stage(p)
    def test_stale_proposal_rejected(self):
        p=self.package();changed=copy.deepcopy(self.card);changed['checked_at']='2026-10-20';db.save_card(self.pid,changed,{})
        with self.assertRaises(ValueError):revalidation_import.stage(p)
    def test_stale_pending_plan_rejected_at_approval(self):
        result=revalidation_import.stage(self.package());batch=revalidation_batch.get(result['id'])
        changed=copy.deepcopy(self.card);changed['checked_at']='2026-10-20';db.save_card(self.pid,changed,{})
        with self.assertRaises(ValueError):revalidation_batch.approve(result['id'],batch['csrf'],'yes')
        self.assertEqual(db.list_documents(self.pid),[])
        self.assertIsNone(revalidation_batch.get(result['id'])['approved_at'])
    def test_nonexistent_quote_rejected(self):
        p=self.package();p['rows'][0]['card']['items']['remedy']['evidence'][0]['quote']='원문에 없는 내용'
        with self.assertRaises(ValueError):revalidation_import.stage(p)
    def test_frozen_card_cannot_be_staged(self):
        p=self.package()
        with db.connect() as con:
            con.execute('UPDATE products SET id=268 WHERE id=?',(self.pid,));con.execute('UPDATE cards SET product_id=268 WHERE product_id=?',(self.pid,))
        p['rows'][0]['product_id']=268
        with self.assertRaises(ValueError):revalidation_import.stage(p)
    def test_authenticated_file_upload_requires_csrf_and_stages_only(self):
        old=config.REVIEW_TOKEN;config.REVIEW_TOKEN='unit-auth'
        try:
            client=TestClient(app,base_url='https://testserver')
            self.assertEqual(client.get('/review/revalidation-import',follow_redirects=False).status_code,303)
            client.cookies.set('aicard_token','unit-auth')
            page=client.get('/review/revalidation-import')
            csrf=re.search(r'name="csrf" value="([^"]+)"',page.text).group(1)
            content=json.dumps(self.package(),ensure_ascii=False).encode()
            self.assertEqual(client.post('/review/revalidation-import',data={'csrf':'wrong'},files={'proposal':('review.json',content,'application/json')}).status_code,403)
            response=client.post('/review/revalidation-import',data={'csrf':csrf},files={'proposal':('review.json',content,'application/json')},follow_redirects=False)
            self.assertEqual(response.status_code,303)
            self.assertTrue(response.headers['location'].startswith('/review/revalidation-batch/'))
            self.assertEqual(db.get_card(self.pid),self.card)
        finally:config.REVIEW_TOKEN=old


if __name__=='__main__':unittest.main()
