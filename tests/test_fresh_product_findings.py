import json
import unittest

from app.pipeline import ingest, quick_coverage
from app.pipeline.retrieve import ChunkIndex
from app.pipeline.run import quick_check


class FreshProductFindings(unittest.TestCase):
    def test_product_overview_tab_keeps_real_features_excludes_other_products(self):
        html = '''<title>Security Camera</title><div class="template-product">
        <div class="overview-tab"><h2>Advanced AI Detection with Instant Alerts</h2>
        <p>AI distinguishes between people, vehicles, animals, and generic motion.</p>
        <div class="reviews">AI customer review text</div></div>
        <div class="related">Other product AI recognizes faces.</div></div>'''
        result = ingest.from_product_html(html, 'https://seller.example/camera')
        card, _ = quick_check('Security Camera', '', result.text, source_capture=result.source_capture)
        self.assertTrue(any('distinguishes between people' in c['text'] for c in card['claims']))
        self.assertNotIn('customer review', result.text)
        self.assertNotIn('Other product', result.text)

    def test_unscoped_overview_is_not_product_description(self):
        from app.pipeline.product_page import ProductBodyUnavailable
        with self.assertRaises(ProductBodyUnavailable):
            ingest.from_product_html('<div class="overview-tab">AI distinguishes people and vehicles. This is a website overview.</div>', 'https://seller.example/')

    def test_structured_summary_explicitly_identified_as_partial(self):
        html = '<script type="application/ld+json">' + json.dumps({
            '@type': 'Product', 'description': '2K Pre-recording Bullet Camera with a Long Lasting Battery & Smart Detection'}) + '</script>'
        result = ingest.from_product_html(html, 'https://seller.example/camera')
        self.assertIn('요약만', result.notes[0])
        self.assertNotIn('세부정보를 확인했습니다', result.notes[0])

    def test_retailer_shared_contacts_do_not_mix_manufacturers(self):
        docs = [{'id': 1, 'kind': '판매페이지', 'chunks': [
            '캐논 A/S센터 1533-3355',
            'DJI 공식 기술지원 A/S센터 080-822-1456',
            '제품 초기 불량시 서비스센터에서 확인합니다.']}]
        index = ChunkIndex(docs)
        canon = quick_coverage.fill(index, '캐논 EOS R6 Mark II')['remedy']['evidence']
        self.assertTrue(any('캐논' in e['quote'] for e in canon))
        self.assertFalse(any('DJI' in e['quote'] for e in canon))
        dji = quick_coverage.fill(index, 'DJI camera')['remedy']['evidence']
        self.assertTrue(any('DJI' in e['quote'] for e in dji))
        self.assertFalse(any('캐논' in e['quote'] for e in dji))

    def test_english_warranty_is_preserved_without_inventing_ai_functions(self):
        text = 'CPU Intel Core Ultra 7 processor with Intel AI Boost (NPU)\nWarranty 1 year limited warranty'
        card, _ = quick_check('Prestige', '', text)
        self.assertEqual([], card['claims'])
        self.assertTrue(any('1 year limited warranty' in e['quote'] for e in card['items']['remedy']['evidence']))


if __name__ == '__main__':
    unittest.main()
