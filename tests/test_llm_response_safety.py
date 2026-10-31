import json
import unittest
from unittest.mock import patch

from app.pipeline import llm, extract


class LLMResponseSafetyTests(unittest.TestCase):
    def test_valid_object_array_and_fenced_json_preserved(self):
        for text, value in [(' {"text":"원문"}', {'text':'원문'}),
                            ('```json\n[1,2]\n```', [1,2])]:
            self.assertEqual(llm._extract_json(text), value)

    def test_scalar_nonfinite_and_duplicate_fields_rejected(self):
        for text in ['null', 'true', '3', '"text"', '{"state":"있음","state":"없음"}',
                     '{"confidence":NaN}', '[Infinity]', '[1e9999]', '[-1e9999]']:
            with self.subTest(text=text), self.assertRaises(json.JSONDecodeError):
                llm._extract_json(text)

    def test_invalid_json_uses_existing_bounded_retry(self):
        with patch.object(llm, 'provider', return_value='gemini'), \
             patch.object(llm, '_gemini_call', side_effect=['null','{"text":"원문"}']) as model:
            self.assertEqual(llm.ask_json('read source'), {'text':'원문'})
        self.assertEqual(model.call_count, 2)

    def test_malformed_extraction_rows_do_not_crash_or_create_claims(self):
        source='AI가 세탁물의 무게를 감지해 세탁 시간을 조절합니다.'
        chunks=[{'doc_id':1,'chunk':0,'quote':source,'kind':'판매페이지'}]
        rows=[None, 7, 'wrong', {'text':{'nested':'wrong'}}, {'text':source,'chunk':0}]
        with patch.object(llm, 'available', return_value=True), \
             patch.object(llm, 'ask_json', return_value=rows), \
             patch.object(extract.config, 'EXTRACT_PASSES', 1), \
             patch.object(extract.config, 'EXTRACT_VERIFY', False):
            result=extract.extract_claims(chunks)
        self.assertTrue(result)
        self.assertTrue(all(c['text'] in source for c in result))

    def test_malformed_drop_list_preserves_valid_claims(self):
        claims=[{'id':'c1','text':'첫 문장'},{'id':'c2','text':'둘째 문장'}]
        with patch.object(llm, 'ask_json', return_value={'drop':[{'id':'c1'},None,4]}):
            self.assertEqual(extract._verify(claims), claims)


if __name__ == '__main__':
    unittest.main()
