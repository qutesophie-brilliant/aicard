import re
import unittest

from app.pipeline.quick_diagrams import capture_uploads
from app.pipeline.run import quick_check

TITLE = '인공지능세탁 코스'
DESCRIPTION = '세탁물에 따라 세탁 시간, 6모션 조합\n그리고 헹굼 횟수까지 알아서 맞춤조절'
DIAGRAM = TITLE + '\n' + DESCRIPTION + '\n무게 감지\n의류 인식\n오염도 감지\n헹굼 추가/세탁시간 조절'
compact = lambda value: re.sub(r'\s+', '', value)


class QuickDiagramTests(unittest.TestCase):
    def check(self, *pages):
        capture = capture_uploads(list(pages), 'https://maker.example/product')
        return quick_check('도식 점검용 제품', 'https://maker.example/product', '', source_capture=capture)

    def test_original_diagram_connects_title_and_description(self):
        card, docs = self.check(DIAGRAM)
        title = next(c for c in card['claims'] if compact(c['text']) == compact(TITLE))
        self.assertTrue(title['evidence'])
        quote = next(e['quote'] for e in title['evidence'] if e.get('diagram'))
        self.assertIn(compact(DESCRIPTION), compact(quote))
        self.assertIn(compact(quote), compact(DIAGRAM))
        self.assertNotIn('6kg', quote)
        self.assertFalse(any(c['text'].startswith('그리고') for c in card['claims']))
        for claim in card['claims']:
            for evidence in claim['evidence']:
                self.assertIn(compact(evidence['quote']), compact(docs[evidence['doc_id']]['diagram_text']))

    def test_no_description_is_invented_for_title_only(self):
        card, _ = self.check(TITLE)
        self.assertTrue(card['claims'])
        self.assertTrue(all(not c['evidence'] for c in card['claims']))

    def test_generic_slogan_does_not_become_an_explanation(self):
        card, _ = self.check(TITLE+'\n알아서 더 똑똑하고 편리하게')
        self.assertFalse(card['claims'][0]['evidence'])

    def test_different_image_does_not_supply_missing_description(self):
        card, _ = self.check(TITLE, DESCRIPTION)
        title = next(c for c in card['claims'] if c['text'] == TITLE)
        self.assertFalse(title['evidence'])

    def test_different_pdf_page_does_not_supply_missing_description(self):
        card, docs = self.check(TITLE, '다른 페이지\n'+DESCRIPTION)
        title = next(c for c in card['claims'] if c['text'] == TITLE)
        self.assertFalse(title['evidence'])
        self.assertGreater(len({d['upload_unit'] for d in docs.values()}), 1)

    def test_next_ai_feature_does_not_describe_the_previous_title(self):
        card, _ = self.check(TITLE+'\nAI 건조 모드\n의류의 재질에 따라 건조 온도와 시간을 조절합니다.')
        title = next(c for c in card['claims'] if c['text'] == TITLE)
        self.assertFalse(title['evidence'])

    def test_drying_condition_does_not_describe_washing_diagram(self):
        card, _ = self.check(TITLE+'\n세탁물의 재질에 따라 건조 온도를 조절합니다.')
        title = next(c for c in card['claims'] if c['text'] == TITLE)
        self.assertFalse(title['evidence'])

    def test_another_feature_heading_stops_the_diagram_block(self):
        card, _ = self.check(TITLE+'\n스마트 페어링 기능\n세탁물에 따라 세탁 시간과 헹굼 횟수를 조절합니다.')
        title = next(c for c in card['claims'] if c['text'] == TITLE)
        self.assertFalse(title['evidence'])

    def test_footnote_does_not_become_a_diagram_description(self):
        card, _ = self.check(TITLE+'\n* 세탁물 종류에 따라 세탁 시간과 헹굼 횟수를 조절합니다.')
        title = next(c for c in card['claims'] if c['text'] == TITLE)
        self.assertFalse(title['evidence'])

    def test_blank_line_preserves_section_boundary(self):
        card, _ = self.check(TITLE+'\n\n'+DESCRIPTION)
        title = next(c for c in card['claims'] if c['text'] == TITLE)
        self.assertFalse(title['evidence'])

    def test_non_ai_manual_instruction_is_not_promoted(self):
        card, _ = self.check('일반 세탁 코스\n'+DESCRIPTION)
        self.assertFalse(any(e.get('diagram') for c in card['claims'] for e in c.get('evidence', [])))
