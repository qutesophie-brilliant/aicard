import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app import db,draft_precheck

class DraftPrecheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=patch.object(db,"DB_PATH",Path(self.tmp.name)/"test.sqlite3");self.path.start();db.init_db()
        self.pid=db.create_product("제품 A","회사","MODEL","품목")
        text="AI가 콘텐츠를 인식해 주요 화면의 밝기를 조절합니다."
        did=db.add_document(self.pid,"판매페이지","본문","https://example.com",text,[text])
        self.card={"claims":[{"id":"c1","text":"AI가 콘텐츠를 인식해 주요","source":{"doc_id":did,"chunk":0,"quote":text},"reviewer_status":None}],"items":{},"easy":{"headline":"초안","items":{"functions":"화면을 조절해요."}}}
        db.save_card(self.pid,self.card,{"provider":"test"});db.set_product_status(self.pid,"pending","")
        self.payload={"expected_sha256":draft_precheck.digest(self.card),"claim_text_updates":{"c1":text},"note":"문장 중간에서 끊긴 표현을 같은 원문에서 보완"}
    def tearDown(self):self.path.stop();self.tmp.cleanup()
    def test_pending_copy_edit_audited_without_human_approval(self):
        result=draft_precheck.apply(self.pid,self.payload)
        self.assertFalse(result["human_reviewed"])
        self.assertEqual("pending",db.get_product(self.pid)["status"])
        self.assertIsNone(db.get_product(self.pid)["approved_at"])
        self.assertIsNone(db.get_card(self.pid)["claims"][0]["reviewer_status"])
        self.assertEqual("AI",db.get_card_meta(self.pid)["ai_prechecks"][0]["actor"])
        with db.connect() as con:
            r=con.execute("SELECT detail FROM review_log WHERE action='ai_precheck'").fetchone()
            self.assertEqual(self.card,json.loads(r[0])["before"])
    def test_approved_card_rejected(self):
        db.set_product_status(self.pid,"approved","")
        with self.assertRaises(ValueError):draft_precheck.apply(self.pid,self.payload)
        self.assertEqual(self.card,db.get_card(self.pid))
    def test_human_edit_rejected(self):
        self.card["claims"][0]["reviewer_note"]="사람이 확인한 메모";db.save_card(self.pid,self.card,{})
        self.payload["expected_sha256"]=draft_precheck.digest(self.card)
        with self.assertRaises(ValueError):draft_precheck.apply(self.pid,self.payload)
    def test_stale_hash_rejected(self):
        self.payload["expected_sha256"]="stale"
        with self.assertRaises(ValueError):draft_precheck.apply(self.pid,self.payload)
    def test_invented_quote_rejected(self):
        self.payload["claim_text_updates"]["c1"]="AI가 정확도 100%를 보장합니다."
        with self.assertRaises(ValueError):draft_precheck.apply(self.pid,self.payload)
    def test_judgment_or_approval_patch_rejected(self):
        self.payload["status"]="approved"
        with self.assertRaises(ValueError):draft_precheck.apply(self.pid,self.payload)
    def test_route_requires_existing_reviewer_auth(self):
        from fastapi.testclient import TestClient
        from app import main,config
        with patch.object(config,"REVIEW_TOKEN","test-only-secret"):
            client=TestClient(main.app)
            self.assertIn(client.post(f"/review/{self.pid}/ai-precheck",json=self.payload).status_code,(401,403))
            client.cookies.set("aicard_token","test-only-secret")
            response=client.post(f"/review/{self.pid}/ai-precheck",json=self.payload)
            self.assertEqual(200,response.status_code)
            self.assertFalse(response.json()["human_reviewed"])
    def source_payload(self):
        with db.connect() as con:con.execute("DELETE FROM cards WHERE product_id=?",(self.pid,))
        db.set_product_status(self.pid,"draft","")
        db.enqueue_build(self.pid,[],"제품 A","https://example.com")
        return {"expected_model":"MODEL","expected_sources_sha256":draft_precheck.sources_digest(db.list_documents(self.pid)),"sales_text":"AI가 화면의 밝기를 조절합니다.","fine_text":"","note":"제품 비교표 제외"}
    def test_queued_sources_can_be_replaced_and_backed_up(self):
        p=self.source_payload();draft_precheck.refresh_sources(self.pid,p)
        self.assertEqual(p['sales_text'],db.list_documents(self.pid)[0]['text'])
        self.assertEqual('draft',db.get_product(self.pid)['status'])
        with db.connect() as con:self.assertIsNotNone(con.execute("SELECT 1 FROM review_log WHERE action='ai_source_precheck'").fetchone())
    def test_running_sources_cannot_be_changed(self):
        p=self.source_payload();db.next_build()
        with self.assertRaises(ValueError):draft_precheck.refresh_sources(self.pid,p)
    def test_queued_sources_require_same_model_and_version(self):
        p=self.source_payload();p['expected_model']='OTHER'
        with self.assertRaises(ValueError):draft_precheck.refresh_sources(self.pid,p)

if __name__=="__main__":unittest.main()
