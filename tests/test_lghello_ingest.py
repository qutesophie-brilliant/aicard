import io
import unittest
from unittest.mock import patch

import httpx
from PIL import Image
from app.pipeline import ingest, image_tiles, product_images
from app.pipeline.ocr import OcrResult
from app.pipeline.product_page import product_body, ProductBodyUnavailable

PAGE = 'https://rental.lghellovision.net/product/detail?seq=3945'
HTML = '''<title>AI 김치냉장고</title><div class="tab-box__detail-review">
<section class="panel-area__detail-info"><div class="banner-wrap">최저가 할인</div>
<div class="detail-img">&lt;p&gt;&lt;img src="/uploads/editor/detail.png"&gt;&lt;/p&gt;</div>
<div class="detail-button-wrap"><img src="/arrow.png">더보기</div></section>
<section class="panel-area__review">AI 리뷰 요약: 아주 좋습니다.</section></div>
<div class="recommended">AI 추천상품</div>'''


class LgHelloTests(unittest.TestCase):
    def test_temperature_sentence_wrap_preserves_complete_claim_and_footnote_boundary(self):
        from app.pipeline.quick_text import chunks
        prefix = 'AI 정온 모드는 냉장고 사용 패턴에 따른 맞춤운전으로'
        result = chunks(prefix + '\n온도 상승 폭을 최소화합니다.\n* 앱 연동이 필요합니다.')
        self.assertEqual(prefix + ' 온도 상승 폭을 최소화합니다.', result[0])
        self.assertEqual('* 앱 연동이 필요합니다.', result[1])
        self.assertEqual([prefix, '* 별도의 시험 조건입니다.'],
                         chunks(prefix + '\n* 별도의 시험 조건입니다.'))

    def test_quick_ocr_does_not_merge_container_trial_into_ai_temperature(self):
        from app.pipeline.run import quick_check
        raw = ('* 시험방법 : 밀폐 챔버에서 김치통의 냄새 성분 농도를 측정\n'
               '- 김치통 정보 (제조자 / 판매자) : 뉴서광 / 삼성전자\n'
               '사용 패턴에 맞춰 적정 온도를 유지하는 AI 정온 모드\n'
               'AI 정온 모드는 사용 패턴에 따른 맞춤운전으로 온도 상승 폭을 최소화합니다.\n'
               '* AI 정온 기능을 사용하려면 앱 연동과 무선 네트워크 연결이 필요합니다.\n'
               '상칸 김치보관 모드 냉장 모드 OFF 모드\n* 각 칸별 모드는 on/off 가능합니다.')
        capture = {'coverage': {}, 'sections': [{'section_id': 0, 'feature_scope': '',
            'text': raw.replace('\n', ' '), 'chunks': [raw.replace('\n', ' ')],
            'diagram_text': raw, 'source_type': 'image_ocr', 'source_url': PAGE,
            'snapshot_sha256': 'test'}]}
        card, docs = quick_check('냉장고', PAGE, '', source_capture=capture)
        self.assertTrue(card['claims'])
        self.assertFalse(card['items']['optout']['evidence'])
        for doc in docs.values():
            self.assertFalse(any('김치통' in q and 'AI 정온' in q for q in doc['chunks']))
        for claim in card['claims']:
            self.assertFalse(any('밀폐 챔버' in e['quote'] for e in claim['evidence']))

    def test_escaped_detail_inside_review_wrapper_preserves_only_product(self):
        body = product_body(HTML, PAGE)
        self.assertEqual(['/uploads/editor/detail.png'], [im['src'] for im in body.select('img')])
        for noise in ('할인', '리뷰', '더보기', '추천상품'):
            self.assertNotIn(noise, str(body))
        with self.assertRaises(ProductBodyUnavailable):
            product_body(HTML, 'https://rental.lghellovision.net.evil.test/product/detail')

    def test_decoded_editor_is_sanitized_and_missing_editor_is_inconclusive(self):
        html = HTML.replace('&lt;p&gt;&lt;img src="/uploads/editor/detail.png"&gt;&lt;/p&gt;',
            '&lt;p&gt;AI가 보관 환경을 분석하여 온도를 조절합니다. 사용 조건을 확인하세요.&lt;/p&gt;'
            '&lt;script&gt;AI 가짜 설명&lt;/script&gt;')
        body = product_body(html, PAGE)
        self.assertIn('보관 환경', body.get_text())
        self.assertNotIn('가짜', body.get_text())
        with self.assertRaises(ProductBodyUnavailable):
            product_body('<title>AI 제품</title><div>AI 리뷰 요약</div>', PAGE)

    def test_image_ocr_retains_original_image_url_and_excludes_other_sources(self):
        out = io.BytesIO()
        with Image.new('RGB', (50, 50), 'white') as im:
            im.save(out, format='PNG')
        source = 'https://rentalcdn.lghellovision.net/uploads/editor/detail.png'
        html = HTML.replace('/uploads/editor/detail.png', source)
        response = httpx.Response(200, text=html, request=httpx.Request('GET', PAGE))
        calls = []
        def download(url, **kwargs):
            calls.append(url)
            return httpx.Response(200, content=out.getvalue(), request=httpx.Request('GET', url))
        with patch.object(ingest, '_fetch', return_value=response), \
             patch('app.network_safety.fetch_public', side_effect=download), \
             patch('app.pipeline.product_ocr.read_images', return_value=[{
                 'text': 'AI가 보관 환경을 분석하여 온도를 조절합니다.', 'engine': 'test',
                 'status': 'read', 'attempts': 1}]):
            result = ingest.from_url(PAGE, product_only=True)
        self.assertEqual([source], calls)
        self.assertEqual(source, result.source_capture['images'][0]['url'])
        self.assertTrue(any(s['source_url'] == source for s in result.source_capture['sections']))

    def test_actual_retailer_image_dimensions_keep_first_and_last_rows(self):
        out = io.BytesIO()
        with Image.new('RGB', (850, 32890), 'white') as im:
            im.putpixel((0, 0), (255, 0, 0))
            im.putpixel((0, 32889), (0, 0, 255))
            im.save(out, format='PNG')
        tiles = image_tiles.split_image(out.getvalue())
        self.assertEqual(23, len(tiles))
        with Image.open(io.BytesIO(tiles[0])) as first:
            self.assertEqual((255, 0, 0), first.getpixel((0, 0)))
        with Image.open(io.BytesIO(tiles[-1])) as last:
            self.assertEqual(850, last.width)
            self.assertEqual((0, 0, 255), last.getpixel((0, last.height - 1)))

    def test_excessive_source_resolution_rejected_before_pixel_decode(self):
        from unittest.mock import MagicMock
        im = MagicMock(width=9000, height=9000, size=(9000, 9000))
        with patch.object(Image, 'open', return_value=im):
            with self.assertRaises(ValueError):
                image_tiles.split_image(b'header')
        im.load.assert_not_called()
        im.convert.assert_not_called()


if __name__ == '__main__':
    unittest.main()
