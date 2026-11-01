import unittest
from app.pipeline import extract, run


def chunks(texts):
    return [{'doc_id':1,'chunk':i,'quote':text} for i,text in enumerate(texts)]


class QuickClaimRecallTests(unittest.TestCase):
    def test_intelligent_and_implicit_learning_phrases_are_candidates(self):
        texts = ['알아서 쾌적한 습도를 유지합니다.', '지능형 모드로 실내 온도를 조절합니다.',
                 '사용 패턴을 학습하고 청소 경로를 설정합니다.', '인공 지능을 기반으로 습도를 조절합니다.']
        claims = extract._rules_extract(chunks(texts), 20, complete_quotes=True)
        self.assertEqual(set(texts), {c['text'] for c in claims})

    def test_shared_prefix_does_not_drop_different_conditions(self):
        prefix = 'AI 맞춤 제어는 사용자가 설정한 목표 습도와 실내 환경을 분석하여 '
        texts = [prefix+'분무량을 조절합니다.', prefix+'온도를 조절합니다.',
                 'AI 모드는 습도 40%를 유지합니다.', 'AI 모드는 습도 60%를 유지합니다.']
        result = extract._rules_extract(chunks(texts+[texts[0]]), 20, complete_quotes=True)
        self.assertEqual(set(texts), {c['text'] for c in result})

    def test_preview_keeps_more_than_fourteen_distinct_claims(self):
        texts = [f'AI 맞춤 제어의 설정 {i}번은 목표 온도를 {i+10}도로 조절합니다.' for i in range(20)]
        card, _ = run.quick_check('검증용 제품', 'https://example.com/product/1', '\n'.join(texts))
        self.assertEqual(20, len(card['claims']))
        self.assertEqual(set(texts), {c['text'] for c in card['claims']})

    def test_recommendations_and_plain_automation_are_not_ai_claims(self):
        texts = ['AI 추천 상품을 구매하세요.', '예약한 시간에 자동으로 전원을 켭니다.',
                 '제품의 전원 버튼을 눌러 주세요.']
        self.assertEqual([], extract._rules_extract(chunks(texts), 20, complete_quotes=True))
