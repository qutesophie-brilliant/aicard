import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import httpx
from PIL import Image
from fastapi.testclient import TestClient
from app import db,main
from app.pipeline import llm,ocr,product_ocr,product_images,source_capture,ingest

URL='https://example.com/product/example'
HTML='<div class="product-description"><p>AI가 오염도를 감지해 세탁 시간을 조절합니다.</p><img src="/PC_02.jpg"><img src="/MO_02.jpg"><img src="/other.jpg"></div>'


def png():
    output=io.BytesIO();Image.new('RGB',(30,30),'white').save(output,format='PNG');return output.getvalue()


class OcrConsistencyTests(unittest.TestCase):
    def test_missing_middle_response_never_shifts_source(self):
        with patch.object(llm,'ask_json',return_value={'pages':[{'image':3,'text':'세 번째'},{'image':1,'text':'첫 번째'}]}):
            self.assertEqual(['첫 번째',None,'세 번째'],llm.ocr_images_indexed([b'a',b'b',b'c']))

    def test_duplicate_or_invalid_identity_is_not_used_as_evidence(self):
        pages=[{'image':1,'text':'가'},{'image':1,'text':'나'},{'image':True,'text':'오류'},
               {'image':4,'text':'범위 밖'},{'image':2,'text':{'text':'잘못된 형식'}}]
        with patch.object(llm,'ask_json',return_value={'pages':pages}):
            self.assertEqual([None,None],llm.ocr_images_indexed([b'a',b'b']))

    def test_ocr_budget_is_scoped_and_only_allows_one_http_attempt(self):
        self.assertIsNone(llm._ocr_budget.get())
        def fake(*args,**kwargs):
            self.assertLessEqual(llm._request_timeout(180),3)
            with self.assertRaises(llm.LLMError):llm._request_timeout(180)
            return {'pages':[{'image':1,'text':'원문'}]}
        with patch.object(llm,'ask_json',side_effect=fake):llm.ocr_images_indexed([b'a'],timeout_seconds=3)
        self.assertIsNone(llm._ocr_budget.get())

    def test_only_empty_image_retried_once_successful_text_unchanged(self):
        with patch.object(ocr,'_shrink',side_effect=lambda b:b),patch.object(llm,'available',return_value=True),\
             patch.object(llm,'ocr_images_indexed',side_effect=[['첫 번째',None,'세 번째'],['복구한 둘째']]) as model:
            rows=product_ocr.read_images([b'a',b'b',b'c'])
        self.assertEqual(['첫 번째','복구한 둘째','세 번째'],[r['text'] for r in rows])
        self.assertEqual([1,2,1],[r['attempts'] for r in rows])
        self.assertEqual([b'b'],model.call_args_list[1].args[0])

    def test_empty_batch_recovery_has_one_image_cap_and_does_not_invent_text(self):
        with patch.object(ocr,'_shrink',side_effect=lambda b:b),patch.object(llm,'available',return_value=True),\
             patch.object(llm,'ocr_images_indexed',side_effect=[[None,'',''],['']]) as model:
            rows=product_ocr.read_images([b'a',b'b',b'c'])
        self.assertEqual(2,model.call_count)
        self.assertEqual(['','',''],[r['text'] for r in rows])
        self.assertTrue(all(r['status']=='no_text' for r in rows))

    def test_timeout_is_distinct_from_empty_text(self):
        with patch.object(ocr,'_shrink',side_effect=lambda b:b),patch.object(llm,'available',return_value=True),\
             patch.object(llm,'ocr_images_indexed',side_effect=httpx.ReadTimeout('slow')),\
             patch.object(ocr,'read_images',return_value=ocr.OcrResult(texts=[''],engine='none')):
            rows=product_ocr.read_images([b'a'])
        self.assertEqual('timeout',rows[0]['status'])
        self.assertEqual('',rows[0]['text'])

    def test_ai_connection_error_retains_local_fallback_and_source_order(self):
        with patch.object(ocr,'_shrink',side_effect=lambda b:b),patch.object(llm,'available',return_value=True),\
             patch.object(llm,'ocr_images_indexed',side_effect=httpx.ConnectError('offline')) as model,\
             patch.object(ocr,'read_images',side_effect=lambda images, **kw: ocr.OcrResult(
                 texts=[{b'a':'첫 원문',b'b':'둘째 원문'}[image] for image in images],engine='tesseract')) as local:
            rows=product_ocr.read_images([b'a',b'b'])
        self.assertEqual(['첫 원문','둘째 원문'],[r['text'] for r in rows])
        self.assertTrue(all(r['engine']=='tesseract' and r['recovered'] for r in rows))
        self.assertEqual(1,model.call_count)
        self.assertEqual([[b'a'], [b'b']], [call.args[0] for call in local.call_args_list])
        self.assertTrue(all(0 < call.kwargs['local_timeout_seconds'] <= 8 for call in local.call_args_list))

    def test_local_fallback_keeps_source_identity(self):
        with patch.object(ocr,'_shrink',side_effect=lambda b:b),patch.object(ocr,'read_images',side_effect=[
            ocr.OcrResult(texts=['가'],engine='tesseract'),ocr.OcrResult(texts=[''],engine='tesseract')]):
            rows=product_ocr.read_images([b'a',b'b'],allow_llm=False)
        self.assertEqual(['read','no_text'],[r['status'] for r in rows])

    def test_mobile_variant_skipped_only_when_desktop_counterpart_present(self):
        capture=source_capture.capture_product_html(HTML,URL)
        product_images.capture_images(HTML,URL,capture,fetch_bytes=lambda url:png(),
            reader=lambda b:ocr.OcrResult(texts=['원문 설명'],engine='test'))
        self.assertEqual(1,capture['coverage']['image_duplicate_variants'])
        self.assertFalse(any('/MO_02.jpg' in r['url'] for r in capture['images']))
        only='<div class="product-description"><p>제품 설명</p><img src="/MO_02.jpg"></div>'
        capture=source_capture.capture_product_html(only,URL)
        product_images.capture_images(only,URL,capture,fetch_bytes=lambda url:png(),
            reader=lambda b:ocr.OcrResult(texts=['설명'],engine='test'))
        self.assertEqual(0,capture['coverage']['image_duplicate_variants'])
        self.assertEqual(1,len(capture['images']))

    def test_download_failure_unread_and_limit_are_separate(self):
        html='<div class="product-description"><p>제품 설명</p>'+''.join(f'<img src="/{i}.jpg">' for i in range(4))+'</div>'
        capture=source_capture.capture_product_html(html,URL)
        def download(url):
            if url.endswith('0.jpg'):raise httpx.ReadTimeout('slow')
            return png()
        product_images.capture_images(html,URL,capture,fetch_bytes=download,
            reader=lambda b:ocr.OcrResult(texts=[''],engine='test'),limit=2)
        self.assertEqual(['download_timeout','no_text','skipped_limit','skipped_limit'],[r['status'] for r in capture['images']])
        self.assertEqual(2,capture['images'][0]['download_attempts'])
        self.assertEqual(1,capture['coverage']['image_download_failed'])
        self.assertEqual(1,capture['coverage']['image_ocr_unconfirmed'])
        self.assertEqual(2,capture['coverage']['image_skipped_limit'])
        self.assertEqual(4,capture['coverage']['image_unread'])

    def test_download_retry_recovers_transient_failure_without_retrying_other_images(self):
        capture=source_capture.capture_product_html(HTML,URL)
        calls=[]
        def download(url):
            calls.append(url)
            if len(calls)==1:raise httpx.ReadTimeout('slow')
            return png()
        product_images.capture_images(HTML,URL,capture,fetch_bytes=download,
            reader=lambda b:ocr.OcrResult(texts=['설명'],engine='test'))
        self.assertEqual(3,len(calls))
        self.assertEqual([2,1],[r['download_attempts'] for r in capture['images']])

    def test_confirmed_download_failure_not_retried(self):
        capture=source_capture.capture_product_html(HTML,URL)
        response=httpx.Response(404,request=httpx.Request('GET',URL))
        with patch.object(product_images,'ocr'):
            with patch('builtins.print'):
                def fail(url):raise httpx.HTTPStatusError('not found',request=response.request,response=response)
                product_images.capture_images(HTML,URL,capture,fetch_bytes=fail)
        self.assertTrue(all(r['download_attempts']==1 for r in capture['images']))


