import unittest
from unittest.mock import Mock, patch

import httpx

from app.pipeline import ingest, extract
from app.pipeline.product_page import product_body, ProductBodyUnavailable


class ProductPageTests(unittest.TestCase):
    def test_costco_detail_image_excludes_gallery_and_recommendations(self):
        from app.pipeline.product_page import product_text, detail_selector
        from app.pipeline.product_images import capture_images
        from app.pipeline.ocr import OcrResult
        html = '''<div class="gallery"><img src="/gallery.jpg"></div>
        <div class="product-details-content-wrapper"><div class="txt_itemDes">
        <img src="/mediapermalink/693448-desc_1"></div></div>
        <section id="product_specs"><div class="product-classification-wrapper">
        <table><tr><td>품질보증기준</td><td>관련 소비자분쟁해결 기준에 따릅니다.</td></tr></table>
        </div></section><div class="recommended-products">
        <div class="product-details-content-wrapper">다른 상품 AI 설명<img src="/other.jpg"></div></div>'''
        url = 'https://www.costco.co.kr/ElectronicsComputers/Cameras/Model/p/693448'
        body = product_body(html, url)
        self.assertEqual(['/mediapermalink/693448-desc_1'], [i['src'] for i in body.select('img')])
        self.assertIn('소비자분쟁해결', product_text(body))
        self.assertNotIn('다른 상품', product_text(body))
        self.assertEqual('explicit_product_container', body.section['data-aicard-selection'])
        import io
        from PIL import Image
        image = io.BytesIO()
        Image.new('RGB', (900, 2200)).save(image, format='PNG')
        seen = []
        def download(src):
            seen.append(src)
            return image.getvalue()
        capture = {'sections': [], 'coverage': {}}
        capture_images(html, url, capture, fetch_bytes=download,
                       reader=lambda _: OcrResult(texts=['스마트 AI 감지가 사람과 반려동물을 감지합니다.'], engine='test'))
        self.assertEqual(['https://www.costco.co.kr/mediapermalink/693448-desc_1'], seen)
        self.assertIn('스마트 AI 감지', capture['sections'][0]['text'])
        for other in ('https://example.com/Model/p/693448', 'https://www.costco.co.kr/'):
            self.assertNotIn('.product-details-content-wrapper', detail_selector(other))

    def test_sony_feature_description_retains_ai_explanation_without_navigation(self):
        html = '''<div class="LargeStaticImageHotSpot__DescriptionContainer">
          <div class="LargeStaticImageHotSpot__ContentHeader"><h2>새로 개발된 AI 프로세싱 유닛</h2></div>
          <div>인체의 형상과 자세 정보를 활용하여 카메라의 피사체 인식 정확도를 개선합니다.</div></div>
          <div class="Highlights__KeySpec"><p>61.0MP 센서</p></div>
          <div class="PrimaryTout__TextBoxContainer">AI 기반 성능 자세히 보기</div>
          <div class="recommendations">다른 카메라 AI 줌 기능</div>'''
        text = product_body(html, 'https://www.sony.co.kr/interchangeable-lens-cameras/products/ilce-7rm5').get_text(' ',strip=True)
        self.assertIn('AI 프로세싱 유닛', text)
        self.assertIn('인체의 형상과 자세 정보', text)
        self.assertNotIn('자세히 보기', text)
        self.assertNotIn('AI 줌', text)

    def test_hp_custom_product_modules_keep_copy_without_generic_ai_or_variants(self):
        html = '''<div class="heroBanner"><c-hp-hero-banner class="c-hp-hero-banner">
          <div class="c-hp-hero-banner__description"><p>OmniBook Ultra는 AI 가속 기능을 제공하는 노트북입니다.</p></div>
          <a>다른 제품 구매</a></c-hp-hero-banner></div>
          <c-hp-ksp-carousel id="benefits" class="dropdown-menu-detached"><p>AI 도구로 창작과 협업을 지원합니다.</p></c-hp-ksp-carousel>
          <c-hp-bg-container id="faqs"><div class="dropdown-menu-detached"><p>배터리 사용 시간은 사용 환경에 따라 달라집니다.</p></div></c-hp-bg-container>
          <c-hp-bg-container id="compare"><p>다른 구성의 AI 프로세서 옵션</p></c-hp-bg-container>
          <c-hp-scroll-reveal><p>일반적인 AI PC 개념 설명</p></c-hp-scroll-reveal>
          <c-hp-footnotes><p>기능 제공 여부는 구성에 따라 다릅니다.</p></c-hp-footnotes>'''
        text = product_body(html, 'https://www.hp.com/kr-ko/laptops/home-laptops/omnibook/omnibook-ultra-ai-pc.html').get_text(' ', strip=True)
        for phrase in ('AI 가속 기능', 'AI 도구로 창작', '사용 환경', '구성에 따라 다릅니다'):
            self.assertIn(phrase, text)
        for phrase in ('다른 제품 구매', '다른 구성', '일반적인 AI PC'):
            self.assertNotIn(phrase, text)

    def test_single_product_group_uses_only_its_shared_description(self):
        import json
        text = 'AI-powered notifications identify people and pets in this indoor camera.'
        group = {'@type':'ProductGroup', 'name':'Indoor Camera', 'description':text,
                 'hasVariant':[{'@type':'Product', 'description':'Variant-only AI feature must not be merged.'}]}
        html = '<script type="application/ld+json">' + json.dumps(group) + '</script>'
        body = product_body(html, 'https://new-seller.example/products/camera')
        self.assertEqual('structured_product_description', body.section['data-aicard-selection'])
        self.assertEqual(text, body.get_text(' ', strip=True))
        self.assertNotIn('Variant-only', body.get_text())

    def test_product_group_without_shared_copy_or_multiple_groups_is_inconclusive(self):
        import json
        group = {'@type':'ProductGroup', 'hasVariant':[{'@type':'Product', 'description':
                 'AI-powered notifications identify people and pets in this indoor camera.'}]}
        other = {'@type':'ProductGroup', 'description':'Another camera has AI noise cancellation and automatic tracking.'}
        for value in (group, [group, other]):
            html = '<script type="application/ld+json">' + json.dumps(value) + '</script>'
            with self.assertRaises(ProductBodyUnavailable):
                product_body(html, 'https://new-seller.example/products/camera')

    def test_visible_single_product_copy_preserves_list_boundaries_without_render(self):
        import json
        from app.pipeline import page_recovery
        from app.pipeline.product_page import product_text
        lines = ['AI identifies people and pets and tracks their movement.',
                 'HomeKit supports up to 1080P resolution.',
                 'An SD card is sold separately.']
        html = '<script type="application/ld+json">' + json.dumps({
            '@type':'Product', 'description':' '.join(lines)}) + '</script>'
        html += '<div class="Layout_root__abc Layout_NavEmpty__abc"><main><div class="unknown-copy"><ul>' + ''.join('<li>'+v+'</li>' for v in lines) + '</ul></div>'
        html += '<div class="recommendations"><p>Another AI product is waterproof.</p></div><div class="navigation">AI menu</div></main></div>'
        body = product_body(html, 'https://new-seller.example/products/camera')
        self.assertEqual('visible_product_description', body.section['data-aicard-selection'])
        self.assertEqual(lines, product_text(body).splitlines())
        renderer = Mock()
        page_recovery.acquire(html, 'https://new-seller.example/products/camera', Mock(), renderer)
        renderer.assert_not_called()

    def test_metadata_copy_in_review_or_navigation_stays_summary_only(self):
        import json
        text = 'AI identifies people and pets and tracks their movement.'
        metadata = '<script type="application/ld+json">' + json.dumps({
            '@type':'Product', 'description':text}) + '</script>'
        for wrapper in ('<nav>{}</nav>', '<div class="reviews">{}</div>', '<div class="recommendations">{}</div>'):
            body = product_body(metadata + wrapper.format('<p>'+text+'</p>'), 'https://new-seller.example/p/1')
            self.assertEqual('structured_product_description', body.section['data-aicard-selection'])

    def test_visible_copy_does_not_select_multiple_products_or_neighbouring_text(self):
        import json
        text = 'AI identifies people and pets and tracks their movement.'
        for product in ([{'@type':'Product', 'description':text}, {'@type':'Product','description':text}],
                        {'@type':'Product', 'description':text}):
            html = '<script type="application/ld+json">' + json.dumps(product) + '</script>'
            html += '<div>' + text + ' Another product detects water leaks.</div>'
            if isinstance(product, list):
                with self.assertRaises(ProductBodyUnavailable):
                    product_body(html, 'https://new-seller.example/p/1')
            else:
                body = product_body(html, 'https://new-seller.example/p/1')
                self.assertEqual('structured_product_description', body.section['data-aicard-selection'])
                self.assertNotIn('water leaks', body.get_text())

    def test_feature_highlights_stops_before_comparison_and_reviews(self):
        html = '<section><h1>Feature Highlights</h1><div><h3>Smart Detection</h3><p>Detects a person or pet and follows movement within the camera view.</p></div></section>'
        html += '<section><h2>Comparison</h2><p>Another model has AI night vision.</p></section><div class="reviews">AI is perfect.</div>'
        body = product_body(html, 'https://new-seller.example/product/camera')
        self.assertIn('Detects a person', body.get_text())
        self.assertNotIn('Another model', body.get_text())
        self.assertNotIn('perfect', body.get_text())

    def _faq_html(self, *, title='Example X5 노트북', visible=True, other=False):
        import json
        name = 'Other Z9 노트북' if other else title
        questions = [name + '에서는 어떤 화면을 지원하나요?', name + '으로 어떤 작업을 할 수 있나요?']
        answers = ['이 제품은 선명한 화면과 풍부한 색상을 표현하는 고해상도 디스플레이를 제공합니다.',
                   '이 제품은 사무 작업과 가벼운 게임, 동영상 시청을 위한 다양한 기능을 제공합니다.']
        rows = [{'@type':'Question','name':q,'acceptedAnswer':{'@type':'Answer','text':a}}
                for q,a in zip(questions, answers)]
        rows.append({'@type':'Question','name':'AI PC란 무엇인가요?', 'acceptedAnswer':{'@type':'Answer','text':'AI PC는 인공지능 기능을 활용하는 다양한 종류의 컴퓨터를 일반적으로 말합니다.'}})
        html = '<h1>' + title + '</h1><script type="application/ld+json">' + json.dumps({'@type':'FAQPage','mainEntity':rows}) + '</script>'
        if visible:
            html += ''.join('<h3>' + row['name'] + '</h3><p>' + row['acceptedAnswer']['text'] + '</p>' for row in rows)
        return html

    def test_visible_model_specific_faq_is_partial_source_not_generic_ai(self):
        from app.pipeline import page_recovery
        html = self._faq_html()
        ing = ingest.from_product_html(html, 'https://example.com/laptops/x5')
        self.assertEqual(ing.source_capture['coverage']['product_selection'], 'structured_product_faq')
        self.assertIn('부분 결과', ing.notes[0])
        self.assertIn('디스플레이', ing.text)
        self.assertNotIn('AI PC는', ing.text)
        renderer = Mock(side_effect=ProductBodyUnavailable('timeout'))
        _, _, _, stages = page_recovery.acquire(html, 'https://example.com/laptops/x5', None, renderer)
        self.assertEqual(stages[0]['status'], 'partial')
        renderer.assert_called_once()

    def test_faq_does_not_use_metadata_only_other_models_or_generic_titles(self):
        for html in (self._faq_html(visible=False), self._faq_html(other=True), self._faq_html(title='고객 지원'),
                     self._faq_html() + '<h1>Other Z9 노트북</h1>'):
            with self.assertRaises(ProductBodyUnavailable):
                product_body(html, 'https://example.com/item')

    def test_product_intro_is_retained_alongside_details_without_reviews(self):
        import json
        from app.pipeline.source_capture import capture_product_html
        intro = 'AI가 탑재된 기술은 피부를 감지하고 면도 강도를 자동으로 조절합니다.'
        html = '<script type="application/ld+json">' + json.dumps({
            '@type': 'Product', 'description': intro,
            'review': {'reviewBody': 'AI 후기를 상품 설명으로 넣지 않습니다.'}}) + '</script>'
        html += '<p>' + intro + '</p><div id="product-features"><p>면도날은 분당 수백 번 회전하며 피부 가까이의 수염을 절삭합니다.</p><p>해당 면도날은 별도로 구매합니다.</p></div><div class="reviews">AI 후기</div>'
        capture = capture_product_html(html, 'https://unknown.example/products/shaver')
        self.assertIn(intro, capture['text'])
        self.assertIn('해당 면도날은 별도로 구매합니다.', capture['text'])
        self.assertNotIn('AI 후기', capture['text'])
        intro_sections = [s for s in capture['sections'] if intro in s['text']]
        self.assertEqual(len(intro_sections), 1)
        self.assertNotIn('별도로 구매', intro_sections[0]['text'])
        from app.pipeline.run import quick_check
        card, _ = quick_check('면도기', '', capture['text'], source_capture=capture)
        self.assertTrue(card['claims'])
        for claim in card['claims']:
            for evidence in claim.get('evidence', []):
                self.assertNotIn('면도날', evidence['quote'])

    def test_detail_intro_merge_does_not_duplicate_or_import_hidden_metadata(self):
        import json
        intro = 'AI가 피부 상태를 분석하여 사용자의 면도 강도를 자동으로 조절합니다.'
        metadata = '<script type="application/ld+json">' + json.dumps({
            '@type': 'Product', 'description': intro}) + '</script>'
        detail = '<div id="product-features"><p>회전식 면도날로 다양한 방향으로 자라는 수염을 정교하게 절삭합니다.</p></div>'
        for outside in ('', '<div class="reviews"><p>' + intro + '</p></div>'):
            self.assertNotIn(intro, product_body(metadata + detail + outside, 'https://example.com/item').get_text())
        body = product_body(metadata + '<div id="product-features"><p>' + intro + '</p></div>', 'https://example.com/item')
        self.assertEqual(body.get_text().count(intro), 1)

    def test_key_features_tab_retains_source_conditions_without_other_tabs(self):
        from app.pipeline.source_capture import capture_product_html
        from app.pipeline.run import quick_check
        for label in ('주요 기능', '주요 특징', 'Key features'):
            html = ('<a href="#features">' + label + '</a><div id="features">'
                    '<h2>AI를 통한 선명도</h2><p>AI 이미지 강화가 선명하고 자연스러운 이미지를 제공합니다.</p>'
                    '<p>이 기능은 전용 앱에서 활성화할 수 있습니다.</p></div>'
                    '<div id="certifications">인증된 다른 제품 AI 카메라</div>'
                    '<div class="reviews">AI 이미지 강화가 아주 좋아요.</div>')
            capture = capture_product_html(html, 'https://unknown.example/webcam')
            card, _ = quick_check('웹캠', '', capture['text'], source_capture=capture)
            self.assertTrue(any('AI 이미지 강화' in c['text'] for c in card['claims']))
            self.assertIn('전용 앱', capture['text'])
            self.assertNotIn('다른 제품', capture['text'])
            self.assertNotIn('좋아요', capture['text'])

    def test_image_enhancement_preview_keeps_formal_extraction_and_noise_filters(self):
        quote = 'AI 이미지 강화가 선명하고 자연스러운 이미지를 제공합니다.'
        rows = [{'doc_id': 1, 'chunk': 0, 'quote': quote}]
        self.assertEqual(extract._rules_extract(rows, 14), [])
        self.assertEqual(extract._rules_extract(rows, 14, complete_quotes=True)[0]['text'], quote)
        for noise in ('이미지 강화가 선명하고 자연스러운 이미지를 제공합니다.',
                      'AI 이미지 강화가 마음에 드는 고객 후기입니다.',
                      'AI 이미지 강화 제품을 할인된 가격으로 구매하세요.'):
            self.assertEqual(extract._rules_extract([{'doc_id': 1, 'chunk': 0, 'quote': noise}], 14, complete_quotes=True), [])

    def test_cafe24_lazy_image_body_without_text(self):
        html = '''<nav>AI 추천</nav><div id="prdDetail"><img ec-data-src="/detail.jpg" src="data:image/gif;base64,AA"></div>
        <div id="prdReview">다른 제품 리뷰</div><div class="relatedProducts"><img src="/other.jpg"></div>'''
        from app.pipeline.product_page import image_source
        body = product_body(html, 'https://hellovelo.co.kr/product/1')
        self.assertEqual(['/detail.jpg'], [image_source(im) for im in body.select('img')])
        self.assertNotIn('다른 제품', str(body))

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

    def test_quick_inline_footnote_is_not_an_advertising_claim(self):
        from app.pipeline.run import quick_check
        text='AI가 출수 패턴을 학습하여 용량을 제안합니다.\n[AI 맞춤출수] * AI 기능은 사용 패턴을 학습하여 온도와 용량을 추천합니다'
        card, docs=quick_check('제품 A','',text)
        self.assertFalse(any(c['text'].startswith('[AI') for c in card['claims']))
        self.assertIn('[AI 맞춤출수]',docs[1]['text'])


    def test_tapo_overview_is_not_discarded_as_related_products(self):
        from app.pipeline.product_page import product_text, detail_selector
        from app.pipeline.run import quick_check
        html = '''<nav>다른 카메라의 AI 기능</nav><article class="product-info">
        <div id="tapo-product-overview" class="product-related-container">
        <div class="product-images"><img src="/gallery.jpg"></div>
        <div class="product-highlights"><h1>Tapo C113</h1>
        <ul><li>스마트 AI 기술이 사람과 움직임을 감지하여 알림을 전달합니다.</li></ul>
        <div class="buy-now">다른 AI 상품 구입</div></div>
        <div class="product-overview-blocks"><img src="/detail.jpg"><div class="recommendations">AI 추천 상품</div><div class="reviews">AI 후기</div></div>
        <div class="hightlight-note"><p>클라우드 스토리지를 이용하려면 Tapo Care를 구독해야 합니다.</p></div>
        </div></article>'''
        url = 'https://tapo.com/kr/product/tapo-c113-security-camera/'
        body = product_body(html, url)
        text = product_text(body)
        self.assertIn('스마트 AI 기술이 사람과 움직임을 감지', text)
        self.assertIn('구독해야 합니다', text)
        self.assertEqual(['/detail.jpg'], [i['src'] for i in body.select('img')])
        for noise in ('다른 카메라', '다른 AI 상품', 'AI 추천', 'AI 후기'):
            self.assertNotIn(noise, text)
        card, _ = quick_check('Tapo C113', url, text)
        self.assertTrue(card['claims'])
        for unrelated in ('https://example.com/kr/product/a/', 'https://tapo.com/kr/'):
            self.assertNotIn('#tapo-product-overview', detail_selector(unrelated))
            with self.assertRaises(ProductBodyUnavailable):
                product_body(html, unrelated)

    def test_logitech_aem_reads_feature_accordion_without_education_banner(self):
        from app.pipeline.product_page import product_text
        from app.pipeline.run import quick_check
        html = '''<body class="productDisplayPage"><main id="maincontent">
        <section class="banner"><p>하이브리드 학습 솔루션이 학생을 도와줍니다.</p></section>
        <div class="section-container-wrapper"><div class="cmp-accordion__panel--hidden">
        <div class="body-copy"><p>Scribe에 내장된 AI는 투명 효과로 가려진 화이트보드를 보여줍니다.</p></div></div></div>
        <div class="specs-group"><p>USB 연결이 필요합니다.</p></div>
        <div id="footnote"><p>호환 업데이트를 확인해 주세요.</p></div>
        <div class="recommended-products"><div class="section-container-wrapper"><div class="body-copy">다른 제품의 AI 추천</div></div></div>
        </main></body>'''
        url = 'https://futureisnow.logitech.com/ko-kr/products/video-conferencing/room-solutions/scribe.html'
        text = product_text(product_body(html, url))
        self.assertIn('내장된 AI는 투명 효과', text)
        self.assertIn('USB 연결', text)
        self.assertIn('호환 업데이트', text)
        self.assertNotIn('학습 솔루션', text)
        self.assertNotIn('다른 제품', text)
        card, _ = quick_check('Scribe', url, text)
        self.assertTrue(any('투명 효과' in c['text'] for c in card['claims']))
        from app.pipeline.extract import _rules_extract
        rows = [{'doc_id': 1, 'chunk': 0, 'quote': 'Scribe에 내장된 AI는 투명 효과로 가려진 화이트보드를 보여줍니다.'}]
        self.assertEqual([], _rules_extract(rows, 14))
        for noise in ('AI 영상 공유 카메라 할인 쿠폰', '화이트보드 내용을 보여줍니다.'):
            self.assertEqual([], _rules_extract([dict(rows[0], quote=noise)], 14, complete_quotes=True))
        with self.assertRaises(ProductBodyUnavailable):
            product_body(html, 'https://example.com/ko-kr/products/a')

    def test_overview_cms_keeps_feature_sections_instead_of_short_metadata(self):
        from app.pipeline.product_page import product_text
        html = '''<script type="application/ld+json">{"@type":"Product","description":"A lightweight laptop with a high resolution display and processor."}</script>
        <nav>다른 제품의 AI 기능을 추천합니다.</nav>
        <div id="overviewContentInsertWrapper"><div id="CMD">
        <section class="wd__section"><h2>AI 경험</h2><p>AI가 영상의 배경 소음을 분석하여 음성을 선명하게 전달합니다.</p><img src="/detail.jpg"></section>
        <section class="wd__section"><h2>사용 조건</h2><p>일부 AI 기능은 인터넷 연결과 계정 등록이 필요합니다.</p></section>
        <div class="reviews">AI 후기로 평가한 다른 제품입니다.</div>
        </div></div><div class="recommendations">AI 추천 상품입니다.</div>'''
        body = product_body(html, 'https://example.com/laptops/model-a/')
        text = product_text(body)
        self.assertIn('배경 소음을 분석', text)
        self.assertIn('인터넷 연결과 계정 등록', text)
        self.assertEqual('/detail.jpg', body.img['src'])
        self.assertNotIn('lightweight', text)
        self.assertNotIn('후기로', text)
        self.assertNotIn('추천', text)
        self.assertEqual('explicit_product_container', body.section['data-aicard-selection'])
        # A generic CMD container outside the product-overview CMS is not enough.
        unrelated = html.replace('overviewContentInsertWrapper', 'generic-container')
        self.assertEqual('structured_product_description', product_body(unrelated, 'https://example.com/').section['data-aicard-selection'])


if __name__ == "__main__":
    unittest.main()
