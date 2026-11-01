import unittest
from unittest.mock import patch
from app import check_budget
from app.pipeline import llm


class PaymentFailureScopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_payment_error_blocks_only_later_calls_in_same_request(self):
        error = llm.LLMError('OCR provider HTTP 402')
        error.status_code = 402
        error.provider_status = 'RESOURCE_EXHAUSTED'
        budget = check_budget.Budget()
        def read():
            with llm.bounded_call():
                return llm.ask_json('read source')
        with patch.object(llm, 'provider', return_value='gemini'), \
             patch.object(llm, '_gemini_call', side_effect=error) as transport:
            for _ in range(2):
                with self.assertRaises(llm.LLMError) as caught:
                    await check_budget.call(budget, read)
                self.assertEqual(402, caught.exception.status_code)
                self.assertEqual('RESOURCE_EXHAUSTED', caught.exception.provider_status)
            self.assertEqual(1, transport.call_count)
            # A new visitor/request after a top-up must try the provider again.
            transport.side_effect = None
            transport.return_value = '{"text":"real source"}'
            result = await check_budget.call(check_budget.Budget(), read)
            self.assertEqual({'text': 'real source'}, result)
            self.assertEqual(2, transport.call_count)

    async def test_transient_error_is_not_a_payment_block(self):
        error = llm.LLMError('OCR provider HTTP 503')
        error.status_code = 503
        budget = check_budget.Budget()
        def read():
            with llm.bounded_call():
                return llm.ask_json('read source')
        with patch.object(llm, 'provider', return_value='gemini'), \
             patch.object(llm, '_gemini_call', side_effect=[error, '{"text":"recovered"}']) as transport:
            with self.assertRaises(llm.LLMError):
                await check_budget.call(budget, read)
            self.assertEqual({'text': 'recovered'}, await check_budget.call(budget, read))
            self.assertEqual(2, transport.call_count)
