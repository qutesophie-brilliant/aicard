"""Request-wide quick-preview deadline and isolated, source-only checkpoints."""
import asyncio
import contextvars
import copy
import threading
import time
from dataclasses import dataclass, field

SECONDS = 100
PARTIAL_NOTE = '일부 자료의 확인을 완료하지 못해 지금까지 읽은 자료로 먼저 결과를 제공합니다. 읽지 못한 내용은 포함하지 않았습니다.'
_current = contextvars.ContextVar('quick_preview_budget', default=None)


@dataclass
class Budget:
    seconds: float = SECONDS
    end: float = field(init=False)
    stopped: bool = False
    snapshot: dict = field(default_factory=dict)
    lock: object = field(default_factory=threading.Lock)

    def __post_init__(self):
        self.end = time.monotonic() + self.seconds

    def remaining(self):
        return 0 if self.stopped else max(0, self.end - time.monotonic())


def remaining(default):
    state = _current.get()
    return min(default, state.remaining()) if state else default


def active():
    return _current.get() is not None


def checkpoint():
    if remaining(1) <= 0:
        raise TimeoutError('quick preview deadline')


def remember_capture(capture, title=None):
    state = _current.get()
    if state is None or state.stopped:
        return
    with state.lock:
        if state.stopped:
            return
        state.snapshot['capture'] = copy.deepcopy(capture)
        if title:
            state.snapshot['title'] = title


def remember_title(title):
    """Retain a fetched page title even if its detail body cannot be read."""
    state = _current.get()
    if state is not None and title and not state.stopped:
        with state.lock:
            if not state.stopped:
                state.snapshot['title'] = str(title)[:500]


def remember_uploads(texts, notes):
    state = _current.get()
    if state is not None and not state.stopped:
        with state.lock:
            state.snapshot['uploads'] = (list(texts), list(notes))


def partial(state, kind):
    with state.lock:
        saved = copy.deepcopy(state.snapshot)
    if kind == 'uploads':
        texts, notes = saved.get('uploads', ([], []))
        if any(t.strip() for t in texts):
            return texts, notes + [PARTIAL_NOTE]
    elif kind == 'url':
        capture = saved.get('capture', {})
        text = '\n'.join(s['text'] for s in capture.get('sections', []) if s.get('text'))
        if len(text.strip()) >= 20:
            from .pipeline.ingest import Ingested, chunk_text
            capture.setdefault('coverage', {})['complete_all_sources'] = False
            capture['coverage']['time_limited'] = True
            return Ingested(title=saved.get('title', '입력한 제품 자료'), text=text,
                            chunks=chunk_text(text), source_capture=capture, notes=[PARTIAL_NOTE])
    return None


async def call(state, function, *args, fallback=None, **kwargs):
    def work():
        token = _current.set(state)
        try:
            checkpoint()
            return function(*args, **kwargs)
        finally:
            _current.reset(token)
    try:
        return await asyncio.wait_for(asyncio.to_thread(work), timeout=max(.001, state.remaining()))
    except Exception as error:
        # asyncio's timer can fire just before the monotonic deadline rounds
        # to zero. Explicitly stop the worker on timeout as well, so no further
        # network/OCR work starts after a partial response has been returned.
        if isinstance(error, TimeoutError) or state.remaining() <= 0:
            state.stopped = True
        result = partial(state, fallback) if fallback else None
        if result is not None:
            return result
        raise
