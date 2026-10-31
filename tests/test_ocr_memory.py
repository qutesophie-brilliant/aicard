import ctypes
import unittest
from unittest.mock import Mock, patch

from app import main, ocr_memory


class OcrHeapCleanupTests(unittest.TestCase):
    def tearDown(self):
        ocr_memory._get_malloc_trim.cache_clear()

    def test_linux_collects_once_then_trims_without_interpreting_return_value(self):
        for result in (0, 1):
            events = []
            with patch.object(ocr_memory.sys, 'platform', 'linux'), \
                 patch.object(ocr_memory.gc, 'collect', side_effect=lambda: events.append('collect')) as collect, \
                 patch.object(ocr_memory, '_get_malloc_trim', return_value=lambda pad: events.append(('trim', pad)) or result):
                self.assertIsNone(ocr_memory.release_ocr_heap())
            collect.assert_called_once_with()
            self.assertEqual(['collect', ('trim', 0)], events)

    def test_non_linux_does_nothing(self):
        with patch.object(ocr_memory.sys, 'platform', 'win32'), \
             patch.object(ocr_memory.gc, 'collect') as collect, \
             patch.object(ocr_memory, '_get_malloc_trim') as resolve:
            ocr_memory.release_ocr_heap()
        collect.assert_not_called()
        resolve.assert_not_called()

    def test_trim_signature_and_cached_lookup(self):
        library = Mock()
        with patch.object(ocr_memory.ctypes, 'CDLL', return_value=library) as load:
            trim = ocr_memory._get_malloc_trim()
            self.assertIs(trim, ocr_memory._get_malloc_trim())
        load.assert_called_once_with(None)
        self.assertEqual([ctypes.c_size_t], trim.argtypes)
        self.assertIs(ctypes.c_int, trim.restype)

    def test_absent_libc_or_symbol_is_silently_skipped(self):
        for library, error in [(None, OSError('unavailable')), (object(), None)]:
            ocr_memory._get_malloc_trim.cache_clear()
            with patch.object(ocr_memory.ctypes, 'CDLL', return_value=library, side_effect=error):
                self.assertIsNone(ocr_memory._get_malloc_trim())

    def test_collection_and_trim_errors_cannot_escape(self):
        for resolve in (Mock(return_value=None), Mock(return_value=Mock(side_effect=RuntimeError('trim failed'))), Mock(side_effect=RuntimeError('lookup failed'))):
            with patch.object(ocr_memory.sys, 'platform', 'linux'), \
                 patch.object(ocr_memory.gc, 'collect', side_effect=RuntimeError('gc failed')), \
                 patch.object(ocr_memory, '_get_malloc_trim', resolve):
                self.assertIsNone(ocr_memory.release_ocr_heap())

    def test_url_cleanup_runs_once_before_gate_release_on_success_and_error(self):
        for error in (None, ValueError('original error')):
            events = []
            gate = Mock()
            gate.acquire.return_value = True
            gate.release.side_effect = lambda: events.append('release')
            expected = object()
            with patch.object(main, '_check_ocr_gate', gate), \
                 patch.object(main, 'release_ocr_heap', side_effect=lambda: events.append('cleanup')) as cleanup, \
                 patch.object(main.ingest, 'from_url', return_value=expected, side_effect=error):
                if error:
                    with self.assertRaises(ValueError) as caught:
                        main._read_check_url('https://example.com/product')
                    self.assertIs(error, caught.exception)
                else:
                    self.assertIs(expected, main._read_check_url('https://example.com/product'))
            cleanup.assert_called_once_with()
            gate.release.assert_called_once_with()
            self.assertEqual(['cleanup', 'release'], events)

    def test_upload_cleanup_runs_once_even_on_validation_error(self):
        for error in (None, ValueError('original validation error')):
            events = []
            gate = Mock()
            gate.acquire.return_value = True
            gate.release.side_effect = lambda: events.append('release')
            with patch.object(main, '_check_ocr_gate', gate), \
                 patch.object(main, 'release_ocr_heap', side_effect=lambda: events.append('cleanup')) as cleanup, \
                 patch('app.upload_safety.check_image_batch', side_effect=error):
                if error:
                    with self.assertRaises(ValueError) as caught:
                        main._read_check_uploads([])
                    self.assertIs(error, caught.exception)
                else:
                    self.assertEqual(([], []), main._read_check_uploads([]))
            cleanup.assert_called_once_with()
            self.assertEqual(['cleanup', 'release'], events)

    def test_broken_cleanup_cannot_change_result_or_original_error(self):
        for error in (None, ValueError('original error')):
            gate = Mock()
            gate.acquire.return_value = True
            expected = object()
            with patch.object(main, '_check_ocr_gate', gate), \
                 patch.object(main, 'release_ocr_heap', side_effect=RuntimeError('cleanup failure')), \
                 patch.object(main.ingest, 'from_url', return_value=expected, side_effect=error):
                if error:
                    with self.assertRaises(ValueError) as caught:
                        main._read_check_url('https://example.com/product')
                    self.assertIs(error, caught.exception)
                else:
                    self.assertIs(expected, main._read_check_url('https://example.com/product'))
            gate.release.assert_called_once_with()

    def test_busy_request_does_not_collect_or_release_another_request_gate(self):
        for operation, argument in [(main._read_check_url, 'https://example.com/product'), (main._read_check_uploads, [])]:
            gate = Mock()
            gate.acquire.return_value = False
            with patch.object(main, '_check_ocr_gate', gate), \
                 patch.object(main, 'release_ocr_heap') as cleanup:
                with self.assertRaises(ValueError):
                    operation(argument)
            cleanup.assert_not_called()
            gate.release.assert_not_called()


if __name__ == '__main__':
    unittest.main()
