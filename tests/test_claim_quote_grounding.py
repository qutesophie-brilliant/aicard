"""Quote safety regression cases, separate from every frozen accuracy set."""
import unittest
from unittest.mock import patch
from app.pipeline import extract


class ClaimQuoteGrounding(unittest.TestCase):
    source = 'AI 카메라는 사람과 반려동물을 구별하고 움직임을 감지합니다.'

    def run_extract(self, response, chunks=None):
        chunks = chunks or [{'doc_id': 1, 'chunk': 0, 'quote': self.source, 'kind': '판매페이지'}]
        with patch.object(extract.llm, 'available', return_value=True), \
             patch.object(extract.llm, 'ask_json', return_value=[response]), \
             patch.object(extract.config, 'EXTRACT_PASSES', 1), \
             patch.object(extract, '_postfilter', side_effect=lambda rows, *_: rows), \
             patch.object(extract, '_must_include', side_effect=lambda rows, *_: rows), \
             patch.object(extract.config, 'EXTRACT_VERIFY', False):
            return extract.extract_claims(chunks)

    def test_real_sentence_is_preserved(self):
        self.assertEqual(self.run_extract({'chunk': 0, 'text': self.source})[0]['text'], self.source)

    def test_same_prefix_changed_suffix_is_rejected(self):
        fake = 'AI 카메라는 사람과 반려동물을 구별하고 영상을 외부로 전송하지 않습니다.'
        self.assertEqual(self.run_extract({'chunk': 0, 'text': fake}), [])

    def test_same_prefix_changed_number_is_rejected(self):
        q = 'AI 기능은 사용 이력을 분석하며 데이터는 7일 보관합니다.'
        chunks = [{'doc_id': 1, 'chunk': 0, 'quote': q, 'kind': '판매페이지'}]
        self.assertEqual(self.run_extract({'chunk': 0, 'text': q.replace('7일', '30일')}, chunks), [])

    def test_layout_whitespace_is_allowed(self):
        q = self.source.replace('반려동물을 ', '반려동물을\n')
        chunks = [{'doc_id': 1, 'chunk': 0, 'quote': q, 'kind': '판매페이지'}]
        self.assertEqual(len(self.run_extract({'chunk': 0, 'text': self.source}, chunks)), 1)

    def test_wrong_chunk_is_recovered_using_full_sentence(self):
        chunks = [{'doc_id': 1, 'chunk': 0, 'quote': self.source.replace('움직임', '얼굴'), 'kind': '판매페이지'},
                  {'doc_id': 2, 'chunk': 0, 'quote': self.source, 'kind': '판매페이지'}]
        result = self.run_extract({'chunk': 0, 'text': self.source}, chunks)
        self.assertEqual(result[0]['source']['doc_id'], 2)

    def test_unknown_sentence_is_rejected(self):
        self.assertEqual(self.run_extract({'chunk': 5, 'text': 'AI 기능이 모든 사고를 완벽히 막아 줍니다.'}), [])


if __name__ == '__main__':
    unittest.main()
