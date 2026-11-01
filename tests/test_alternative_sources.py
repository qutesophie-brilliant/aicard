import unittest
from unittest.mock import patch
import httpx
from fastapi.testclient import TestClient
from app import alternative_sources as alt, main

ORIGINAL = 'https://smartstore.naver.com/store/products/1234'
SOURCE = 'https://maker.example/products/FX24GNGRX'


def payload(url=SOURCE):
    return {'candidates': [{'groundingMetadata': {
        'groundingChunks': [{'web': {'uri': url, 'title': '제조사 FX24GNGRX'}}],
        'searchEntryPoint': {'renderedContent': '<div>Google Search</div>'}}}]}


class AlternativeTests(unittest.TestCase):
    def test_metadata_only_not_generated_urls(self):
        p = {'candidates': [{'content': {'parts': [{'text': 'https://invented.example/item'}]}}]}
        self.assertEqual([], alt.parse_sources(p, ORIGINAL)['sources'])

    def test_excludes_original_duplicate_and_unsafe_urls(self):
        p = payload()
        chunks = p['candidates'][0]['groundingMetadata']['groundingChunks']
        for url in [SOURCE, ORIGINAL, 'javascript:alert(1)', 'https://127.0.0.1/admin', 'https://x.example/?token=secret']:
            chunks.append({'web': {'uri': url, 'title': 'test'}})
        self.assertEqual([SOURCE], [s['url'] for s in alt.parse_sources(p, ORIGINAL)['sources']])

    def test_sensitive_query_is_not_sent(self):
        for url in ['https://shop.example/item?api_key=private', 'https://user:pass@shop.example/item', 'https://localhost/item']:
            with patch.object(alt.config, 'GEMINI_API_KEY', 'test'), patch.object(alt.httpx, 'post') as post:
                self.assertEqual('unavailable', alt.search(url)['status'])
                post.assert_not_called()

    def test_tracking_removed_model_retained(self):
        self.assertEqual('https://shop.example/item?model=ABC123', alt.public_hint('https://shop.example/item?model=ABC123&utm_source=foo#tracking'))

    def test_citation_redirect_preserved_but_analysis_uses_actual_page(self):
        citation = 'https://vertexaisearch.cloud.google.com/grounding-api-redirect/example'
        response = httpx.Response(302, headers={'location': SOURCE}, request=httpx.Request('GET', citation))
        with patch.object(alt, 'fetch_public', return_value=response):
            result = alt.resolve_sources(alt.parse_sources(payload(citation), ORIGINAL), ORIGINAL)
        self.assertEqual(citation, result['sources'][0]['url'])
        self.assertEqual(SOURCE, result['sources'][0]['analysis_url'])

    def test_resolved_original_is_not_an_alternative(self):
        citation = 'https://vertexaisearch.cloud.google.com/grounding-api-redirect/example'
        response = httpx.Response(302, headers={'location': ORIGINAL}, request=httpx.Request('GET', citation))
        with patch.object(alt, 'fetch_public', return_value=response):
            result = alt.resolve_sources(alt.parse_sources(payload(citation), ORIGINAL), ORIGINAL)
        self.assertEqual([], result['sources'])

    def test_identification_video_is_not_offered_as_product_page(self):
        citation = 'https://vertexaisearch.cloud.google.com/grounding-api-redirect/example'
        response = httpx.Response(302, headers={'location': 'https://www.youtube.com/watch?v=example'}, request=httpx.Request('GET', citation))
        with patch.object(alt, 'fetch_public', return_value=response):
            result = alt.resolve_sources(alt.parse_sources(payload(citation), ORIGINAL), ORIGINAL)
        self.assertEqual([], result['sources'])

    def test_category_and_explicit_model_suffix_mismatch_are_excluded(self):
        title = '삼성 AI 냉장고 RM70F90R2DD'
        for target in ('https://m.ssg.com/sellerhome/104777/category?dispCtgId=6000208876',
                       'https://www.samsung.com/sec/support/model/RM70F90R2D/'):
            result = alt.resolve_sources(alt.parse_sources(payload(target), ORIGINAL), ORIGINAL, title)
            self.assertEqual([], result['sources'])
        for target in ('https://www.samsung.com/sec/refrigerators/product/RM70F90R2DD/',
                       'https://shop.example/product/1234567',
                       'https://shop.example/product/humidifier/80/category/115/display/1/'):
            result = alt.resolve_sources(alt.parse_sources(payload(target), ORIGINAL), ORIGINAL, title)
            self.assertEqual(target, result['sources'][0]['analysis_url'])

    def test_single_grounded_call_and_no_retry(self):
        with patch.object(alt.config, 'GEMINI_API_KEY', 'test'), patch.object(alt, 'public_addresses'), patch.object(alt.httpx, 'post', side_effect=httpx.ConnectError('offline')) as post:
            self.assertEqual('unavailable', alt.search(ORIGINAL, '세탁기 ABC123')['status'])
            self.assertEqual(1, post.call_count)
            self.assertEqual([{'google_search': {}}], post.call_args.kwargs['json']['tools'])

    def test_provider_result_preserves_source(self):
        response = httpx.Response(200, json=payload(), request=httpx.Request('POST', 'https://api.example'))
        with patch.object(alt.config, 'GEMINI_API_KEY', 'test'), patch.object(alt, 'public_addresses'), patch.object(alt.httpx, 'post', return_value=response):
            self.assertEqual(SOURCE, alt.search(ORIGINAL, 'FX24GNGRX')['sources'][0]['url'])

    def test_key_prefix_does_not_select_an_unconfigured_vertex_endpoint(self):
        response = httpx.Response(200, json=payload(), request=httpx.Request('POST', 'https://api.example'))
        with patch.object(alt.config, 'GEMINI_API_KEY', 'AQ.test'), patch.object(alt, 'public_addresses'), patch.object(alt.httpx, 'post', return_value=response) as post:
            alt.search(ORIGINAL, 'FX24GNGRX')
        self.assertEqual(alt.config.GEMINI_ENDPOINTS[0].format(model=alt.config.GEMINI_MODEL), post.call_args.args[0])


class AlternativeRoutesTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)

    def test_no_ai_copy_keeps_analysis_and_offers_other_source_search(self):
        response = self.client.post('/check', data={'url': SOURCE, 'text': '모델 ABC123 냉장고\n냉장고 용량은 600리터이며 제품의 무상 보증 기간은 1년입니다.'})
        self.assertEqual(200, response.status_code)
        self.assertIn('이 제품의 다른 설명 페이지 찾기', response.text)
        self.assertIn('action="/check/alternatives"', response.text)
        self.assertIn('소비자 권익 항목별 확인 결과', response.text)

    def test_blocked_url_automatically_searches_and_never_analyzes_search_text(self):
        with patch.object(main, '_match_published', return_value=None), patch.object(main, '_product_candidates', return_value=[]), patch.object(main, '_read_check_url', side_effect=ValueError('blocked')), patch.object(alt, 'search', return_value=alt.parse_sources(payload(), ORIGINAL)) as search, patch.object(main, '_quick_result') as quick:
            response = self.client.post('/check', data={'url': ORIGINAL})
        search.assert_called_once_with(ORIGINAL, '')
        quick.assert_not_called()
        self.assertIn('이 페이지 분석하기', response.text)
        self.assertIn('name="fresh" value="1"', response.text)
        self.assertIn('다른 공개 출처를 찾았습니다', response.text)
        self.assertIn(SOURCE, response.text)
        self.assertIn('동일 제품으로 확정된 자료가 아닙니다', response.text)

    def test_supplement_title_search_without_retrying_blocked_page(self):
        with patch.object(main, '_product_candidates', return_value=[]), patch.object(alt, 'search', return_value=alt.parse_sources(payload(), ORIGINAL)) as search, patch.object(main, '_read_check_url') as read:
            response = self.client.post('/check/alternatives', data={'url': ORIGINAL, 'product_title': 'FX24GNGRX'})
        read.assert_not_called()
        search.assert_called_once_with(ORIGINAL, 'FX24GNGRX')
        self.assertEqual(200, response.status_code)

    def test_markup_is_escaped_in_sandbox(self):
        result = alt.parse_sources(payload(), ORIGINAL)
        result['suggestions'] = '<script>alert(1)</script>'
        result['sources'][0]['title'] = '<img src=x onerror=alert(1)>'
        with patch.object(main, '_product_candidates', return_value=[]), patch.object(alt, 'search', return_value=result):
            response = self.client.post('/check/alternatives', data={'url': ORIGINAL, 'product_title': 'FX24GNGRX'})
        self.assertNotIn('<script>alert(1)</script>', response.text)
        self.assertNotIn('<img src=x onerror=alert(1)>', response.text)
        self.assertIn('sandbox="allow-popups allow-popups-to-escape-sandbox"', response.text)

    def test_search_error_keeps_upload_and_review_routes(self):
        with patch.object(main, '_product_candidates', return_value=[]), patch.object(alt, 'search', side_effect=RuntimeError('secret')):
            response = self.client.post('/check/alternatives', data={'url': ORIGINAL, 'product_title': 'FX24GNGRX'})
        self.assertNotIn('secret', response.text)
        self.assertIn('사진·PDF로 계속하기', response.text)
        self.assertIn('관리자에게 링크 확인 요청', response.text)


if __name__ == '__main__':
    unittest.main()
