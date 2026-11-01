"""Contrast operational consumer information with lookalike retail wording."""
import unittest
from unittest.mock import patch
from app.pipeline import quick_coverage
from app.pipeline.retrieve import ChunkIndex
from app.pipeline.run import quick_check


class FiveTopicBoundaryTests(unittest.TestCase):
    def test_retail_consent_is_not_appliance_data_handling(self):
        for text in ('해당 개인 정보는 주문 및 배송 용도로만 사용됩니다.',
                     '주문 시 제3자 개인정보 동의한 것으로 간주 됩니다.',
                     '제 3자에 대한 개인정보 제공 동의(정보통신망법)',
                     '정보통신 서비스 제공자는 개인 정보를 제공받는 자의 개인정보 이용목적을 알려야 한다.',
                     '개인 정보의 이용제한'):
            self.assertFalse(quick_coverage._matches('data', text), text)
        self.assertTrue(quick_coverage._matches('data', '주문 정보는 배송에 사용됩니다. 음성 데이터는 기기 내부에서 처리합니다.'))
        self.assertTrue(quick_coverage._matches('data', '앱에서 개인정보 제공 동의를 해제할 수 있습니다.'))
        self.assertTrue(quick_coverage._other_service_brand('애플 고객센터 080-333-4000', 'LG 냉장고'))
        self.assertFalse(quick_coverage._other_service_brand('LG전자 서비스센터', 'LG 냉장고'))

    cases = {
        'conditions': (
            ['AI 세탁은 세탁물 6kg 이하에서 사용할 수 있습니다.',
             'AI 인식은 어두운 환경에서 성능이 저하될 수 있습니다.',
             '인공지능 세탁은 세탁량 6kg 이하에서 동작합니다. 개봉 후 반품은 제한됩니다.'],
            ['AI 제품은 개봉 후 반품이 제한됩니다.',
             '* 시험 부하: AI 세탁은 6kg 이하에서 동작합니다.',
             '지능형 기술로 더욱 편리한 생활을 제공합니다.']),
        'data': (
            ['사용자의 목소리를 녹음하지 않습니다.',
             '음성 데이터는 기기 내부에서 처리되며 서버로 전송하지 않습니다.',
             '촬영한 사진은 기기 내부에 저장합니다.',
             '녹화 파일은 30일 후 삭제합니다.',
             '사용자의 청소 기록을 저장합니다.'],
            ['USB 데이터 전송 속도는 10Gbps입니다.',
             'AI 학습 데이터 100만 건으로 시험했습니다.',
             '음성 안내로 사용법을 알려줍니다.']),
        'optout': (
            ['자동 모드를 끄고 수동 모드로 사용할 수 있습니다.',
             '설정에서 음성 인식을 비활성화할 수 있습니다.',
             'AI 기능은 끌 수 없습니다.',
             '전원을 끄거나 AI 기능을 설정에서 해제할 수 있습니다.',
             'You can turn off AI tracking in the app.'],
            ['AI 냉장고의 전원 버튼을 눌러 제품을 끌 수 있습니다.',
             'AI 청소기의 전원을 끄세요.',
             'AI 제품의 조명을 끄면 배터리가 절약됩니다.',
             'AI 경험을 해제하여 더 많은 가능성을 만나보세요.',
             'AI 카메라 사용; 시험 시 키보드 조명 끄기']),
        'safety': (
            ['펌웨어 보안 패치를 제공합니다.',
             '과열 시 자동 정지하는 안전장치가 있습니다.',
             '소프트웨어 업데이트는 5년 동안 지원합니다.',
             '보안 업데이트를 지원하지 않습니다.'],
            ['안전한 AI 기술로 일상을 바꿉니다.',
             '제품 정보는 매일 업데이트됩니다.',
             '배송 상태를 업데이트합니다.',
             '2K 회전형 Wi-Fi 보안 카메라']),
        'remedy': (
            ['제품의 무상 보증 기간은 1년입니다.',
             '필터 불량 시 고객센터에서 교환해 드립니다.',
             '소모품은 무상 보증에서 제외됩니다.',
             '고객센터에 수리를 문의하세요.',
             'This product has a limited warranty.'],
            ['필터는 6개월마다 교환해 주세요.',
             '호환 필터로 교환하여 사용하세요.',
             '배터리를 교환하면 오래 사용할 수 있습니다.',
             '먼지 봉투 교환 방법을 안내합니다.']),
    }

    def test_inclusion_exclusion_for_all_five_topics(self):
        for key, (included, excluded) in self.cases.items():
            for expected, examples in ((True, included), (False, excluded)):
                for quote in examples:
                    with self.subTest(key=key, quote=quote):
                        self.assertEqual(expected, quick_coverage._matches(key, quote))

    def test_no_keyword_search_match_still_checks_all_five_categories(self):
        quotes = [examples[0][0] for examples in self.cases.values()]
        index = ChunkIndex([{'id': 15, 'kind': '판매페이지', 'url': 'https://example.test/item', 'chunks': quotes}])
        with patch.object(index, 'search', return_value=[]):
            items = quick_coverage.fill(index)
        for key, quote in zip(self.cases, quotes):
            with self.subTest(key=key):
                self.assertIn(quote, [e['quote'] for e in items[key]['evidence']])
                self.assertTrue(all(e['doc_id'] == 15 for e in items[key]['evidence']))

    def test_preview_preserves_negative_statement_and_does_not_classify_power_or_filter(self):
        card, _ = quick_check('점검용 AI 청소기', '',
            'AI가 장애물을 인식합니다.\n사용자의 목소리를 녹음하지 않습니다.\n'
            'AI 청소기의 전원을 끄세요.\n필터는 6개월마다 교환해 주세요.')
        self.assertEqual(['사용자의 목소리를 녹음하지 않습니다.'],
                         [e['quote'] for e in card['items']['data']['evidence']])
        self.assertFalse(card['items']['optout']['evidence'])
        self.assertFalse(card['items']['remedy']['evidence'])

    def test_different_topics_in_same_document_stay_separate(self):
        quotes = [examples[0][0] for examples in self.cases.values()]
        card, _ = quick_check('분류 시험', '', '\n'.join(quotes))
        for key, quote in zip(self.cases, quotes):
            self.assertEqual([quote], [e['quote'] for e in card['items'][key]['evidence']])
