import io
import unittest
from unittest.mock import patch

import pymupdf
from PIL import Image, PngImagePlugin
from fastapi.testclient import TestClient

from app import main
from app.pipeline import ocr
from app.upload_safety import UploadSafetyError, check_upload, _pdf_names


LIMIT = 8 * 1024 * 1024


def image_bytes(fmt='PNG', metadata=False):
    stream = io.BytesIO()
    info = PngImagePlugin.PngInfo()
    info.add_text('Comment', 'untrusted metadata')
    Image.new('RGB', (50, 70), 'white').save(stream, format=fmt,
        **({'pnginfo': info} if metadata and fmt == 'PNG' else {}))
    return stream.getvalue()


def pdf_bytes(action=None, attached=False):
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((72, 72), 'AI washing works with loads of 6kg or less.')
        if action:
            doc.xref_set_key(doc.pdf_catalog(), 'OpenAction', action)
        if attached:
            doc.embfile_add('attachment.txt', b'test attachment')
        doc.set_metadata({'author': 'private metadata'})
        return doc.tobytes(garbage=4, deflate=True)


class UploadSafetyTests(unittest.TestCase):
    def check(self, filename, data, ctype='application/octet-stream'):
        return check_upload(filename, ctype, data, max_bytes=LIMIT)

    def test_real_jpeg_and_png_stay_readable_and_preserve_pixels(self):
        for fmt, name in [('JPEG', 'photo.jpg'), ('PNG', 'photo.png')]:
            with self.subTest(fmt=fmt):
                checked = self.check(name, image_bytes(fmt))
                with Image.open(io.BytesIO(checked.data)) as image:
                    self.assertEqual(image.format, 'PNG')
                    self.assertEqual(image.size, (50, 70))
                    self.assertEqual(image.getpixel((0, 0)), (255, 255, 255))

    def test_image_metadata_and_trailing_data_not_forwarded(self):
        data = image_bytes(metadata=True) + b'untrusted trailing data'
        result = self.check('capture.png', data)
        self.assertNotIn(b'untrusted', result.data)
        with Image.open(io.BytesIO(result.data)) as image:
            self.assertFalse(image.info)

    def test_image_extension_mismatch_rejected(self):
        with self.assertRaises(UploadSafetyError):
            self.check('capture.jpg', image_bytes('PNG'))

    def test_disguised_executable_rejected(self):
        with self.assertRaises(UploadSafetyError):
            self.check('capture.png', b'MZ' + b'not an image')

    def test_oversized_decoded_image_rejected_before_decode(self):
        with patch('app.upload_safety.MAX_PIXELS', 100):
            with self.assertRaises(UploadSafetyError):
                self.check('capture.png', image_bytes())

    def test_empty_or_oversized_upload_rejected(self):
        for data in (b'', b'a' * 20):
            with self.subTest(size=len(data)), self.assertRaises(UploadSafetyError):
                check_upload('capture.png', '', data, max_bytes=10)

    def test_plain_pdf_preserves_text_and_removes_metadata_and_trailer(self):
        result = self.check('manual.pdf', pdf_bytes() + b'untrusted trailing data')
        self.assertNotIn(b'untrusted trailing data', result.data)
        with pymupdf.open(stream=result.data, filetype='pdf') as doc:
            self.assertIn('6kg or less', doc[0].get_text())
            self.assertFalse(doc.metadata['author'])

    def test_large_pdf_page_has_bounded_ocr_render(self):
        with pymupdf.open() as doc:
            doc.new_page(width=4000, height=16000)
            raw = doc.tobytes()
        sizes = []
        def reader(images, allow_llm):
            for data in images:
                with Image.open(io.BytesIO(data)) as image:
                    sizes.append(image.size)
            return ocr.OcrResult(texts=['scanned page'], engine='test')
        ocr.read_pdf(raw, allow_llm=False, image_reader=reader)
        self.assertEqual(1, len(sizes))
        self.assertLessEqual(max(sizes[0]), ocr.MAX_SIDE)

    def test_javascript_launch_and_external_actions_rejected(self):
        actions = [
            '<< /S /JavaScript /JS (app.alert\\(1\\)) >>',
            '<< /S /Launch /F (example.exe) >>',
            '<< /S /SubmitForm /F (https://example.test/) >>',
        ]
        for action in actions:
            with self.subTest(action=action), self.assertRaises(UploadSafetyError):
                self.check('manual.pdf', pdf_bytes(action))

    def test_embedded_attachment_rejected(self):
        with self.assertRaises(UploadSafetyError):
            self.check('manual.pdf', pdf_bytes(attached=True))

    def test_name_parser_detects_escaped_names_but_ignores_prose(self):
        names = set(_pdf_names('<< /S /Java#53cript /JS (text /Launch) /Title <2f4a53> >> % /EmbeddedFile'))
        self.assertIn('JavaScript', names)
        self.assertIn('JS', names)
        self.assertNotIn('Launch', names)
        self.assertNotIn('EmbeddedFile', names)

    def test_plain_text_mentions_are_not_treated_as_pdf_actions(self):
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.insert_text((72, 72), '/JavaScript /Launch are words in a printed manual.')
            raw = doc.tobytes()
        result = self.check('manual.pdf', raw)
        with pymupdf.open(stream=result.data, filetype='pdf') as doc:
            self.assertIn('/JavaScript /Launch', doc[0].get_text())

    def test_ordinary_pdf_source_links_are_preserved(self):
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.insert_link({'kind': pymupdf.LINK_URI, 'from': pymupdf.Rect(1, 1, 100, 20),
                              'uri': 'https://example.test/source'})
            raw = doc.tobytes()
        checked = self.check('manual.pdf', raw)
        with pymupdf.open(stream=checked.data, filetype='pdf') as doc:
            self.assertEqual(doc[0].get_links()[0]['uri'], 'https://example.test/source')

    def test_entire_batch_checked_before_external_ocr(self):
        with patch.object(main, '_read_upload_images') as reader:
            with self.assertRaises(UploadSafetyError):
                main._read_check_uploads([('good.png', 'image/png', image_bytes()),
                    ('blocked.pdf', 'application/pdf', pdf_bytes('<< /S /Launch /F (x.exe) >>'))])
            reader.assert_not_called()

    def test_public_route_rejects_before_ocr_and_analysis(self):
        with patch.object(main, '_rate_ok', return_value=True), \
             patch.object(main, '_read_upload_images') as reader, \
             patch.object(main, 'quick_check') as analyze:
            response = TestClient(main.app).post('/check', files={
                'files': ('blocked.pdf', pdf_bytes('<< /S /JavaScript /JS (1) >>'), 'application/pdf')})
        self.assertEqual(response.status_code, 200)
        self.assertIn('스크립트·실행 동작', response.text)
        reader.assert_not_called()
        analyze.assert_not_called()


if __name__ == '__main__':
    unittest.main()
