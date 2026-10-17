import unittest
from app.schema import heads_up


class PublicSummaryTests(unittest.TestCase):
    def test_breakdown_includes_conditions_only_without_changing_status(self):
        card = {'claims': [
            {'id': 'c1', 'status': '근거 확인', 'basis_kind': 'sensor'},
            {'id': 'c2', 'status': '조건 확인', 'basis_kind': 'none'},
        ]}
        result = heads_up(card)[0]['text']
        self.assertIn('2개', result)
        self.assertIn('센서·규칙 1개', result)
        self.assertIn('조건·한계만 제시 1개', result)

    def test_summary_does_not_say_all_when_only_conditions_are_present(self):
        card = {'claims': [{'id': 'c1', 'status': '조건 확인', 'basis_kind': 'none'}]}
        result = heads_up(card)[0]['text']
        self.assertIn('조건·한계만 제시 1개', result)
        self.assertNotIn('센서', result)

