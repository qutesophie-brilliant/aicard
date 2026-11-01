import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import main, check_budget
from app.product_lookup import candidates, exact_product
from app.pipeline import ingest, page_recovery


PRODUCTS = [dict(id=901, name='테스트 세탁기 24kg', model='FX24GNGRX', maker='LG전자',
                 category='세탁기', status='approved', is_sample=0),
            dict(id=902, name='테스트 세탁기 25kg', model='FX25GNGRX', maker='LG전자',
                 category='세탁기', status='approved', is_sample=0)]


class LookupTests(unittest.TestCase):
    def test_exact_model_case_and_name(self):
        for q in ('fx24gngrx', '테스트 세탁기 24kg', 'LG전자 FX24GNGRX'):
            self.assertEqual(901, exact_product(candidates(q, PRODUCTS))['id'])

    def test_model_suffix_and_accessory_title_not_exact(self):
        self.assertEqual([], candidates('FX24GNGRX-PLUS', PRODUCTS, partial=False))
        self.assertEqual([], candidates('FX24GNGRX2', PRODUCTS, partial=False))
        self.assertIsNone(exact_product(candidates('FX24GNGRX 전용 커버', PRODUCTS)))

    def test_different_seller_title_is_candidate_not_automatic_identity(self):
        matches = candidates('특가 LG 트롬 FX24GNGRX | 다른 판매처', PRODUCTS, partial=False)
        self.assertEqual([901], [m['product']['id'] for m in matches])
        self.assertIsNone(exact_product(matches))

    def test_duplicate_model_and_multi_model_require_selection(self):
        self.assertIsNone(exact_product(candidates('FX24GNGRX', PRODUCTS + [dict(PRODUCTS[0], id=903)])))
        matches = candidates('FX24GNGRX / FX25GNGRX', PRODUCTS, partial=False)
        self.assertEqual(2, len(matches))
        self.assertIsNone(exact_product(matches))

    def test_hidden_and_sample_cards_excluded(self):
        self.assertEqual([], candidates('FX24GNGRX', [dict(PRODUCTS[0], status='pending'), dict(PRODUCTS[0], is_sample=1)]))

    def test_partial_name_only_for_explicit_search(self):
        self.assertEqual(2, len(candidates('세탁기', PRODUCTS)))
        self.assertEqual([], candidates('세탁기', PRODUCTS, partial=False))


class LookupRoutesTests(unittest.TestCase):
    def setUp(self):
        self.saved = main._check_hits.copy()
        main._check_hits.clear()
        self.products = patch.object(main.db, 'list_products', return_value=PRODUCTS)
        self.products.start()
        self.client = TestClient(main.app)

    def tearDown(self):
        self.products.stop()
        main._check_hits.clear(); main._check_hits.update(self.saved)

    def test_exact_search_redirects_without_fetch_or_llm(self):
        with patch.object(main, '_read_check_url') as read:
            r = self.client.get('/check/find', params={'q':'FX24GNGRX'}, follow_redirects=False)
        self.assertEqual(303, r.status_code)
        self.assertEqual('/card/901?from=check', r.headers['location'])
        read.assert_not_called()

    def test_search_choices_and_no_result_preserve_url(self):
        r = self.client.get('/check/find', params={'q':'세탁기', 'url':'https://shop.example/item'})
        self.assertIn('/card/901?from=check', r.text)
        self.assertIn('/card/902?from=check', r.text)
        self.assertIn('https://shop.example/item', r.text)
        r = self.client.get('/check/find', params={'q':'<script>missing</script>'})
        self.assertNotIn('<script>missing</script>', r.text)
        self.assertIn('사진·PDF로 분석하기', r.text)

    def test_body_failure_preserves_title_and_offers_existing_card(self):
        def fail(url):
            page_recovery._remember_body('<title>LG FX24GNGRX 판매 상세</title>', url)
            raise ingest.product_page.ProductBodyUnavailable('본문 수집 부족')
        with patch.object(main, '_match_published', return_value=None), patch.object(main, '_read_check_url', side_effect=fail):
            r = self.client.post('/check', data={'url':'https://shop.example/opaque-item'})
        self.assertIn('기존 검토 카드를 찾았습니다', r.text)
        self.assertIn('/card/901?from=check', r.text)
        self.assertNotIn('자료 확인 상태 카드', r.text)

    def test_url_model_on_blocked_page_is_candidate_only(self):
        with patch.object(main, '_match_published', return_value=None), patch.object(main, '_read_check_url', side_effect=ValueError('blocked')):
            r = self.client.post('/check', data={'url':'https://shop.example/product/FX24GNGRX'})
        self.assertEqual(200, r.status_code)
        self.assertIn('모델명까지 확인', r.text)
        self.assertIn('/card/901?from=check', r.text)

    def test_fresh_analysis_bypasses_existing_card_lookup(self):
        ing = ingest.Ingested(title='FX24GNGRX 판매 페이지', text='AI 센서가 세탁물의 무게를 감지하여 설정을 조절합니다.', chunks=[])
        with patch.object(main, '_read_check_url', return_value=ing), patch.object(main.config, 'CHECK_LLM', False):
            r = self.client.post('/check', data={'url':'https://shop.example/item', 'fresh':'true'})
        self.assertNotIn('기존 검토 카드를 찾았습니다', r.text)
        self.assertIn('세탁물', r.text)

    def test_readable_different_seller_title_links_existing_card(self):
        ing = ingest.Ingested(title='특가 LG FX24GNGRX 판매 페이지', text='AI 세탁기 설명입니다.', chunks=[])
        with patch.object(main, '_match_published', return_value=None), patch.object(main, '_read_check_url', return_value=ing), patch.object(main, '_quick_result') as quick:
            r = self.client.post('/check', data={'url':'https://shop.example/new-item'})
        self.assertIn('/card/901?from=check', r.text)
        quick.assert_not_called()

    def test_bookmarklet_uses_same_title_lookup(self):
        with patch.object(main, '_match_published', return_value=None):
            r = self.client.post('/api/check', json={'title':'LG FX24GNGRX 판매', 'url':'https://shop.example/item'})
        self.assertIn('/card/901?from=check', r.text)
        self.assertNotIn('<!DOCTYPE', r.text)

    def test_opaque_blocked_url_offers_search_without_invented_match(self):
        with patch.object(main, '_match_published', return_value=None), patch.object(main, '_read_check_url', side_effect=ValueError('blocked')):
            r = self.client.post('/check', data={'url':'https://shop.example/123'})
        self.assertIn('제품명으로 기존 카드 찾기', r.text)
        self.assertNotIn('/card/901?from=check', r.text)
