import json
import unittest
from unittest.mock import patch

import httpx

from app import config
from app.pipeline import llm


class IndexedOcrSchemaTests(unittest.TestCase):
    def test_indexed_ocr_labels_each_image_and_constrains_complete_response(self):
        response = httpx.Response(200, json={'candidates': [{'content': {'parts': [
            {'text': json.dumps({'pages': [
                {'image': 3, 'text': '세 번째'},
                {'image': 1, 'text': '첫 번째'},
                {'image': 2, 'text': ''},
            ]}, ensure_ascii=False)}
        ]}}]})
        with patch.object(config, 'LLM_PROVIDER', 'gemini'), \
             patch.object(config, 'GEMINI_MODELS', ['gemini-3.8-flash']), \
             patch.object(config, 'GEMINI_ENDPOINTS', ['https://example.com/{model}']), \
             patch.object(llm, '_gemini_ok', None), \
             patch.object(llm, '_gemini_dead', set()), \
             patch('httpx.post', return_value=response) as send:
            texts = llm.ocr_images_indexed([b'one', b'two', b'three'])
        self.assertEqual(['첫 번째', '', '세 번째'], texts)
        self.assertEqual(1, send.call_count)
        body = send.call_args.kwargs['json']
        parts = body['contents'][0]['parts']
        self.assertEqual(['[입력 이미지 1]', '[입력 이미지 2]', '[입력 이미지 3]'],
                         [parts[i]['text'] for i in (0, 2, 4)])
        self.assertTrue(all('inline_data' in parts[i] for i in (1, 3, 5)))
        pages = body['generationConfig']['responseSchema']['properties']['pages']
        self.assertEqual('3', pages['minItems'])
        self.assertEqual('3', pages['maxItems'])
        self.assertEqual(['image', 'text'], pages['items']['required'])
        self.assertEqual({'type': 'INTEGER', 'minimum': 1, 'maximum': 3},
                         pages['items']['properties']['image'])
        self.assertIsNone(llm._ocr_budget.get())

    def test_formal_generation_does_not_receive_ocr_labels_or_schema(self):
        response = httpx.Response(200, json={'candidates': [{'content': {'parts': [
            {'text': '{"pages": ["원문"]}'}
        ]}}]})
        with patch.object(config, 'GEMINI_MODELS', ['gemini-3.8-flash']), \
             patch.object(config, 'GEMINI_ENDPOINTS', ['https://example.com/{model}']), \
             patch.object(llm, '_gemini_ok', None), \
             patch.object(llm, '_gemini_dead', set()), \
             patch('httpx.post', return_value=response) as send:
            llm._gemini_call('formal card', [b'one'], max_tokens=8000)
        body = send.call_args.kwargs['json']
        self.assertEqual(2, len(body['contents'][0]['parts']))
        self.assertIn('inline_data', body['contents'][0]['parts'][0])
        self.assertNotIn('responseSchema', body['generationConfig'])
        self.assertNotIn('thinkingConfig', body['generationConfig'])


if __name__ == '__main__':
    unittest.main()
