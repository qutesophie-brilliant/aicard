import unittest
from unittest.mock import patch

from app.pipeline import extract, page_recovery
from app.pipeline.product_page import product_body, product_text, ProductBodyUnavailable


class KoreanDetailRecoveryTests(unittest.TestCase):
    def test_intelligent_assistant_is_a_claim_not_proof_of_machine_learning(self):
        from app.pipeline.run import quick_check
        text = '지능형 어시스턴트\n*앱 내 실시간 채팅 기능은 추후 OTA 업데이트로 제공됩니다. 이 기능은 영어 대화만 지원합니다.'
        card, _ = quick_check('로봇청소기', 'https://example.com/product', text)
        self.assertEqual(['지능형 어시스턴트'], [c['text'] for c in card['claims']])
        self.assertTrue(card['quick_has_intelligent_wording'])
        negative, _ = quick_check('기기', 'https://example.com/product', '음성 어시스턴트 기능으로 명령할 수 있습니다.')
        self.assertEqual([], negative['claims'])

    def test_linked_intelligent_features_and_management_keep_conditions(self):
        html = '''<nav><a href="#intelligent-features">지능형 기능</a>
          <a href="#remote-management">원격 관리</a><a href="#compare">제품 비교</a></nav>
          <div id="intelligent-features"><h2>AI 프레이밍</h2>
            <p>AI가 발표자를 인식하여 화면을 조정합니다.</p>
            <p>발표자 모드는 별도의 업데이트가 필요합니다.</p></div>
          <div id="remote-management"><p>Wi-Fi 연결로 장치를 관리하고 업데이트할 수 있습니다.</p></div>
          <div id="compare">다른 카메라는 AI 음성 번역을 지원합니다.</div>
          <div class="reviews">AI 기능이 좋다는 구매 후기</div>'''
        body = product_body(html, 'https://new-seller.example/ko/products/camera')
        text = product_text(body)
        for phrase in ('발표자를 인식', '별도의 업데이트', 'Wi-Fi 연결'):
            self.assertIn(phrase, text)
        for phrase in ('AI 음성 번역', '구매 후기'):
            self.assertNotIn(phrase, text)
        # The public HTML already supplies evidence: no rendering or AI selector needed.
        with patch.object(page_recovery.page_selector, 'recover') as selector:
            prepared, _, _, _ = page_recovery.acquire(html, 'https://new-seller.example/ko/products/camera', None)
            selector.assert_not_called()
            self.assertIn('별도의 업데이트', product_text(product_body(prepared, 'https://new-seller.example/ko/products/camera')))

    def test_ecovacs_campaign_keeps_product_copy_images_and_caveats(self):
        html = '''<div id="allcontent"><div id="section3"><h2>AIVI 기능</h2>
          <p>환경 데이터를 분석하고 장애물의 위치와 형태를 직접 식별합니다.</p>
          <img data-src="https://cdn.example/detail.jpg" src="/placeholder.jpg"></div>
          <div id="section6"><p>YIKO-GPT 지능형 어시스턴트</p>
          <p>앱 내 실시간 채팅은 추후 OTA 업데이트로 제공되며 영어 대화만 지원합니다.</p></div>
          <div class="reviews">사용자 리뷰 AI 최고</div></div>
          <footer><p>다른 제품 추천 AI 가습기</p></footer>'''
        url = 'https://www.ecovacs.com/kr/campaign/deebot-new-model'
        body = product_body(html, url)
        self.assertEqual('explicit_product_container', body.section['data-aicard-selection'])
        self.assertIn('영어 대화만', product_text(body))
        self.assertIn('장애물의 위치', product_text(body))
        self.assertEqual('https://cdn.example/detail.jpg', body.img['data-src'])
        self.assertNotIn('리뷰', product_text(body))
        self.assertNotIn('가습기', product_text(body))
        with self.assertRaises(ProductBodyUnavailable):
            product_body(html, 'https://unrelated.example/campaign/deebot-new-model')

    def test_intelligent_wording_reaches_model_alongside_literal_ai(self):
        quotes = ['AI가 주변 밝기를 분석하여 조명을 조절합니다.',
                  '지능형 온도 조절 기능이 실내 환경에 맞게 작동합니다.',
                  '알아서 실내 환경에 맞게 작동하는 기능입니다.',
                  '무료 배송과 할인 혜택을 제공합니다.']
        chunks = [dict(doc_id=1, chunk=i, quote=q, kind='sales') for i,q in enumerate(quotes)]
        with patch.object(extract.llm, 'available', return_value=True), \
             patch.object(extract.llm, 'ask_json', return_value=[]) as ask, \
             patch.object(extract.config, 'EXTRACT_PASSES', 1), \
             patch.object(extract.config, 'EXTRACT_VERIFY', False):
            extract.extract_claims(chunks)
        prompt = ask.call_args.args[0].split('문장들:\n')[-1]
        for q in quotes[:3]:
            self.assertIn(q, prompt)
        self.assertNotIn(quotes[3], prompt)


if __name__ == '__main__':
    unittest.main()
