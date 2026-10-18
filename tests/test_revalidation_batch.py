import copy,json,tempfile,unittest
from pathlib import Path
from app import db,revalidation,revalidation_batch

class BatchApprovalTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.old=db.DB_PATH;db.DB_PATH=Path(self.tmp.name)/'db';db.init_db();self.ids=[];self.plans=[]
  for name in ['제품 A','제품 B']:
   pid=db.create_product(name);card={'product':{'name':name},'claims':[],'items':{},'sources':[],'public_db':{},'checked_at':'2026-10-08'};db.save_card(pid,card,{});db.set_product_status(pid,'approved','기존 승인');self.ids.append(pid)
   with db.connect() as con:p,c,docs=revalidation.snapshot(con,pid)
   payload={'product_id':pid,'expected_fingerprint':revalidation.fingerprint(p,c,docs),'card':card,'source':{'kind':'판매페이지','title':'원문','url':'https://example.com/product','fetched_at':'2026-10-18','text':'현재 제조사 원문 설명입니다. '*20},'summary':'현재 자료 대조','changes':[],'checks':[]}
   self.plans.append(revalidation.create(payload)['id'])
 def tearDown(self):db.DB_PATH=self.old;self.tmp.cleanup()
 def test_batch_commits_only_ready_and_is_idempotent(self):
  batch=revalidation_batch.create({'title':'대조','ready':self.plans,'held':[{'id':999,'model':'보류제품','reason':'원문 추가 확인'}]});row=revalidation_batch.get(batch['id'])
  revalidation_batch.approve(batch['id'],row['csrf'],'yes');revalidation_batch.approve(batch['id'],row['csrf'],'yes')
  self.assertEqual([len(db.list_documents(pid)) for pid in self.ids],[1,1]);self.assertTrue(revalidation_batch.get(batch['id'])['approved_at'])
 def test_stale_second_card_rolls_back_first_card_too(self):
  batch=revalidation_batch.create({'title':'대조','ready':self.plans,'held':[]});row=revalidation_batch.get(batch['id'])
  card=db.get_card(self.ids[1]);card['checked_at']='2026-10-19';db.save_card(self.ids[1],card,{})
  with self.assertRaises(ValueError):revalidation_batch.approve(batch['id'],row['csrf'],'yes')
  self.assertEqual([len(db.list_documents(pid)) for pid in self.ids],[0,0]);self.assertFalse(revalidation.get(self.plans[0])['approved_at']);self.assertFalse(revalidation_batch.get(batch['id'])['approved_at'])
 def test_consent_and_duplicate_targets_are_required(self):
  with self.assertRaises(ValueError):revalidation_batch.create({'title':'대조','ready':self.plans,'held':[{'id':self.ids[0],'reason':'중복'}]})
  batch=revalidation_batch.create({'title':'대조','ready':self.plans,'held':[]});row=revalidation_batch.get(batch['id'])
  with self.assertRaises(ValueError):revalidation_batch.approve(batch['id'],row['csrf'],'no')
