import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from app import db,release_review
from app.draft_precheck import digest,sources_digest

class ReleaseReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.patch=patch.object(db,'DB_PATH',Path(self.tmp.name)/'db.sqlite3');self.patch.start();db.init_db()
        self.ids=[self.seed('A'),self.seed('B')]
    def tearDown(self):self.patch.stop();self.tmp.cleanup()
    def seed(self,name):
        pid=db.create_product(name,model=name);q='센서로 밝기를 감지해 화면을 조절합니다.';did=db.add_document(pid,'판매페이지','본문','https://example.com',q,[q])
        db.save_card(pid,{'claims':[{'id':'c1','text':'AI 밝기','status':'자료 부족','basis_state':'없음','conditions_noted':False,'reviewer_status':None,'reviewer_note':'','evidence':[{'doc_id':did,'quote':q}]}],'items':{'data':{'summary':'미확인'}}},{'provider':'test'});db.set_product_status(pid,'pending');return pid
    def payload(self):
        return {'title':'확인안','summary':'조회 범위 확인','cards':[{'id':pid,'expected_sha256':digest(db.get_card(pid)),'expected_sources_sha256':sources_digest(db.list_documents(pid)),'recommendation':'ready' if i==0 else 'hold','reason':'원문 확인' if i==0 else '제공 상태 미확인','decisions':[{'claim_id':'c1','basis':'있음','conditions_noted':False,'reason':'센서와 제어 동작이 명시되어 있습니다.'}]} for i,pid in enumerate(self.ids)]}
    def plan(self,payload=None):
        r=release_review.create(payload or self.payload());return release_review.get(r['id'])
    def test_staging_changes_no_card_or_publication(self):
        before=[db.get_card(p) for p in self.ids];r=self.plan()
        self.assertEqual(before,[db.get_card(p) for p in self.ids]);self.assertTrue(all(db.get_product(p)['status']=='pending' for p in self.ids));self.assertFalse(r['approved_at'])
    def test_approval_only_ready_and_preserves_original_prediction(self):
        r=self.plan();release_review.approve(r['id'],r['csrf'],'yes')
        cl=db.get_card(self.ids[0])['claims'][0];self.assertEqual('자료 부족',cl['status']);self.assertEqual('근거 확인',cl['reviewer_status']);self.assertEqual('approved',db.get_product(self.ids[0])['status']);self.assertEqual('pending',db.get_product(self.ids[1])['status'])
        self.assertTrue(release_review.approve(r['id'],r['csrf'],'yes')['already_approved'])
        with db.connect() as con:self.assertEqual(1,con.execute("SELECT count(*) FROM review_log WHERE action='human_release_approval'").fetchone()[0])
    def test_ack_and_csrf_are_required(self):
        r=self.plan()
        for csrf,ack in [('bad','yes'),(r['csrf'],'')]:
            with self.assertRaises(ValueError):release_review.approve(r['id'],csrf,ack)
        self.assertTrue(all(db.get_product(p)['status']=='pending' for p in self.ids))
    def test_stale_second_card_blocks_whole_batch(self):
        p=self.payload();p['cards'][1]['recommendation']='ready';r=self.plan(p);c=db.get_card(self.ids[1]);c['new']='changed';db.save_card(self.ids[1],c)
        with self.assertRaises(ValueError):release_review.approve(r['id'],r['csrf'],'yes')
        self.assertTrue(all(db.get_product(p)['status']=='pending' for p in self.ids))
    def test_source_change_blocks_approval(self):
        r=self.plan();db.add_document(self.ids[0],'설명서','새 원문','','새 원문',['새 원문'])
        with self.assertRaises(ValueError):release_review.approve(r['id'],r['csrf'],'yes')
    def test_human_edits_protected(self):
        r=self.plan();c=db.get_card(self.ids[0]);c['claims'][0]['reviewer_note']='사람 메모';db.save_card(self.ids[0],c)
        with self.assertRaises(ValueError):release_review.approve(r['id'],r['csrf'],'yes')
    def test_frozen_and_published_protected(self):
        with patch.object(release_review,'FROZEN',{self.ids[0]}):
            with self.assertRaises(ValueError):self.plan()
        db.set_product_status(self.ids[0],'approved')
        with self.assertRaises(ValueError):self.plan()
    def test_duplicate_cards_and_claims_rejected(self):
        p=self.payload();p['cards'].append(copy.deepcopy(p['cards'][0]))
        with self.assertRaises(ValueError):self.plan(p)
        p=self.payload();p['cards'][0]['decisions']*=2
        with self.assertRaises(ValueError):self.plan(p)
    def test_invalid_axis_rejected(self):
        p=self.payload();p['cards'][0]['decisions'][0].update(basis='없음',conditions_noted=True)
        with self.assertRaises(ValueError):self.plan(p)
    def test_routes_private_render_and_final_explicit_action(self):
        from fastapi.testclient import TestClient
        from app import main,config
        with patch.object(config,'REVIEW_TOKEN','test-release-secret'):
            client=TestClient(main.app)
            self.assertIn(client.post('/review/release-plan',json=self.payload()).status_code,(401,403))
            client.cookies.set('aicard_token','test-release-secret')
            r=client.post('/review/release-plan',json=self.payload())
            self.assertEqual(200,r.status_code)
            url=r.json()['url'];plan=release_review.get(r.json()['id'])
            view=client.get(url)
            self.assertEqual(200,view.status_code)
            self.assertIn('1장 최종 승인하고 공개',view.text)
            self.assertEqual('no-store',view.headers['cache-control'])
            client.cookies.clear()
            self.assertIn(client.get(url,follow_redirects=False).status_code,(303,401,403))
            client.cookies.set('aicard_token','test-release-secret')
            self.assertEqual(403,client.post(url+'/approve',data={'csrf':plan['csrf'],'ack':'yes'},headers={'Origin':'https://other.example'}).status_code)
            denied=client.post(url+'/approve',data={'csrf':plan['csrf']},follow_redirects=False)
            self.assertEqual(303,denied.status_code)
            self.assertEqual('pending',db.get_product(self.ids[0])['status'])
            accepted=client.post(url+'/approve',data={'csrf':plan['csrf'],'ack':'yes'},headers={'Origin':'http://testserver'})
            self.assertEqual(200,accepted.status_code)
            self.assertEqual('approved',db.get_product(self.ids[0])['status'])
            self.assertEqual('pending',db.get_product(self.ids[1])['status'])

if __name__=='__main__':unittest.main()
