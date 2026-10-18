import copy
import json
import tempfile
import unittest
from pathlib import Path
from app import db, revalidation


class RevalidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = db.DB_PATH
        db.DB_PATH = Path(self.tmp.name)/'test.sqlite'
        db.init_db()
        self.pid = db.create_product('제품 A')
        self.card = {'product':{'name':'제품 A'},'claims':[{'id':'c1','text':'원문 문장','status':'근거 확인'}],'items':{},'public_db':{},'sources':[],'checked_at':'2026-10-08'}
        db.save_card(self.pid,self.card,{})
        db.set_product_status(self.pid,'approved','기존 승인')
    def tearDown(self):
        db.DB_PATH = self.old
        self.tmp.cleanup()
    def payload(self):
        with db.connect() as con:
            p,c,docs = revalidation.snapshot(con,self.pid)
        return {'product_id':self.pid,'expected_fingerprint':revalidation.fingerprint(p,c,docs),'card':copy.deepcopy(self.card),'source':{'kind':'판매페이지','title':'제조사 원문','url':'https://example.com/product','fetched_at':'2026-10-18T12:00:00+09:00','text':'제조사 원문 설명입니다. '*20},'summary':'재대조','changes':[],'checks':[]}
    def test_proposal_does_not_modify_live_card_and_approval_is_atomic_idempotent(self):
        plan = revalidation.create(self.payload())
        self.assertEqual(db.get_card(self.pid),self.card)
        self.assertEqual(len(db.list_documents(self.pid)),0)
        row = revalidation.get(plan['id'])
        with self.assertRaises(ValueError): revalidation.approve(plan['id'],'wrong','yes')
        revalidation.approve(plan['id'],row['csrf'],'yes')
        self.assertEqual(len(db.get_card(self.pid)['claims']),1)
        self.assertEqual(len(db.list_documents(self.pid)),1)
        revalidation.approve(plan['id'],row['csrf'],'yes')
        self.assertEqual(len(db.list_documents(self.pid)),1)
        with db.connect() as con:
            log = con.execute("SELECT detail FROM review_log WHERE action='human_reapproval'").fetchone()
        self.assertEqual(json.loads(log['detail'])['before'],self.card)
    def test_stale_plan_rejected_without_partial_writes(self):
        plan = revalidation.create(self.payload()); row = revalidation.get(plan['id'])
        changed = copy.deepcopy(self.card); changed['checked_at']='2026-10-19'
        db.save_card(self.pid,changed,{})
        with self.assertRaises(ValueError): revalidation.approve(plan['id'],row['csrf'],'yes')
        self.assertEqual(len(db.list_documents(self.pid)),0)
        self.assertFalse(revalidation.get(plan['id'])['approved_at'])
    def test_ungrounded_quote_and_changed_counts_rejected(self):
        payload = self.payload();payload['card']['claims'][0]['evidence']=[{'doc_id':-1,'quote':'원문에 없는 문장'}]
        with self.assertRaises(ValueError): revalidation.create(payload)
        payload = self.payload();payload['card']['claims']=[]
        with self.assertRaises(ValueError): revalidation.create(payload)
    def test_frozen_research_cards_rejected(self):
        with db.connect() as con:con.execute('UPDATE products SET id=268 WHERE id=?',(self.pid,));con.execute('UPDATE cards SET product_id=268 WHERE product_id=?',(self.pid,))
        with db.connect() as con:
            with self.assertRaises(ValueError): revalidation.snapshot(con,268)

    def test_explicit_exclusion_archives_original(self):
        payload=self.payload();payload['source']['text']+=' 다른 제품 안내입니다. 원문 문장'
        payload['card']['claims']=[]
        payload['card']['review_exclusions']=[{'id':'c1','text':'원문 문장','reason':'다른 제품의 설명','source_quote':'다른 제품 안내입니다. 원문 문장'}]
        plan=revalidation.create(payload);row=revalidation.get(plan['id'])
        self.assertEqual(len(db.get_card(self.pid)['claims']),1)
        revalidation.approve(plan['id'],row['csrf'],'yes')
        self.assertEqual(db.get_card(self.pid)['claims'],[])
        self.assertEqual(revalidation.get(plan['id'])['payload']['before']['claims'],self.card['claims'])
    def test_exclusion_rejects_addition_status_change_and_ungrounded_quote(self):
        for mode in ('new','status','quote','duplicate'):
            payload=self.payload();ex={'id':'c1','text':'원문 문장','reason':'대상 제품 외 설명','source_quote':'제조사 원문 설명입니다.'}
            payload['card']['review_exclusions']=[ex];payload['card']['claims']=[]
            if mode=='new':payload['card']['claims']=[{'id':'new','status':'근거 확인','text':'새 문구'}]
            if mode=='status':payload['card']['claims']=[dict(self.card['claims'][0],status='자료 부족')]
            if mode=='quote':ex['source_quote']='자료에 없는 설명'
            if mode=='duplicate':payload['card']['review_exclusions']=[ex,ex]
            with self.assertRaises(ValueError):revalidation.create(payload)
