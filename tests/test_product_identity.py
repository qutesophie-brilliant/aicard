import unittest
from unittest.mock import patch

from app import db, main


class ProductIdentityTests(unittest.TestCase):
    def test_naver_mobile_and_ad_tracking_reuse_same_product(self):
        for host in ('smartstore.naver.com', 'brand.naver.com'):
            sources = {1: [f'https://{host}/example/products/123456']}
            result = self.match(f'https://m.{host}/example/products/123456/?NaPm=ad123&utm_source=naver#detail', sources)
            self.assertEqual(1, result['id'])

    def test_naver_different_store_product_options_and_hosts_stay_separate(self):
        sources = {1: ['https://smartstore.naver.com/example/products/123456']}
        for url in (
            'https://smartstore.naver.com/other/products/123456',
            'https://smartstore.naver.com/example/products/123457',
            'https://smartstore.naver.com/example/products/123456?optionId=2',
            'https://brand.naver.com/example/products/123456',
            'https://m.smartstore.naver.com.evil.example/example/products/123456',
            'https://m.smartstore.naver.com:8443/example/products/123456',
        ):
            with self.subTest(url=url):
                self.assertIsNone(self.match(url, sources))

    def test_naver_rules_do_not_affect_other_paths_or_sites(self):
        from app.product_identity import source_identity
        for base in ('https://shop.example/item/123', 'https://smartstore.naver.com/example/category/123'):
            self.assertNotEqual(source_identity(base), source_identity(base + '?NaPm=value'))
        self.assertNotEqual(source_identity('https://smartstore.naver.com/example/category/123'),
                            source_identity('https://m.smartstore.naver.com/example/category/123'))

    def match(self, url, sources):
        products = [{'id': key, 'model': f'MODEL{key:04d}'} for key in sources]
        with patch.object(db, 'list_products', return_value=products), \
             patch.object(db, 'list_documents', side_effect=lambda key: [{'url': value} for value in sources[key]]):
            return main._match_published(url)

    def test_query_product_identity_is_preserved(self):
        sources = {1: ['https://shop.example/product?goodsNo=111'],
                   2: ['https://shop.example/product?goodsNo=222']}
        self.assertEqual(2, self.match('https://shop.example/product?goodsNo=222', sources)['id'])
        self.assertIsNone(self.match('https://shop.example/product?goodsNo=333', sources))

    def test_coupang_item_and_vendor_identity_are_not_tracking(self):
        sources = {1: ['https://www.coupang.com/vp/products/123?itemId=11&vendorItemId=21']}
        self.assertIsNone(self.match('https://www.coupang.com/vp/products/123?itemId=12&vendorItemId=22', sources))

    def test_tracking_query_order_fragment_and_https_do_not_hide_same_source(self):
        sources = {1: ['https://shop.example/product?goodsNo=111&item=2']}
        result = self.match('http://www.shop.example/product/?utm_source=google&item=2&goodsNo=111#detail', sources)
        self.assertEqual(1, result['id'])

    def test_model_in_an_unrelated_host_does_not_redirect(self):
        sources = {1: ['https://maker.example/product/MODEL0001']}
        self.assertIsNone(self.match('https://other.example/product/MODEL0001', sources))

    def test_model_prefix_is_not_an_exact_model(self):
        sources = {1: ['https://maker.example/product/MODEL0001']}
        self.assertIsNone(self.match('https://maker.example/product/MODEL0001PLUS', sources))

    def test_shared_manual_is_ambiguous_instead_of_first_product(self):
        sources = {1: ['https://maker.example/manual.pdf'], 2: ['https://maker.example/manual.pdf']}
        self.assertIsNone(self.match('https://maker.example/manual.pdf', sources))

    def test_path_and_identifier_values_remain_case_sensitive(self):
        sources = {1: ['https://shop.example/Product?code=AbC']}
        self.assertIsNone(self.match('https://shop.example/Product?code=abc', sources))
        self.assertIsNone(self.match('https://shop.example/product?code=AbC', sources))

    def test_generic_homepage_is_not_a_product_match(self):
        self.assertIsNone(self.match('https://maker.example/', {1: ['https://maker.example/']}))

    def test_search_and_ad_tracking_do_not_hide_product(self):
        sources = {1: ['https://shop.example/product?modelId=MODEL0001']}
        self.assertEqual(1, self.match('https://shop.example/product?modelId=MODEL0001&srsltid=google123&wbraid=ad&gbraid=ad2', sources)['id'])

    def test_unreserved_path_encoding_is_equivalent(self):
        sources = {1: ['https://shop.example/item/MODEL-0001']}
        self.assertEqual(1, self.match('https://shop.example/item/%4dODEL%2d0001/', sources)['id'])

    def test_encoded_separator_remains_distinct(self):
        sources = {1: ['https://shop.example/item/MODEL/0001']}
        self.assertIsNone(self.match('https://shop.example/item/MODEL%2F0001', sources))

    def test_index_and_model_suffixes_are_not_blindly_removed(self):
        sources = {1: ['https://shop.example/item/MODEL0001']}
        for tail in ('/index.html', '-index', '-PLUS', '?variant=2'):
            self.assertIsNone(self.match('https://shop.example/item/MODEL0001' + tail, sources))

