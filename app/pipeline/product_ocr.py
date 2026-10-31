"""Indexed consumer OCR with bounded recovery; formal-card OCR keeps its old path."""
import time
import contextvars
import threading
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from . import llm, ocr

MAX_RECOVERIES = 1
TOTAL_OCR_SECONDS = 42
LONG_OCR_SECONDS = 75
INITIAL_OCR_SECONDS = 28
LONG_INITIAL_OCR_SECONDS = 50
BATCH_IMAGES = 4
_LOCAL_OCR_SLOT = threading.BoundedSemaphore(1)
_LOG = logging.getLogger('uvicorn.error')


def _provider_failure(exc):
    """Return only allowlisted diagnostics, never exception response content."""
    if isinstance(exc, TimeoutError) or 'timeout' in type(exc).__name__.lower():
        return 'timeout'
    if isinstance(exc, llm.LLMError):
        match = re.fullmatch(r'OCR provider HTTP (400|401|403|404|429|5\d\d)', str(exc))
        if match:
            code = match.group(1)
            return 'provider_http_' + ('5xx' if code.startswith('5') else code)
    return 'provider_error'


def provider_failure_notes(rows):
    """Safe, shared consumer explanations for URL and uploaded-image results."""
    messages = {
        'timeout': 'AI 문자 인식 응답이 늦어져 일부 이미지의 AI 읽기 결과를 받지 못했습니다.',
        'provider_http_429': 'AI 문자 인식 서비스의 요청량 제한으로 일부 이미지의 AI 읽기 결과를 받지 못했습니다.',
        'provider_http_401': 'AI 문자 인식 서비스 연결을 확인해야 해 일부 이미지의 AI 읽기 결과를 받지 못했습니다.',
        'provider_http_403': 'AI 문자 인식 서비스 연결을 확인해야 해 일부 이미지의 AI 읽기 결과를 받지 못했습니다.',
        'provider_http_400': 'AI 문자 인식 서비스가 이번 요청을 처리하지 못했습니다.',
        'provider_http_404': 'AI 문자 인식 서비스 연결을 확인해야 해 이번 요청을 처리하지 못했습니다.',
        'provider_http_5xx': 'AI 문자 인식 서비스의 일시적인 오류로 일부 이미지의 AI 읽기 결과를 받지 못했습니다.',
        'provider_error': '일부 이미지의 AI 문자 인식 결과를 정상적으로 받지 못했습니다.',
    }
    reasons = set()
    for row in rows:
        reason = row.get('provider_failure')
        if reason in messages:
            reasons.add(reason)
        reasons.update(reason for reason in row.get('provider_failures', []) if reason in messages)
    return list(dict.fromkeys(message for reason, message in messages.items() if reason in reasons))


def read_images(images, allow_llm=True, local_timeout_seconds=None, recover_unconfirmed=False):
    from .image_tiles import split_image, join_texts, MAX_TILES
    groups = [split_image(data) for data in images]
    if len(groups) <= BATCH_IMAGES and all(len(group) == 1 for group in groups):
        return _read_images(images, allow_llm, local_timeout_seconds, recover_unconfirmed)
    payloads = [tile for group in groups for tile in group]
    if len(payloads) > MAX_TILES:
        raise ValueError('한 번에 읽을 상세 이미지가 너무 많습니다. 파일을 나누어 올려 주세요.')
    deadline = time.monotonic() + LONG_OCR_SECONDS
    batches = [payloads[start:start + BATCH_IMAGES]
               for start in range(0, len(payloads), BATCH_IMAGES)]
    def read_batch(batch):
        started = time.monotonic()
        if deadline - time.monotonic() < 1:
            return [{'text': '', 'engine': 'none', 'status': 'timeout', 'attempts': 0} for _ in batch]
        rows = _read_images(batch, allow_llm, local_timeout_seconds or 8,
                           recover_unconfirmed, deadline=deadline,
                           initial_llm_timeout=LONG_INITIAL_OCR_SECONDS)
        _LOG.info('Consumer OCR batch: images=%d read=%d elapsed=%.1fs statuses=%s',
                  len(batch), sum(bool(row['text']) for row in rows),
                  time.monotonic() - started, ','.join(row['status'] for row in rows))
        return rows
    if allow_llm and llm.available() and len(batches) > 1:
        # Network waits overlap so the last part of a long product page gets
        # the same time budget as the first. Results stay in source order.
        # CPU-heavy local fallback is separately limited to one worker.
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(contextvars.copy_context().run, read_batch, batch)
                       for batch in batches]
            readings = [row for future in futures for row in future.result()]
    else:
        readings = [row for batch in batches for row in read_batch(batch)]
    rows, offset = [], 0
    for group in groups:
        parts = readings[offset:offset + len(group)]
        offset += len(group)
        text = join_texts([part['text'] for part in parts])
        unconfirmed = sum(not part['text'].strip() for part in parts)
        engines = {part['engine'] for part in parts if part['text'].strip()}
        rows.append({'text': text, 'engine': next(iter(engines)) if len(engines) == 1 else 'mixed' if engines else 'none',
                     'status': 'partial' if text and unconfirmed else 'read' if text else parts[0]['status'],
                     'attempts': max(part['attempts'] for part in parts),
                     'tile_count': len(parts), 'unconfirmed_tiles': unconfirmed,
                     'recovered': any(part.get('recovered') for part in parts),
                     'fallback_attempted': any(part.get('fallback_attempted') for part in parts),
                     'provider_failures': sorted({part['provider_failure'] for part in parts if part.get('provider_failure')})})
    return rows