class OcrStatusIntegrationTests(unittest.TestCase):
    def test_consumer_can_distinguish_skipped_failed_and_unconfirmed_images(self):
        capture=source_capture.capture_product_html(HTML,URL)
        def fake_capture(html,url,cap):
            cap['images']=[{'url':URL+f'/{i}.jpg','image_number':i,'text':'','engine':'none','status':status,'ocr_attempts':0}
                           for i,status in enumerate(['skipped_limit','download_timeout','no_text'],1)]
            cap['coverage'].update(image_candidates=3,image_attempted=2,image_skipped_limit=1,image_download_failed=1,image_ocr_unconfirmed=1)
        with patch.object(product_images,'capture_images',side_effect=fake_capture):
            result=ingest.from_product_html(HTML,URL,ocr_images=True)
        self.assertTrue(any('3장 중 2장을 확인 대상으로 선택' in note for note in result.notes))
        with tempfile.TemporaryDirectory() as d,patch.object(db,'DB_PATH',Path(d)/'scratch.sqlite3'):
            db.init_db()
            with patch.object(main,'_read_check_url',return_value=result),patch.object(main,'_rate_ok',return_value=True):
                page=TestClient(main.app).post('/check',data={'url':URL})
        self.assertEqual(200,page.status_code)
        for label in ('이미지별 확인 상태','이번 분석에서 미시도','이미지 수집 실패','글자 읽기 결과 미확인','이미지에 글자가 없다는 뜻은 아닙니다'):
            self.assertIn(label,page.text)
        self.assertNotIn('확인 대상 이미지 3장 중 0장의 글자를 AI',page.text)


if __name__=='__main__':unittest.main()
