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

    def test_lg_product_copy_excludes_scene_descriptions_and_mobile_duplicate(self):
        html = '''<section class="wcms-usp-scope pc">새로운 제품을 만나 보세요.</section>
        <section class="wcms-usp-scope pc"><h2>AI 맞춤출수</h2>
        <p>사용자의 출수 패턴을 학습해 자주 사용하는 온도와 용량을 제안합니다.</p>
        <div class="bullet-list">* AI 기능 사용에는 Wi-Fi 연결과 앱 등록이 필요합니다.</div>
        <article class="cap-section"><div title="동영상 해설">이 동영상은 AI 스피커가 우주를 비행하는 장면입니다.</div></article>
        <div class="visual-area"><div class="hidden">그림 속에 있는 AI 아이콘입니다.</div></div>
        <div class="pdp-product-table-wrap">다른 제품 비교: AI 공기청정기 114㎡ 모델의 추천 기능입니다.</div>
        </section><section class="wcms-usp-scope mo">모바일 전용 복제 문구</section>'''
        text = product_body(html, "https://www.lge.co.kr/water-purifiers/model").get_text()
        self.assertIn("출수 패턴을 학습", text)
        self.assertIn("Wi-Fi 연결", text)
        for junk in ("우주를 비행", "모바일 전용", "새로운 제품을 만나", "AI 아이콘", "다른 제품 비교"):
            self.assertNotIn(junk, text)

    def test_quick_rights_match_topics_not_shared_words(self):
        from app.pipeline.run import quick_check
        text = '''AI 맞춤출수는 사용 패턴을 학습해 온도를 제안합니다.
        설치 후 제품 불량 발생 시 제조사에서 A/S 처리합니다.
        시험 데이터는 얼음의 저장량을 측정한 값입니다.
        외부에서 원격제어 사용 시 데이터 사용 환경이 필요합니다.
        닫기 레시피 모드 종류를 선택해 주세요.
        UV LED 살균 기능으로 시험균주 감소 정도를 비교합니다.'''
        card, _ = quick_check("제품 A", "", text)
        self.assertFalse(card["items"]["optout"]["evidence"])
        self.assertFalse(card["items"]["safety"]["evidence"])
        self.assertTrue(card["items"]["remedy"]["evidence"])
        for ev in card["items"]["data"]["evidence"]:
            self.assertNotIn("A/S", ev["quote"])
            self.assertNotIn("저장량", ev["quote"])
            self.assertNotIn("데이터 사용 환경", ev["quote"])

    def test_quick_rights_keeps_explicit_privacy_and_optout(self):
        from app.pipeline.run import quick_check
        text = '''AI 카메라가 장애물을 인식합니다.
        영상 데이터는 기기 내부에서 처리하며 클라우드로 전송하지 않습니다.
        AI 인식 기능은 앱 설정에서 비활성화할 수 있습니다.
        소프트웨어 보안 업데이트는 5년 동안 지원합니다.'''
        card, _ = quick_check("제품 A", "", text)
        for key in ("data", "optout", "safety"):
            self.assertTrue(card["items"][key]["evidence"])

    def test_inline_layout_wraps_do_not_fragment_product_sentences(self):
        html = '''<title>제품 A</title><div class="product-description"><p>AI가 사용자의 <span>출수 패턴을</span><br>학습하여 온도를 제안합니다.</p><button>닫기</button><p>* Wi-Fi 연결이 필요합니다.</p></div>'''
        response = httpx.Response(200, text=html, request=httpx.Request("GET", "https://example.com/item"))
        with patch.object(ingest, "_fetch", return_value=response):
            result = ingest.from_url("https://example.com/item", ocr_images=False, product_only=True)
        self.assertIn("AI가 사용자의 출수 패턴을 학습하여 온도를 제안합니다.", result.text)
        self.assertNotIn("닫기", result.text)

    def test_spec_cell_delivery_line_does_not_erase_ai_copy(self):
        from app.pipeline.product_page import product_text
        from app.pipeline.run import split_fine_print, quick_check
        html = '<div class="spec-sum"><div class="spec"><td class="desc_info">인공지능으로 완성되는 AI 퀀텀 사운드<br>※ 배송 비용은 설치 지역에 따라 달라집니다.</td></div></div>'
        text = product_text(product_body(html, 'https://www.navimro.com/g/1/'))
        sales, fine = split_fine_print(text)
        card, _ = quick_check('제품 A', '', sales, fine)
        self.assertEqual(['인공지능으로 완성되는 AI 퀀텀 사운드'], [c['text'] for c in card['claims']])

    def test_quick_preserves_full_sentence_and_ignores_adjacent_non_ai_feature(self):
        from app.pipeline.run import quick_check
        text = 'AI가 화면의 주요 콘텐츠를 인식해, 설정을 변경할 필요 없이 화질을 자동으로 조정합니다.'
        card, _ = quick_check('제품 A', '', text + '\n용기 위치를 자동으로 감지하여 화구를 선택합니다.')
        self.assertEqual([text], [c['text'] for c in card['claims']])

    def test_quick_keeps_long_complete_quote(self):
        from app.pipeline.run import quick_check
        text = 'AI가 사용자의 출수 패턴을 학습하여 ' + '온도와 용량을 확인하고 ' * 10 + '설정을 제안합니다.'
        self.assertGreater(len(text), 160)
        card, _ = quick_check('제품 A', '', text)
        self.assertEqual(text, card['claims'][0]['text'])

    def test_html_source_line_wrap_is_not_sentence_boundary(self):
        from app.pipeline.product_page import product_text
        html='<div class="product-description"><p>AI가 주요 장면을 분석해\n    화질을 조절합니다.</p><p>Wi-Fi 연결이 필요합니다.</p></div>'
        text=product_text(product_body(html,'https://example.com/product/1'))
        self.assertIn('AI가 주요 장면을 분석해 화질을 조절합니다.',text)
        self.assertIn('\nWi-Fi 연결',text)

    def test_quick_product_title_is_not_a_second_claim(self):
        from app.pipeline.run import quick_check
        card, _=quick_check('LG 퓨리케어 AI 냉동얼음정수기','','LG 퓨리케어 AI 냉동얼음정수기\nAI가 출수 패턴을 학습하여 용량을 제안합니다.')
        self.assertEqual(['AI가 출수 패턴을 학습하여 용량을 제안합니다.'],[c['text'] for c in card['claims']])


if __name__ == "__main__":
    unittest.main()
