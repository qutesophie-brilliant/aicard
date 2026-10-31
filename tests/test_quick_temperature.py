import re
import unittest

from app.pipeline.quick_diagrams import capture_uploads
from app.pipeline.run import quick_check


TITLE = 'AI 자동온도 케어시스템'
RANGE = '헤르젠 쌀냉장고는 12도-18도를 유지시켜줍니다.'
CONTROL = ('외부온도에 반응하는 스마트 항온센서\n'
           '12도에 도달하면 중지, 17도가 되면\n'
           '냉각시스템 재가동으로 전력을 스스로 아껴줌')
LIMIT = ('실내온도가 30°C 이상일 때 쌀냉장고 내부 온도가 20°C 이하로 '
         '내려가지 않을 수 있습니다.')
SOURCE = TITLE + '\n(12~18°C)\n' + RANGE + '\n\n' + CONTROL + '\n\n주의사항\n' + LIMIT
compact = lambda s: re.sub(r'\s+', '', s)


class QuickTemperatureTests(unittest.TestCase):
    def check_upload(self, *texts):
        capture = capture_uploads(list(texts), '')
        return quick_check('실제 원문 검수', '', '', source_capture=capture)

    def test_real_temperature_control_and_limit_are_quoted(self):
        card, docs = self.check_upload(SOURCE)
        claim = next(c for c in card['claims'] if c['text'] == TITLE)
        quotes = [e['quote'] for e in claim['evidence']]
        self.assertTrue(any(compact(CONTROL) in compact(q) for q in quotes))
        self.assertIn(RANGE, quotes)
        self.assertIn(LIMIT, quotes)
        self.assertTrue(claim['conditions_noted'])
        self.assertTrue(any(e['quote'] == LIMIT for e in card['items']['conditions']['evidence']))
        self.assertFalse(any(c['text'] == CONTROL.splitlines()[-1] for c in card['claims']))
        for evidence in claim['evidence']:
            self.assertIn(compact(evidence['quote']), compact(docs[evidence['doc_id']]['diagram_text']))

    def test_pasted_text_uses_same_source_only_path(self):
        card, _ = quick_check('원문 검수', '', SOURCE)
        claim = next(c for c in card['claims'] if c['text'] == TITLE)
        self.assertTrue(any(compact(CONTROL) in compact(e['quote']) for e in claim['evidence']))

    def test_wrapped_limit_keeps_original_ocr_lines_together(self):
        wrapped = LIMIT.replace('내려가지', '\n내려가지')
        card, _ = self.check_upload(SOURCE.replace(LIMIT, wrapped))
        claim = next(c for c in card['claims'] if c['text'] == TITLE)
        self.assertTrue(any(compact(e['quote']) == compact(LIMIT) and e.get('condition_type') == 'operating'
                            for e in claim['evidence']))

    def test_actual_image_source_url_is_preserved(self):
        from app.pipeline.ingest import chunk_text, merge_ocr_lines
        image_url = 'https://image.cjonstyle.net/cjupload/htmledit/ven_img/510513/0910her.jpg'
        text = merge_ocr_lines(SOURCE)
        capture = {'sections': [{'section_id': 0, 'kind': '판매페이지', 'feature_scope': '',
            'source_type': 'image_ocr', 'source_url': image_url, 'text': text,
            'chunks': chunk_text(text, ocr=True), 'snapshot_sha256': 'fixture'}],
            'images': [], 'coverage': {'scope': 'identified_product_html_only'}}
        card, docs = quick_check('제품', 'https://display.cjonstyle.com/p/item/2074044916', '', source_capture=capture)
        evidence = [e for c in card['claims'] for e in c['evidence'] if e.get('temperature_control')]
        self.assertTrue(evidence)
        self.assertTrue(all(docs[e['doc_id']]['url'] == image_url for e in evidence))

    def test_another_thermal_feature_makes_fallback_ambiguous(self):
        card, _ = self.check_upload(SOURCE + '\nAI 냉동 온도 조절 기능')
        self.assertFalse(any(e.get('temperature_control') for c in card['claims'] for e in c['evidence']))

    def test_cannot_borrow_control_or_limit_from_another_upload(self):
        card, _ = self.check_upload(TITLE, CONTROL + '\n' + LIMIT)
        claim = next(c for c in card['claims'] if c['text'] == TITLE)
        self.assertFalse(any(e.get('temperature_control') for e in claim['evidence']))

    def test_temperature_range_or_limit_alone_is_not_a_mechanism(self):
        card, _ = self.check_upload(TITLE + '\n' + RANGE + '\n' + LIMIT)
        claim = next(c for c in card['claims'] if c['text'] == TITLE)
        self.assertFalse(any(e.get('temperature_control') for e in claim['evidence']))

    def test_drying_and_freezing_do_not_borrow_cooling_control(self):
        for title in ['AI 건조 온도 제어', 'AI 냉동 온도 조절', 'AI 가열 온도 조절']:
            card, _ = self.check_upload(title + '\n' + CONTROL + '\n' + LIMIT)
            self.assertFalse(any(e.get('temperature_control') for c in card['claims'] for e in c['evidence']))

    def test_no_temperature_numbers_are_supplied(self):
        card, _ = self.check_upload(TITLE + '\n외부온도에 반응하는 스마트 항온센서\n냉각시스템 중지 또는 재가동')
        self.assertFalse(any(e.get('temperature_control') for c in card['claims'] for e in c['evidence']))

    def test_uses_only_the_numbers_in_each_new_source(self):
        changed = SOURCE.replace('12', '8').replace('18', '14').replace('17', '13').replace('30', '35').replace('20', '22')
        card, _ = self.check_upload(changed)
        evidence = [e for c in card['claims'] for e in c['evidence'] if e.get('temperature_control')]
        self.assertTrue(evidence)
        for item in evidence:
            self.assertIn(compact(item['quote']), compact(changed))
        self.assertTrue(any('35°C' in e['quote'] and '22°C' in e['quote'] for e in evidence))

    def test_lab_limits_are_not_presented_as_operating_conditions(self):
        trial = '시험 조건: 실내온도 30°C에서 내부 온도 20°C 이하로 내려가지 않을 수 있습니다.'
        card, _ = self.check_upload(TITLE + '\n' + CONTROL + '\n' + trial)
        self.assertFalse(any(e.get('temperature_control') and e.get('condition_type') == 'operating'
                             for c in card['claims'] for e in c['evidence']))

    def test_separate_trial_heading_does_not_become_usage_advice(self):
        card, _ = self.check_upload(TITLE + '\n' + CONTROL + '\n\n시험 조건:\n' + LIMIT)
        self.assertFalse(any(e.get('temperature_control') and e.get('condition_type') == 'operating'
                             for c in card['claims'] for e in c['evidence']))

    def direct(self, text, titles):
        from app.pipeline.quick_temperature import connect
        docs = [dict(id=0, upload_unit=0, text=text, chunks=text.splitlines(), kind='판매페이지')]
        claims = [dict(id=str(i), text=title, source={'doc_id': 0}, evidence=[], status='자료 부족')
                  for i, title in enumerate(titles)]
        return connect(claims, docs)

    def test_two_refrigerators_do_not_share_controls_or_limits(self):
        first, second = 'AI 쌀냉장고 자동온도 케어', 'AI 음료냉장고 자동온도 케어'
        text = (first + '\n온도 센서가 12도에서 냉각을 중지하고 17도에서 재가동합니다.\n'
                'Point 2\n' + second + '\n온도 센서가 3도에서 냉각을 중지하고 5도에서 재가동합니다.\n'
                '실내온도가 30도 이상이면 음료 냉장고 내부 온도가 8도 이하로 내려가지 않을 수 있습니다.')
        self.assertTrue(all(not c['evidence'] for c in self.direct(text, [first, second])))
        card, _ = self.check_upload(text)
        self.assertFalse(any(e.get('temperature_control') for c in card['claims'] for e in c['evidence']))

    def test_two_ai_cooling_features_with_one_control_are_ambiguous(self):
        first, second = TITLE, 'AI 냉각 보관 기능'
        text = first + '\n' + CONTROL + '\nPoint 2\n' + second
        self.assertTrue(all(not c['evidence'] for c in self.direct(text, [first, second])))

    def test_one_claim_with_two_distinct_controls_is_ambiguous(self):
        text = SOURCE + '\nPoint 2\n온도 센서가 3도에서 냉각을 중지하고 5도에서 재가동합니다.'
        self.assertFalse(self.direct(text, [TITLE])[0]['evidence'])

    def test_non_ai_second_refrigerator_does_not_supply_control(self):
        title = 'AI 쌀냉장고 자동온도 케어'
        text = title + '\nPoint 2\n음료 냉장고\n' + CONTROL
        self.assertFalse(self.direct(text, [title])[0]['evidence'])

    def test_conflicting_ranges_and_limits_are_not_chosen_by_length(self):
        second_range = '냉장 온도는 3도-5도를 유지합니다.'
        second_limit = '실내온도가 35도 이상이면 내부 온도가 8도 이하로 내려가지 않을 수 있습니다.'
        claim = self.direct(SOURCE + '\n' + second_range + '\n' + second_limit, [TITLE])[0]
        self.assertTrue(any(compact(CONTROL) == compact(e['quote']) for e in claim['evidence']))
        self.assertFalse(any(e.get('condition_type') == 'operating' for e in claim['evidence']))
        self.assertFalse(any(e['quote'] in (RANGE, second_range) for e in claim['evidence']))

    def test_repeated_same_control_and_product_name_keep_unique_feature(self):
        claim = self.direct('헤르젠 쌀냉장고 AI CARE+\n' + SOURCE + '\n' + CONTROL,
                            ['헤르젠 쌀냉장고 AI CARE+', TITLE])[1]
        self.assertTrue(any(compact(CONTROL) == compact(e['quote']) for e in claim['evidence']))

    def test_merged_ai_care_product_paragraph_is_not_a_second_thermal_claim(self):
        product = ('산화된 쌀 건강한 쌀 헤르젠 쌀냉장고 AI Care+ 쌀냉장고 원리 '
                   '4D 순환방식 냉각시스템 2중 밀폐기능 쌀 잔여량 표시')
        feature = 'Point 01 AI 자동온도 케어시스템 (12~18℃) ' + RANGE
        text = product + '\n\n' + feature + '\n\n' + CONTROL + '\n\n주의사항\n' + LIMIT
        claims = self.direct(text, [product, feature])
        self.assertFalse(claims[0]['evidence'])
        self.assertTrue(any(compact(CONTROL) == compact(e['quote']) for e in claims[1]['evidence']))
        self.assertTrue(any(e['quote'] == LIMIT for e in claims[1]['evidence']))

    def test_general_kimchi_fridge_comparison_does_not_hide_rice_fridge_control(self):
        text = ('일반 김치냉장고\n쌀이 얼었다 녹았다 하는 보관 환경\n\n'
                'Point 01\n' + SOURCE.replace(CONTROL, 'Point 02\n4D 냉각 기술\n' + CONTROL))
        card, _ = self.check_upload(text)
        claim = next(c for c in card['claims'] if c['text'] == TITLE)
        self.assertTrue(any(compact(CONTROL) == compact(e['quote']) for e in claim['evidence']))
        self.assertTrue(any(e['quote'] == LIMIT for e in claim['evidence']))

    def test_raw_image_lines_preserve_point_and_trial_boundaries(self):
        from app.pipeline.ingest import chunk_text, merge_ocr_lines
        raw = ('일반 김치냉장고\n쌀이 얼었다 녹았다 하는 보관 환경\n\n'
               'Point 01\n' + TITLE + '\n' + RANGE + '\n\n'
               'Point 02\n4D 냉각 기술\n' + CONTROL + '\n\n'
               '시험 조건\n실내온도 35°C에서 내부 온도가 8°C 이하로 내려가지 않을 수 있습니다.\n\n'
               '주의사항\n' + LIMIT)
        text = merge_ocr_lines(raw)
        capture = {'sections': [{'section_id': 0, 'kind': '판매페이지', 'feature_scope': '',
            'source_type': 'image_ocr', 'source_url': 'https://example.com/product-detail.jpg',
            'text': text, 'diagram_text': raw, 'chunks': chunk_text(text, ocr=True),
            'snapshot_sha256': 'fixture'}], 'images': [],
            'coverage': {'scope': 'identified_product_html_only'}}
        card, _ = quick_check('제품', 'https://example.com/product', '', source_capture=capture)
        evidence = [e for c in card['claims'] for e in c['evidence'] if e.get('temperature_control')]
        self.assertTrue(any(compact(CONTROL) == compact(e['quote']) for e in evidence))
        self.assertTrue(any(e['quote'] == LIMIT for e in evidence))
        self.assertFalse(any('35°C' in e['quote'] for e in evidence))

