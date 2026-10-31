import json
import time
import unittest
from contextlib import contextmanager, ExitStack
from unittest.mock import patch

import httpx

from app import config, main
from app.pipeline import llm, ocr, product_ocr


def response(text, reason='STOP'):
    return httpx.Response(200, json={'candidates': [{
        'finishReason': reason, 'content': {'parts': [{'text': text}]}}]})


@contextmanager
def provider(result):
    with ExitStack() as stack:
        for name, value in [('LLM_PROVIDER', 'gemini'), ('GEMINI_API_KEY', 'fixture-key'),
                            ('GEMINI_MODELS', ['gemini-3.8-flash']),
                            ('GEMINI_ENDPOINTS', ['https://example.com/{model}'])]:
            stack.enter_context(patch.object(config, name, value))
        stack.enter_context(patch.object(llm, '_gemini_ok', None))
        stack.enter_context(patch.object(llm, '_gemini_dead', set()))
        kwargs = {'side_effect': result} if isinstance(result, Exception) else {'return_value': result}
        send = stack.enter_context(patch('httpx.post', **kwargs))
        yield send


class OcrResponseErrorTests(unittest.TestCase):
    def assert_failure(self, result, category):
        with provider(result) as send:
            with self.assertRaises(llm.LLMError) as caught:
                llm.ocr_images_indexed([b'image'])
        self.assertEqual(1, send.call_count)
        self.assertEqual(category, product_ocr._provider_failure(caught.exception))
        self.assertNotIn('secret', str(caught.exception))
        return caught.exception

    def test_invalid_generated_json_preserves_cause_without_budget_retry(self):
        exc = self.assert_failure(response('{"pages": [secret'), 'provider_invalid_json')
        self.assertIsInstance(exc.__cause__, json.JSONDecodeError)

    def test_non_json_http_body_gets_safe_invalid_json_class(self):
        self.assert_failure(httpx.Response(200, text='secret body'), 'provider_invalid_json')

    def test_output_limit_is_distinct_from_incomplete_json(self):
        self.assert_failure(response('{"pages": [secret', 'MAX_TOKENS'), 'provider_output_limit')

    def test_empty_and_blocked_responses_have_distinct_safe_classes(self):
        for result, category in [
            (httpx.Response(200, json={'candidates': []}), 'provider_empty_response'),
            (response(''), 'provider_empty_response'),
            (httpx.Response(200, json={'promptFeedback': {'blockReason': 'secret'}}), 'provider_blocked_response'),
            (response('secret', 'SAFETY'), 'provider_blocked_response'),
        ]:
            with self.subTest(category=category):
                self.assert_failure(result, category)

    def test_malformed_envelopes_are_not_generic_exceptions(self):
        for payload in [[], {'candidates': 'secret'}, {'candidates': [None]},
                        {'candidates': [{'content': 'secret'}]},
                        {'candidates': [{'content': {'parts': ['secret']}}]},
                        {'candidates': [{'content': {'parts': [{'text': {'secret': 1}}]}}]}]:
            with self.subTest(payload_type=type(payload).__name__):
                self.assert_failure(httpx.Response(200, json=payload), 'provider_invalid_response')

    def test_network_and_configuration_errors_have_safe_categories(self):
        for exc in [httpx.ConnectError('secret'), httpx.ReadError('secret'),
                    httpx.RemoteProtocolError('secret'), httpx.ProxyError('secret')]:
            self.assertEqual('provider_network', product_ocr._provider_failure(exc))
        for exc in [httpx.InvalidURL('secret'), httpx.UnsupportedProtocol('secret')]:
            self.assertEqual('provider_configuration', product_ocr._provider_failure(exc))

    def test_budget_exhaustion_and_empty_configuration_are_distinct(self):
        for state, expected in [({'deadline': time.monotonic() + 10, 'requests': 0}, 'request_budget'),
                                ({'deadline': time.monotonic() - 1, 'requests': 1}, 'time_budget')]:
            token = llm._ocr_budget.set(state)
            try:
                with self.assertRaises(llm.LLMError) as caught:
                    llm._request_timeout(180)
                self.assertEqual(expected, product_ocr._provider_failure(caught.exception))
            finally:
                llm._ocr_budget.reset(token)
        with provider(response('{}')) as send, patch.object(config, 'GEMINI_ENDPOINTS', []):
            with self.assertRaises(llm.LLMError) as caught:
                llm.ocr_images_indexed([b'image'])
        self.assertEqual('provider_configuration', product_ocr._provider_failure(caught.exception))
        send.assert_not_called()

    def test_thought_parts_are_not_mixed_into_transcription_json(self):
        result = httpx.Response(200, json={'candidates': [{'content': {'parts': [
            {'thought': True, 'text': 'internal reasoning'},
            {'text': '{"pages":[{"image":1,"text":"actual text"}]}'}]}}]})
        with provider(result) as send:
            self.assertEqual(['actual text'], llm.ocr_images_indexed([b'image']))
        self.assertEqual(1, send.call_count)

    def test_invalid_json_reaches_safe_upload_fallback_note(self):
        with provider(response('{secret')) as send, \
             patch.object(ocr, '_shrink', side_effect=lambda data: data), \
             patch.object(ocr, 'read_images', return_value=ocr.OcrResult(texts=['actual local text'], engine='tesseract')):
            result = main._read_upload_images([b'image'])
        self.assertEqual(1, send.call_count)
        self.assertIn('응답의 형식이 올바르지 않아', ' '.join(result.notes))
        self.assertNotIn('secret', ' '.join(result.notes))

    def test_http_errors_still_stop_without_routing_or_retries(self):
        for code in [400, 401, 403, 404, 429, 500, 503]:
            with provider(httpx.Response(code, json={'error': {'message': 'secret'}})) as send, \
                 patch.object(config, 'GEMINI_ENDPOINTS', ['https://first.example/{model}', 'https://second.example/{model}']):
                with self.assertRaisesRegex(llm.LLMError, f'^OCR provider HTTP {code}$'):
                    llm.ocr_images_indexed([b'image'])
            self.assertEqual(1, send.call_count)

    def test_formal_json_retry_is_unchanged(self):
        with provider(response('{}')), patch('httpx.post', side_effect=[response('{broken'), response('{"ok":true}')]) as send:
            self.assertEqual({'ok': True}, llm.ask_json('formal'))
        self.assertEqual(2, send.call_count)


if __name__ == '__main__':
    unittest.main()
