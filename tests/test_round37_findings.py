import unittest
from app.pipeline import extract, quick_coverage


class NewKoreanProductFindings(unittest.TestCase):
    def test_interface_throughput_is_not_personal_data_evidence(self):
        for quote in (
            '2개의 고속 USB4 Type-C 포트는 고속 충전, 최대 5K 외부 디스플레이 및 최대 40Gbps 데이터 전송을 지원합니다.',
            'Thunderbolt 포트로 최대 40Gbps의 데이터 전송을 지원합니다.',
        ):
            self.assertFalse(quick_coverage._matches('data', quote), quote)

    def test_actual_data_handling_is_kept_even_with_interface_context(self):
        for quote in (
            '카메라의 영상 데이터는 클라우드로 전송해 저장합니다.',
            'USB 포트로 연결된 카메라의 사용자 데이터는 서버로 전송하지 않습니다.',
        ):
            self.assertTrue(quick_coverage._matches('data', quote), quote)

    def test_navigation_is_not_an_ai_claim(self):
        quote='Learn more about ASUS Next-Gen AI PCs'
        row={'doc_id':1,'chunk':0,'quote':quote}
        self.assertEqual([],extract._rules_extract([row],14,complete_quotes=True))

    def test_real_learning_function_is_not_filtered(self):
        quote='AI learns your cleaning habits and adjusts the cleaning route.'
        row={'doc_id':1,'chunk':0,'quote':quote}
        self.assertTrue(extract._rules_extract([row],14,complete_quotes=True))
