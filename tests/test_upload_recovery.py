import io
import unittest
from unittest.mock import patch

from PIL import Image
from app import main
from app.pipeline import llm, ocr


def png(color):
    stream = io.BytesIO()
    Image.new('RGB', (24, 24), color).save(stream, format='PNG')
    return stream.getvalue()


class UploadRecoveryTests(unittest.TestCase):
    def test_jpeg_upload_is_decoded_and_uses_the_consumer_reader(self):
        stream = io.BytesIO()
        Image.new('RGB', (24, 24), 'white').save(stream, format='JPEG')
        expected = 'AI 세탁은 오염도를 감지해 세탁 시간을 조절합니다.'
        with patch.object(main, '_read_upload_images', return_value=ocr.OcrResult(texts=[expected], engine='test')) as reader:
            texts, _ = main._read_check_uploads([('capture.jpg', 'image/jpeg', stream.getvalue())])
        self.assertEqual([expected], texts)
        reader.assert_called_once()

    def test_connection_failure_retains_later_local_text_and_source_order(self):
        from app.pipeline import product_ocr
        with patch.object(ocr, '_shrink', side_effect=lambda data: data), \
             patch.object(llm, 'available', return_value=True), \
             patch.object(llm, 'ocr_images_indexed', side_effect=RuntimeError('offline')), \
             patch.object(ocr, 'read_images', side_effect=[ocr.OcrResult(texts=[''], engine='none'),
                 ocr.OcrResult(texts=['둘째 이미지의 실제 글자'], engine='tesseract')]):
            rows = product_ocr.read_images([b'first', b'second'], local_timeout_seconds=8, recover_unconfirmed=True)
        self.assertEqual(['', '둘째 이미지의 실제 글자'], [row['text'] for row in rows])
        self.assertEqual('read', rows[1]['status'])

    def test_native_recovery_has_a_bounded_timeout(self):
        from app.pipeline import product_ocr
        with patch.object(ocr, '_shrink', side_effect=lambda data: data), \
             patch.object(llm, 'available', return_value=True), \
             patch.object(llm, 'ocr_images_indexed', side_effect=[[''], ['']]), \
             patch.object(ocr, 'read_images', return_value=ocr.OcrResult(texts=['조건 원문'], engine='tesseract')) as local:
            rows = product_ocr.read_images([b'image'], local_timeout_seconds=8, recover_unconfirmed=True)
        self.assertEqual('조건 원문', rows[0]['text'])
        self.assertLessEqual(local.call_args.kwargs['local_timeout_seconds'], 8)

    def test_scanned_pdf_keeps_page_order_with_consumer_image_reader(self):
        import pymupdf
        document = pymupdf.open()
        document.new_page()
        document.new_page()
        raw = document.tobytes()
        document.close()
        reader = lambda images, allow_llm: ocr.OcrResult(texts=['첫째 쪽', '둘째 쪽'], engine='test')
        result = ocr.read_pdf(raw, allow_llm=False, image_reader=reader)
        self.assertEqual(['첫째 쪽', '둘째 쪽'], result.texts)

    def test_missing_middle_upload_recovers_without_overwriting_good_text(self):
        images = [png('red'), png('green'), png('blue')]
        with patch.object(main.config, 'CHECK_OCR_LLM', True), \
             patch.object(ocr, '_shrink', side_effect=lambda data: data), \
             patch.object(llm, 'available', return_value=True), \
             patch.object(llm, 'ocr_images', return_value=['첫 설명', '', '셋째 설명']), \
             patch.object(llm, 'ocr_images_indexed', side_effect=[['첫 설명', None, '셋째 설명'], [None]]), \
             patch.object(ocr, 'tesseract_available', return_value=True), \
             patch.object(ocr, '_tesseract', return_value='AI 세탁은 6kg 이하에서 동작합니다.') as local:
            texts, notes = main._read_check_uploads([(f'{i}.png', 'image/png', data) for i, data in enumerate(images)])
        self.assertIn('AI 세탁은 6kg 이하에서 동작합니다.', '\n'.join(texts))
        self.assertIn('첫 설명', '\n'.join(texts))
        self.assertIn('셋째 설명', '\n'.join(texts))
        self.assertEqual(1, local.call_count)
        self.assertIn('서버', ' '.join(notes))

    def test_blank_upload_is_unconfirmed_not_reported_as_all_read(self):
        with patch.object(main.config, 'CHECK_OCR_LLM', True), \
             patch.object(ocr, '_shrink', side_effect=lambda data: data), \
             patch.object(llm, 'available', return_value=True), \
             patch.object(llm, 'ocr_images', return_value=['AI가 오염도를 감지해 세탁 시간을 조절합니다.', '']), \
             patch.object(llm, 'ocr_images_indexed', side_effect=[['AI가 오염도를 감지해 세탁 시간을 조절합니다.', ''], ['']]), \
             patch.object(ocr, 'tesseract_available', return_value=True), \
             patch.object(ocr, '_tesseract', return_value=''):
            _, notes = main._read_check_uploads([('a.png', 'image/png', png('red')), ('b.png', 'image/png', png('blue'))])
        self.assertTrue(any('미확인' in note for note in notes), notes)

