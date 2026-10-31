"""Best-effort return of freed OCR heap pages after a complete request.

This does not free live images or increase the memory limit.  It only asks
glibc to return already-unused pages; non-Linux platforms do nothing.
"""
import ctypes
import gc
import sys
from functools import lru_cache


@lru_cache(maxsize=1)
def _get_malloc_trim():
    try:
        trim = ctypes.CDLL(None).malloc_trim
        trim.argtypes = [ctypes.c_size_t]
        trim.restype = ctypes.c_int
        return trim
    except Exception:
        return None


def release_ocr_heap():
    """Run once before releasing the request gate, never per image or batch."""
    if sys.platform != 'linux':
        return
    try:
        gc.collect()
    except Exception:
        pass
    try:
        trim = _get_malloc_trim()
        if trim is not None:
            trim(0)
    except Exception:
        # Cleanup must not replace a successful result or the original error.
        pass
