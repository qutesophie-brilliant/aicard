import unittest
from app.pipeline import link, llm
from app.pipeline.retrieve import ChunkIndex
from app.pipeline.run import quick_check


CLAIM = '헤르젠 대표 연제윤 헤르젠 쌀냉장고 AI Care+ UPGRADE 01 UV LED를 더해 매일 자동 살균 보관을 넘어 이제 쌀 속 위생까지 책임지는 쌀을 지키는 가장 스마트한 방법!!'
REVIEW = '신선하게 보관해서 먹는 편리함~ 강추합니다^^ HERZHEN 헤르젠 전속모델 윤유선 쌀을 건강하게, 생활을 더 편리하게 고객님들의 아낌없는 관심과 성원에 진심으로 감사드립니다.'


class QuickTestimonialTests(unittest.TestCase):
    def test_actual_review_not_linked_as_technical_explanation(self):
        capture = {'coverage': {}, 'sections': [{
            'section_id': 0, 'feature_scope': '', 'source_url': 'https://example.test/detail.jpg',
            'snapshot_sha256': 'test', 'source_type': 'image_ocr',
            'text': CLAIM + '\n' + REVIEW, 'chunks': [CLAIM, REVIEW],
        }]}
        card, docs = quick_check('제품', '', '', source_capture=capture)
        self.assertTrue(card['claims'])
        self.assertTrue(any(REVIEW in doc['text'] for doc in docs.values()))
        for claim in card['claims']:
            self.assertFalse(any('강추' in ev['quote'] or '성원' in ev['quote'] for ev in claim['evidence']))

    def test_real_recommendation_feature_is_preserved(self):
        claim = {'id': 'c1', 'text': 'AI 맞춤 코스',
                 'source': {'doc_id': 1, 'chunk': 0, 'quote': 'AI 맞춤 코스'}}
        explanation = 'AI가 세탁물의 무게와 오염도를 분석하여 적절한 세탁 코스를 추천합니다.'
        index = ChunkIndex([{'id': 1, 'kind': '판매페이지', 'text': '', 'chunks': ['AI 맞춤 코스', explanation]}])
        with llm.rules_only():
            result = link.link_claims([claim], index, exclude_testimonials=True)
        self.assertTrue(any(explanation in e['quote'] for e in result[0]['evidence']))

    def test_filter_preserves_weather_and_recommendation_feature_words(self):
        self.assertFalse(link._TESTIMONIAL.search('강추위에 대비하여 센서가 온도를 조절합니다.'))
        self.assertFalse(link._TESTIMONIAL.search('AI가 사용 이력을 분석하여 코스를 추천합니다.'))
        self.assertTrue(link._TESTIMONIAL.search(REVIEW))


if __name__ == '__main__':
    unittest.main()
