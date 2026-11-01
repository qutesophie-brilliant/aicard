import unittest
from app.pipeline.run import quick_check,_dedupe_quick_evidence
from app.pipeline import quick_coverage,quick_text


class QuickUploadFlowTests(unittest.TestCase):
    def test_off_in_image_geometry_is_not_ai_disable(self):
        text = 'Our advanced AI optimizes your whiteboard content, keeping it in focus and even squaring it off for maximum visibility.'
        self.assertFalse(quick_coverage._matches('optout', text))
        self.assertFalse(quick_coverage._matches('optout', 'AI tracking is available with 20% off this week.'))
        for text in ('You can turn off AI tracking in the app.',
                     'AI tracking can be switched off in settings.',
                     'You can disable AI tracking.', 'AI tracking: OFF',
                     'AI 기능을 설정에서 비활성화할 수 있습니다.'):
            self.assertTrue(quick_coverage._matches('optout', text), text)

    def test_pdf_soft_line_wrap_keeps_full_sentence_and_numeric_condition(self):
        text='인공지능 세탁은 세탁물 무게와 오염도를 감지해\n세탁 시간을 조절합니다.'
        condition='* 인공지능 세탁은 세탁량 6kg 이하에서 동작합니다.'
        card,_=quick_check('기능 점검용 세탁기','',text,condition)
        self.assertEqual(card['claims'][0]['text'],text.replace('\n',' '))
        self.assertEqual('근거 확인',card['items']['conditions']['status'])
        self.assertTrue(any('6kg' in e['quote'] for e in card['items']['conditions']['evidence']))

    def test_footnote_is_not_joined_to_connective_line(self):
        chunks=quick_text.chunks('AI가 오염도를 감지해\n* 해당 기능은 세탁량 6kg 이하에서 동작합니다.')
        self.assertEqual(2,len(chunks))
        self.assertTrue(chunks[1].startswith('*'))

    def test_lab_setting_is_not_promoted_to_usage_condition(self):
        self.assertFalse(quick_coverage._matches('conditions','* 시험 부하: AI 세탁은 6kg 이하에서 동작합니다.'))
        self.assertFalse(quick_coverage._matches('conditions','재고는 최대 6개 이하에서 주문할 수 있습니다.'))
        self.assertTrue(quick_coverage._matches('conditions','인공지능 세탁은 세탁량 6kg 이하에서 동작합니다.'))

    def test_repeated_award_index_word_is_not_an_operating_condition(self):
        text = ('2023년 대한민국 소비자 만족지수 1위 2022년 대한민국 히트상품 대상 '
                '소비자 만족지수 1위 히트상품 대상 대한민국 브랜드 선호도 2년 연속 1위 '
                '헤르젠 쌀냉장고 AI Care 고객 리뷰 후기 실제 구매자의 리뷰를 확인해보세요.')
        self.assertFalse(quick_coverage._matches('conditions', text))
        card, _ = quick_check('조건 분류 점검', '', text, '')
        self.assertFalse(card['items']['conditions']['evidence'])
        self.assertTrue(quick_coverage._matches(
            'conditions', 'AI 인식은 어두운 환경에서 성능이 저하될 수 있습니다.'))

    def test_same_page_nested_intro_is_shown_once(self):
        docs=[{'id':1,'url':'https://example.test/product'},{'id':2,'url':'https://example.test/product'}]
        short={'doc_id':1,'quote':'손빨래의 정교함을 구현한 섬세한 6모션 세탁','kind':'판매페이지'}
        long={'doc_id':2,'quote':'세탁의 진화 '+short['quote']+' 강력한 물살','kind':'판매페이지'}
        self.assertEqual([long],_dedupe_quick_evidence([short,long],docs))

    def test_condition_excerpt_and_different_source_are_preserved(self):
        docs=[{'id':1,'url':'https://example.test/a'},{'id':2,'url':'https://example.test/b'}]
        condition={'doc_id':1,'quote':'인공지능 세탁은 세탁량 6kg 이하에서 동작합니다.','kind':'사양표'}
        broader={'doc_id':1,'quote':'사용 안내: '+condition['quote'],'kind':'사양표'}
        distinct={'doc_id':2,'quote':condition['quote'],'kind':'판매페이지'}
        self.assertEqual([condition,broader,distinct],_dedupe_quick_evidence([condition,broader,distinct],docs))


if __name__=='__main__':unittest.main()
