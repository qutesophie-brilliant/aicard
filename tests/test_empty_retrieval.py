import unittest
from unittest.mock import patch

from app.pipeline.retrieve import ChunkIndex
from app.pipeline.run import quick_check


class EmptyRetrievalTests(unittest.TestCase):
    def test_punctuation_only_source_has_no_search_results(self):
        index = ChunkIndex([{
            'id': 1, 'kind': '판매페이지', 'chunks': ['… … …', '-----'],
        }])
        self.assertEqual([], index.search('AI 작동 설명'))
        self.assertEqual('… … …', index.condition_context()[0]['quote'])

    def test_empty_and_readable_chunks_keep_source_positions(self):
        index = ChunkIndex([{
            'id': 1, 'kind': '판매페이지',
            'chunks': ['… … …', 'AI가 오염도를 감지해 세탁 시간을 조절합니다.',
                       '사용 전 설명서를 확인하세요.', '설치 안내를 참고하세요.'],
        }])
        results = index.search('오염도를 감지')
        self.assertTrue(results)
        self.assertEqual(1, results[0]['chunk'])

    def test_unreadable_new_input_returns_actionable_validation(self):
        with self.assertRaisesRegex(ValueError, '캡처|PDF'):
            quick_check('새 제품', '', '… … … … … … … … … …')

    def test_readable_new_input_still_builds_unsaved_result(self):
        with patch('app.pipeline.run.db.save_card') as save:
            card, docs = quick_check('새 세탁기', '',
                'AI가 세탁물의 오염도를 감지해 세탁 시간을 조절합니다.',
                '* 이 기능은 세탁량 6kg 이하에서 동작합니다.')
        self.assertTrue(card['claims'])
        self.assertTrue(docs)
        save.assert_not_called()


if __name__ == '__main__':
    unittest.main()
