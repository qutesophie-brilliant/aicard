import json
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import httpx
from app import main
from app.pipeline import image_tiles, llm, ocr, product_ocr


class ParallelOcrTests(unittest.TestCase):
    def test_later_batches_start_before_first_finishes_and_order_is_preserved(self):
        barrier = threading.Barrier(4)
        deadlines = []
        images = [str(i).encode() for i in range(13)]
        def read(batch, *args, **kwargs):
            deadlines.append(kwargs['deadline'])
            barrier.wait(timeout=2)
            time.sleep((13 - int(batch[0])) * .001)
            return [{'text': b.decode(), 'status': 'read', 'engine': 'llm', 'attempts': 1} for b in batch]
        with patch.object(image_tiles, 'split_image', side_effect=lambda b: [b]), \
             patch.object(llm, 'available', return_value=True), \
             patch.object(product_ocr, '_read_images', side_effect=read):
            rows = product_ocr.read_images(images)
        self.assertEqual([str(i) for i in range(13)], [r['text'] for r in rows])
        self.assertEqual(1, len(set(deadlines)))

    def test_local_fallback_is_serialized_across_parallel_batches(self):
        active = peak = 0
        lock = threading.Lock()
        def local(images, **kwargs):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(.015)
            with lock:
                active -= 1
            return ocr.OcrResult(texts=[images[0].decode()], engine='tesseract')
        with patch.object(ocr, '_shrink', side_effect=lambda b:b), \
             patch.object(ocr, 'read_images', side_effect=local):
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = [pool.submit(product_ocr._read_images, [str(i).encode()], False, 8) for i in range(4)]
                rows = [f.result()[0] for f in futures]
        self.assertEqual(1, peak)
        self.assertEqual(['0', '1', '2', '3'], [r['text'] for r in rows])

    def test_provider_failure_is_not_described_as_an_image_without_text(self):
        with patch.object(product_ocr, 'read_images', return_value=[
                {'text': '', 'status': 'timeout', 'engine': 'none', 'attempts': 1}]):
            with self.assertRaisesRegex(ValueError, '처리를 완료하지 못했습니다'):
                main._read_upload_images([b'image'])

    def test_low_thinking_is_scoped_to_consumer_ocr(self):
        bodies = []
        def post(*args, **kwargs):
            bodies.append(json.loads(json.dumps(kwargs['json'])))
            return httpx.Response(200, json={'candidates':[{'content':{'parts':[{'text':'{}'}]}}]})
        with patch.object(llm.config, 'GEMINI_MODELS', ['gemini-3.8-flash']), \
             patch.object(llm.config, 'GEMINI_ENDPOINTS', ['https://example.com/{model}']), \
             patch.object(llm, '_gemini_ok', None), patch.object(llm, '_gemini_dead', set()), \
             patch('httpx.post', side_effect=post):
            token = llm._ocr_budget.set({'deadline': time.monotonic()+5, 'requests': 1})
            try:
                llm._gemini_call('OCR', [], 8000)
            finally:
                llm._ocr_budget.reset(token)
            llm._gemini_call('formal', [], 8000)
        self.assertEqual({'thinkingLevel':'LOW'}, bodies[0]['generationConfig']['thinkingConfig'])
        self.assertNotIn('thinkingConfig', bodies[1]['generationConfig'])


if __name__ == '__main__':
    unittest.main()
