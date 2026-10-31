"""Indexed consumer OCR with bounded recovery; formal-card OCR keeps its old path."""
import time
from . import llm, ocr

MAX_RECOVERIES = 1
TOTAL_OCR_SECONDS = 42


def read_images(images, allow_llm=True, local_timeout_seconds=None, recover_unconfirmed=False):
    from .image_tiles import split_image, join_texts, MAX_TILES
    groups = [split_image(data) for data in images]
    if len(groups) <= ocr.LLM_BATCH and all(len(group) == 1 for group in groups):
        return _read_images(images, allow_llm, local_timeout_seconds, recover_unconfirmed)
    payloads = [tile for group in groups for tile in group]
    if len(payloads) > MAX_TILES:
        raise ValueError('한 번에 읽을 상세 이미지가 너무 많습니다. 파일을 나누어 올려 주세요.')
    deadline = time.monotonic() + TOTAL_OCR_SECONDS
    readings = []
    for start in range(0, len(payloads), ocr.LLM_BATCH):
        batch = payloads[start:start + ocr.LLM_BATCH]
        if deadline - time.monotonic() < 1:
            readings.extend({'text': '', 'engine': 'none', 'status': 'timeout', 'attempts': 0} for _ in batch)
        else:
            readings.extend(_read_images(batch, allow_llm, local_timeout_seconds or 8,
                                         recover_unconfirmed, deadline=deadline))
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
                     'recovered': any(part.get('recovered') for part in parts)})
    return rows


def _read_images(images, allow_llm=True, local_timeout_seconds=None, recover_unconfirmed=False, deadline=None):
    payloads = [ocr._shrink(data) for data in images]
    rows = [{'text': '', 'engine': 'none', 'status': 'not_read', 'attempts': 0} for _ in payloads]
    if not payloads:
        return rows
    deadline = deadline or time.monotonic() + TOTAL_OCR_SECONDS
    def read_local(payloads):
        if local_timeout_seconds is None:
            return ocr.read_images(payloads, allow_llm=False)
        texts, engines, notes = [], set(), []
        for payload in payloads:
            remaining = deadline - time.monotonic()
            if remaining < 1:
                texts.append('')
                continue
            result = ocr.read_images([payload], allow_llm=False,
                                    local_timeout_seconds=min(local_timeout_seconds, remaining))
            texts.append(result.text.strip())
            notes.extend(result.notes)
            if result.text.strip():
                engines.add(result.engine)
        engine = next(iter(engines)) if len(engines) == 1 else 'mixed' if engines else 'none'
        return ocr.OcrResult(texts=texts, engine=engine, notes=notes)
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
                row['status'] = 'timeout' if 'timeout' in type(exc).__name__.lower() else 'provider_error'
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
