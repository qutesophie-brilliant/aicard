import unittest
from unittest.mock import patch

import httpx

from app.pipeline import ingest, extract
from app.pipeline.product_page import product_body, ProductBodyUnavailable


class ProductPageTests(unittest.TestCase):
    def test_menu_recommendations_excluded_conditions_retained(self):
        html = '''<title>제품 A</title><div class="lnb-main">모터/감속기 계장/자동제어 추천상품</div>
        <div class="product-description"><h2>AI가 소리를 분석하여 음질을 조절합니다.</h2>
        <p>※ 음질 조절 기능은 일부 입력에서는 적용되지 않습니다.</p>
        <p>제품 보증 기간은 1년이며 제조사의 보증 조건을 확인해야 합니다.</p>
        <div class="relatedProducts">AI가 공간을 분석하는 다른 제품</div>
        <div class="recommendation">AI가 먼지를 감지하는 추천상품</div></div>
        <footer>개인정보 처리방침 고객센터</footer>'''
        text = product_body(html, "https://example.com/item/1").get_text("\n")
        self.assertIn("AI가 소리를 분석", text)
        self.assertIn("적용되지 않습니다", text)
        self.assertIn("보증 기간은 1년", text)
        for junk in ("추천상품", "다른 제품", "개인정보", "자동제어"):
            self.assertNotIn(junk, text)

    def test_preserve_product_recommendation_feature(self):
        html = '<div itemprop="description">AI가 시청 이력을 분석하여 콘텐츠를 추천해 줍니다. 추천 기능을 이용하려면 계정 설정이 필요합니다.</div>'
        text = product_body(html, "https://example.com/item/2").get_text()
        self.assertIn("콘텐츠를 추천", text)

    def test_empty_dynamic_description_is_inconclusive(self):
        html = '<title>AI 청소기</title><nav>AI 추천상품</nav><div id="detailIfrm"><iframe src="about:blank"></iframe></div>'
        with self.assertRaises(ProductBodyUnavailable):
            product_body(html, "https://www.11st.co.kr/products/1")

    def test_sitewide_ai_banner_cannot_be_product_fallback(self):
        html = '<main>AI가 유사상품을 자동으로 모아 가격비교 서비스를 제공합니다.</main>'
        with self.assertRaises(ProductBodyUnavailable):
            product_body(html, "https://unknown.example/item")

    def test_navimro_adapter_excludes_sibling_recommendation_rail(self):
        html = '''<div class="spec-sum"><div class="spec"><table><tr><td class="desc_info">인공지능으로 완성되는 AI 퀀텀 사운드.
        ※ 벽걸이 상품은 브라켓이 포함된 금액입니다. 설치 조건을 확인하세요.</td></tr></table></div>
        <div class="spec-right">자동제어 추천상품 AI가 학습하는 냉장고</div></div>'''
        text = product_body(html, "https://www.navimro.com/g/1/").get_text()
        self.assertIn("AI 퀀텀 사운드", text)
        self.assertNotIn("냉장고", text)

    def test_meta_and_title_not_added_to_product_body(self):
        html = '''<title>AI 제품 A</title><meta name="description" content="AI 추천상품 자동제어">
        <div class="product-description">AI가 음성을 분석하여 발화자를 구분합니다. 계정을 등록한 뒤 기능을 이용할 수 있습니다.</div>'''
        response = httpx.Response(200, text=html, request=httpx.Request("GET", "https://example.com/item"))
        with patch.object(ingest, "_fetch", return_value=response):
            result = ingest.from_url("https://example.com/item", ocr_images=False, product_only=True)
        self.assertEqual("AI 제품 A", result.title)
        self.assertNotIn("추천상품", result.text)
        self.assertTrue(result.notes)

    def test_plain_text_menu_fragments_not_claims(self):
        lines = ["모터/감속기 계장/자동제어 추천상품", "청소용품 위생용품 와이퍼/보루 추천상품",
                 "인공지능으로 완성되는 AI 퀀텀 사운드"]
        chunks = [{"doc_id": 1, "chunk": i, "quote": q, "kind": "판매페이지"} for i, q in enumerate(lines)]
        claims = extract._rules_extract(chunks, 14)
        self.assertEqual([lines[-1]], [c["text"] for c in claims])

    def test_quick_repeated_slogan_not_additional_evidence(self):
        from app.pipeline.run import quick_check
        card, _ = quick_check("제품 A", "", "인공지능으로 완성되는 AI 퀀텀 사운드\n번인 걱정 없는 퀀텀 디스플레이 4K")
        self.assertEqual(1, len(card["claims"]))
        self.assertEqual([], card["claims"][0]["evidence"])


if __name__ == "__main__":
    unittest.main()
