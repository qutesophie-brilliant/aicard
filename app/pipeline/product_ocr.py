"""Bounded URL-image OCR, separate from formal-card and upload OCR paths."""
import time
from . import llm, ocr

MAX_RECOVERIES = 1
TOTAL_OCR_SECONDS = 42


def read_images(images, allow_llm=True):
    payloads = [ocr._shrink(data) for data in images]
    rows = [{'text': '', 'engine': 'none', 'status': 'not_read', 'attempts': 0} for _ in payloads]
    if not payloads:
        return rows
    deadline = time.monotonic() + TOTAL_OCR_SECONDS
    if allow_llm and llm.available():
        failure = None
        try:
            texts = llm.ocr_images_indexed(payloads, timeout_seconds=min(28, deadline-time.monotonic()))
        except Exception as exc:
            texts = [None] * len(rows)
            failure = 'timeout' if 'timeout' in type(exc).__name__.lower() else 'provider_error'
        for i, row in enumerate(rows):
            value = texts[i] if i < len(texts) else None
            row.update(attempts=1, engine='llm', status=failure or ('missing_response' if value is None else 'no_text'))
            if isinstance(value, str) and value.strip():
                row.update(text=value.strip(), status='read')
        if failure:
            # Keep the existing server OCR fallback when the AI service fails.
            # Its positional contract is local/deterministic, unlike an LLM's
            # unvalidated pages array. Never use source alt text as a fallback.
            local = ocr.read_images(payloads, allow_llm=False)
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
                row['status'] = 'timeout' if 'timeout' in type(exc).__name__.lower() else 'provider_error'
        return rows
    # Do not silently lose the image-to-source mapping when no AI is configured.
    for i, data in enumerate(payloads):
        result = ocr.read_images([data], allow_llm=False)
        text = result.text.strip()
        rows[i].update(text=text, engine=result.engine, status='read' if text else 'no_text', attempts=1)
    return rows