def _read_images(images, allow_llm=True, local_timeout_seconds=None, recover_unconfirmed=False,
                 deadline=None, initial_llm_timeout=INITIAL_OCR_SECONDS):
    payloads = [ocr._shrink(data) for data in images]
    rows = [{'text': '', 'engine': 'none', 'status': 'not_read', 'attempts': 0} for _ in payloads]
    if not payloads:
        return rows
    deadline = deadline or time.monotonic() + TOTAL_OCR_SECONDS
    def read_local(payloads):
        texts, engines, notes = [], set(), []
        for payload in payloads:
            remaining = deadline - time.monotonic()
            if remaining < 1:
                texts.append('')
                continue
            if not _LOCAL_OCR_SLOT.acquire(timeout=max(0, remaining)):
                texts.append('')
                continue
            try:
                remaining = deadline - time.monotonic()
                if remaining < 1:
                    texts.append('')
                    continue
                result = ocr.read_images([payload], allow_llm=False,
                                        local_timeout_seconds=min(local_timeout_seconds or 8, remaining))
            finally:
                _LOCAL_OCR_SLOT.release()
            texts.append(result.text.strip())
            notes.extend(result.notes)
            if result.text.strip():
                engines.add(result.engine)
        engine = next(iter(engines)) if len(engines) == 1 else 'mixed' if engines else 'none'
        return ocr.OcrResult(texts=texts, engine=engine, notes=notes)
    if allow_llm and llm.available():
        failure = None
        try:
            texts = llm.ocr_images_indexed(payloads, timeout_seconds=min(initial_llm_timeout, deadline-time.monotonic()))
        except Exception as exc:
            texts = [None] * len(rows)
            reason = _provider_failure(exc)
            failure = 'timeout' if reason == 'timeout' else 'provider_error'
            for row in rows:
                row['provider_failure'] = reason
            _LOG.warning('Consumer OCR provider failed: category=%s type=%s', reason, type(exc).__name__)
        for i, row in enumerate(rows):
            value = texts[i] if i < len(texts) else None
            row.update(attempts=1, engine='llm', status=failure or ('missing_response' if value is None else 'no_text'))
            if isinstance(value, str) and value.strip():
                row.update(text=value.strip(), status='read')
        if failure:
            # Keep the existing server OCR fallback when the AI service fails.
            # Its positional contract is local/deterministic, unlike an LLM's
            # unvalidated pages array. Never use source alt text as a fallback.
            local = read_local(payloads)
            for i, row in enumerate(rows):
                text = local.texts[i].strip() if i < len(local.texts) else ''
                row.update(initial_status=failure, attempts=2, fallback_attempted=True)
                if text:
                    row.update(text=text, engine=local.engine, status='read', recovered=True)
            return rows
        # Retry only one unresolved image, never the successful images or the
        # whole batch. A second empty result does not mean the image has no text.
        recoveries = 0
        for i, row in enumerate(rows):
            if row['text'] or recoveries >= MAX_RECOVERIES:
                continue
            remaining = deadline - time.monotonic()
            if remaining < 1:
                row['retry_skipped'] = 'time_budget'
                continue
            recoveries += 1
            row['initial_status'] = row['status']
            row['attempts'] += 1
            try:
                values = llm.ocr_images_indexed([payloads[i]], timeout_seconds=min(12, remaining))
                value = values[0] if values else None
                if isinstance(value, str) and value.strip():
                    row.update(text=value.strip(), status='read', recovered=True)
                else:
                    row['status'] = 'missing_response' if value is None else 'no_text'
            except Exception as exc:
                row['provider_failure'] = _provider_failure(exc)
                row['status'] = 'timeout' if row['provider_failure'] == 'timeout' else 'provider_error'
        if recover_unconfirmed:
            # Do not regenerate good text or add further paid calls. At most
            # two unresolved images get local recovery within the same budget.
            recovered_locally = 0
            for i, row in enumerate(rows):
                if row['text'] or recovered_locally >= 2 or deadline - time.monotonic() < 1:
                    continue
                recovered_locally += 1
                local = read_local([payloads[i]])
                row.update(fallback_attempted=True, attempts=row['attempts'] + 1)
                if local.text.strip():
                    row.update(text=local.text.strip(), engine=local.engine, status='read', recovered=True)
        return rows
    # Do not silently lose the image-to-source mapping when no AI is configured.
    for i, data in enumerate(payloads):
        if local_timeout_seconds is not None and deadline - time.monotonic() < 1:
            rows[i]['status'] = 'timeout'
            continue
        result = read_local([data])
        text = result.text.strip()
        rows[i].update(text=text, engine=result.engine, status='read' if text else 'no_text', attempts=1)
    return rows
