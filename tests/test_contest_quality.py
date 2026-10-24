"""Development regressions; not a new accuracy study or held-out evaluation."""
import unittest
from unittest.mock import patch

from app.pipeline import coverage, llm
from app.pipeline.run import quick_check, split_fine_print
from app.schema import COVERAGE_ITEMS


FUNCTION = 'AI 맞춤 세탁은 센서로 세탁물의 무게와 오염도를 감지하여 세탁 시간과 물 사용량을 조절합니다.'
LIMIT = 'AI 맞춤 세탁은 면 소재 세탁물 6kg 이하에서만 사용할 수 있습니다.'
ENVIRONMENT = '실제 세탁 결과는 세탁물의 종류와 사용 환경에 따라 달라질 수 있습니다.'


def candidate(text, n=0):
    return {'doc_id': 1, 'chunk': n, 'quote': text, 'kind': '판매페이지'}


class InputBoundaryTests(unittest.TestCase):
    def test_mixed_paragraph_keeps_function_and_environment_separate(self):
        sales, fine = split_fine_print('점검용 AI 세탁기\n' + ' '.join([FUNCTION, LIMIT, ENVIRONMENT]))
        self.assertIn(FUNCTION, sales)
        self.assertNotIn(ENVIRONMENT, sales)
        self.assertIn(ENVIRONMENT, fine)
        with llm.rules_only():
            card, _ = quick_check('점검용 AI 세탁기', '', sales, fine)
        self.assertIn(FUNCTION, [c['text'] for c in card['claims']])

    def test_linebreak_does_not_change_claims(self):
        results = []
        for separator in (' ', '\n'):
            sales, fine = split_fine_print('점검용 AI 세탁기\n' + separator.join([FUNCTION, LIMIT, ENVIRONMENT]))
            with llm.rules_only():
                card, _ = quick_check('점검용 AI 세탁기', '', sales, fine)
            results.append([c['text'] for c in card['claims']])
        self.assertEqual(results[0], results[1])
        self.assertIn(FUNCTION, results[0])

    def test_coupon_sentence_does_not_erase_adjacent_function(self):
        sales, fine = split_fine_print(FUNCTION + ' 쿠폰은 결제 시 적용됩니다.')
        self.assertIn(FUNCTION, sales)
        self.assertNotIn('쿠폰', sales + fine)

    def test_explicit_footnote_scope_is_preserved(self):
        note = '* 실험실 자체 시험 결과입니다. ' + FUNCTION
        sales, fine = split_fine_print(note)
        self.assertEqual('', sales)
        self.assertEqual(note, fine)

    def test_decimal_is_not_a_sentence_boundary(self):
        text = 'AI가 2.4 GHz Wi-Fi로 연결된 기기의 사용 패턴을 학습합니다.'
        sales, fine = split_fine_print(text)
        self.assertEqual(text, sales)
        self.assertEqual('', fine)


class CoverageEvidenceTests(unittest.TestCase):
    def fill(self, cands, summary, evidence=None):
        candidates = {key: [] for key in COVERAGE_ITEMS}
        candidates['remedy'] = cands
        output = {key: {'status': '자료 부족', 'summary': '', 'evidence': []} for key in COVERAGE_ITEMS}
        output['remedy'] = {'status': '조건 확인', 'summary': summary,
                            'evidence': list(range(len(cands))) if evidence is None else evidence}
        with patch.object(llm, 'ask_json', return_value=output):
            return coverage._fill_llm_batch(candidates)['remedy']

    def test_invalid_source_indices_never_select_an_unrelated_quote(self):
        cands = [candidate('첫 원문'), candidate('다른 원문', 1)]
        self.assertEqual([], coverage._pack(cands, [-1, True, 0.9, '0', 2, None]))
        self.assertEqual([], coverage._pack(cands, '0'))
        self.assertEqual([{'doc_id': 1, 'chunk': 0, 'quote': '첫 원문', 'kind': '판매페이지'}],
                         coverage._pack(cands, [0, 0]))

    def test_unsupported_positive_summary_is_not_displayed(self):
        item = self.fill([candidate('고객센터 문의')], '제품 가격을 전액 환불합니다.', [-1])
        self.assertEqual('자료 부족', item['status'])
        self.assertNotIn('전액', item['summary'])
        self.assertFalse(item['evidence'])

    def test_multiple_subscription_policies_are_not_general_product_refunds(self):
        cands = [candidate('AI 올인원2.0 구독 중도해지 시 파손료는 분실료의 50%입니다.'),
                 candidate('AI 스마트 요금제 구독케어 무상수리서비스는 품질보증기간 이내 해지 시 전액 환불됩니다.', 1)]
        item = self.fill(cands, '품질보증기간 이내 해지 시 전액 환불되며 파손료는 분실료의 50%입니다.')
        self.assertEqual('추가 확인 필요', item['status'])
        self.assertIn('구독', item['summary'])
        self.assertNotIn('전액 환불', item['summary'])
        self.assertEqual(2, len(item['evidence']))

    def test_unscoped_subscription_summary_requires_review(self):
        item = self.fill([candidate('AI 스마트 요금제 구독케어 무상수리서비스는 해지 시 환불 조건이 적용됩니다.')],
                         '품질보증기간 이내 해지 시 전액 환불됩니다.')
        self.assertEqual('추가 확인 필요', item['status'])

    def test_explicit_single_contract_scope_is_retained(self):
        summary = 'AI 스마트 요금제의 구독케어 무상수리서비스 해지에는 별도 환불 조건이 적용됩니다.'
        item = self.fill([candidate(summary)], summary)
        self.assertEqual('조건 확인', item['status'])
        self.assertEqual(summary, item['summary'])

    def test_ordinary_product_warranty_is_not_subscription(self):
        summary = '제품 보증기간은 1년이며 구매 후 7일 이내 반품 조건은 원문에 안내되어 있습니다.'
        item = self.fill([candidate(summary)], summary)
        self.assertEqual('조건 확인', item['status'])
        self.assertEqual(summary, item['summary'])

    def test_generic_scoped_subscription_contact_is_not_a_refund_guarantee(self):
        summary = '구독 고객센터에서 해지를 신청하면 계약 조건에 따라 환불 금액을 산정합니다.'
        item = self.fill([candidate('AI 스마트 요금제 구독케어 해지에 따른 고객 환불 금액을 산정합니다.')], summary)
        self.assertEqual('조건 확인', item['status'])
        self.assertEqual(summary, item['summary'])

    def test_missing_information_is_not_promoted_to_a_conflict(self):
        item = {'status':'자료 부족','summary':'품질보증기간 이내 해지 시 환불 안내만 있습니다.',
                'evidence':[candidate('품질보증기간 이내 고객 해지 시 전액 환불')]}
        self.assertEqual(item, coverage._guard_remedy_scope(item, item['evidence']))


if __name__ == '__main__':
    unittest.main()
