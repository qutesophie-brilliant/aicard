"""Bounded actual-image OCR. Alt text locates candidates, never supplies claims."""
import hashlib
import re
from urllib.parse import urljoin, urlsplit
from . import product_page, ocr
from .. import config


class _PrefetchImages:
    """At most three in-flight downloads, retaining the request's deadline."""
    def __init__(self, sources, download, deadline):
        from concurrent.futures import ThreadPoolExecutor
        self.pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix='detail-image')
        self.sources = iter(sources)
        self.download = download
        self.deadline = deadline
        self.pending = {}
        self.fill()

    def fill(self):
        import time
        import contextvars
        from .. import check_budget
        while len(self.pending) < 3 and time.monotonic() < self.deadline and check_budget.remaining(15) >= 15:
            src = next(self.sources, None)
            if src is None:
                break
            self.pending[src] = self.pool.submit(contextvars.copy_context().run, self.download, src)

    def read(self, src):
        import contextvars
        future = self.pending.pop(src, None)
        if future is None:
            future = self.pool.submit(contextvars.copy_context().run, self.download, src)
        try:
            return future.result()
        finally:
            self.fill()

    def close(self):
        for future in self.pending.values():
            future.cancel()
        self.pool.shutdown(wait=False, cancel_futures=True)
        self.pending.clear()


def spread_candidates(candidates, limit, *, spread_short_sets=False):
    """Cover long descriptions before spending the budget on adjacent slices.

    Explicit AI-labelled images retain priority. Within each priority group,
    visit both ends and successively split the widest gap. A slow CDN should
    not make the first promotional/installation panels our entire evidence.
    """
    if len(candidates) <= limit and (not spread_short_sets or len(candidates) <= 3):
        return candidates
    ordered = []
    for score in sorted({score for score, _ in candidates}, reverse=True):
        group = [row for row in candidates if row[0] == score]
        picks = [0] if len(group) == 1 else [0, len(group)-1]
        gaps = [(0, len(group)-1)]
        while len(picks) < min(limit, len(group)):
            left, right = max(gaps, key=lambda pair: pair[1]-pair[0])
            gaps.remove((left, right))
            if right-left <= 1:
                break
            middle = (left+right)//2
            picks.append(middle)
            gaps.extend([(left,middle),(middle,right)])
        selected = set(picks)
        ordered.extend(group[i] for i in picks)
        ordered.extend(row for i,row in enumerate(group) if i not in selected)
    return ordered


def capture_images(html, url, capture, fetch_bytes=None, reader=None, limit=24):
    import httpx
    from .ingest import chunk_text, merge_ocr_lines
    body=product_page.product_body(html,url)
    source_host=urlsplit(url).hostname or ''
    cj_hosts={'image.cjonstyle.net','itemimage.cjonstyle.net'} if source_host in ('display.cjonstyle.com','base.cjonstyle.com') else set()
    candidates=[]; seen=set()
    for im in body.select('img'):
        src=urljoin(url, product_page.image_source(im))
        parsed=urlsplit(src)
        if parsed.scheme == 'http' and parsed.netloc in cj_hosts:
            src=parsed._replace(scheme='https').geturl()
        if not src or src==url or urlsplit(src).scheme not in ('http','https'): continue
        if re.search(r'icon|logo|\.svg|_mo[_.]|-m(?:-|\.)',src,re.I) or src in seen: continue
        seen.add(src)
        score=2 if re.search(r'AI|인공지능|학습|스스로|알아서',im.get('alt',''),re.I) else 0
        candidates.append((score,src))
    candidates.sort(key=lambda x:-x[0])
    # LG may place PC_02.jpg and MO_02.jpg in the same desktop product root.
    # Skip a mobile variant only when its exact desktop counterpart exists.
    sources = {src for _,src in candidates}
    duplicates = {src for _,src in candidates if re.search(r'/MO_',src,re.I)
                  and re.sub(r'/MO_', '/PC_', src, flags=re.I) in sources}
    candidates = [(score,src) for score,src in candidates if src not in duplicates]
    # A few very long images can consume the tile budget before the later
    # feature panels. Spread those sources too, not only sets above 24 images.
    candidates = spread_candidates(candidates, limit, spread_short_sets=True)
    def download(src):
        from ..network_safety import fetch_public
        # The product body identifies candidate CDNs. Each fetch and redirect
        # still validates/pins a public IP and checks image type and byte size.
        return fetch_public(src, timeout=10, max_bytes=8*1024*1024,
                            max_redirects=2, image_only=True).content
    use_prefetch = fetch_bytes is None
    fetch_bytes=fetch_bytes or download
    import math
    import time
    from .. import check_budget
    from .image_tiles import MAX_PIXELS, MAX_IMAGE_TILES, TILE_SIDE, OVERLAP, MAX_SHORT_SIDE
    audit=[]; payloads=[]; loaded=[]; total_bytes=0; total_tiles=0; separators=0
    download_deadline = time.monotonic() + 35
    prefetch = _PrefetchImages([src for _,src in candidates[:limit]], download, download_deadline) if use_prefetch else None
    if prefetch:
        fetch_bytes = prefetch.read
    for index,(_,src) in enumerate(candidates):
        row={'url':src,'text':'','engine':'none','status':'pending','image_number':index+1,
             'download_attempts':0,'ocr_attempts':0}
        audit.append(row)
        if index >= limit + min(separators, limit) or time.monotonic() >= download_deadline or check_budget.remaining(15) < 15:
            row['status']='skipped_limit'
            continue
        try:
            for attempt in range(2):
                row['download_attempts'] += 1
                try:
                    data=fetch_bytes(src)
                    break
                except (httpx.TimeoutException,httpx.HTTPStatusError) as exc:
                    if attempt or (isinstance(exc,httpx.HTTPStatusError) and exc.response.status_code < 500):
                        raise
            import io
            from PIL import Image
            with Image.open(io.BytesIO(data)) as im:
                if im.width * im.height > MAX_PIXELS: raise ValueError('Image exceeds OCR pixel budget')
                width, height = im.size
                # Unused detail-editor slots can be one-pixel GIF/JPG strips.
                # Do not spend an OCR slot on them. Replacement downloads stay
                # within twice the normal limit and the existing deadline.
                if min(width, height) < 8:
                    row['status'] = 'no_readable_area'
                    separators += 1
                    continue
                if max(width,height) <= TILE_SIDE + 1 or max(width,height)/min(width,height) < 2:
                    tiles = 1
                else:
                    scale = min(1,MAX_SHORT_SIDE/min(width,height))
                    length = max(round(width*scale),round(height*scale))
                    tiles = 1 + math.ceil(max(0,length-TILE_SIDE)/(TILE_SIDE-OVERLAP))
            # Too much detail must not discard everything already read.
            if total_bytes + len(data) > 32*1024*1024 or total_tiles + tiles > MAX_IMAGE_TILES:
                row['status']='skipped_limit'
                continue
            total_bytes += len(data); total_tiles += tiles
            row['image_sha256']=hashlib.sha256(data).hexdigest()
            payloads.append(data);loaded.append(row)
        except Exception as e:
            row['error']=type(e).__name__+': '+str(e)[:160]
            row['status']='download_timeout' if isinstance(e,httpx.TimeoutException) else 'download_failed'
    if prefetch:
        prefetch.close()
    if payloads:
        if reader is not None:
            results=[reader(data) for data in payloads]
            readings=[{'text':r.text,'engine':r.engine,'status':'read' if r.text.strip() else 'no_text','attempts':1} for r in results]
        else:
            from . import product_ocr
            readings=product_ocr.read_images(payloads,allow_llm=config.CHECK_OCR_LLM,
                                             local_timeout_seconds=8, recover_unconfirmed=True)
        for i,row in enumerate(loaded):
            result=readings[i]
            text=merge_ocr_lines(result['text'])
            row.update(text=text,engine=result['engine'],status=result['status'],ocr_attempts=result['attempts'])
            for key in ('initial_status','retry_skipped','recovered','fallback_attempted','tile_count','unconfirmed_tiles','provider_failure','provider_failures','provider_diagnostics'):
                if key in result: row[key]=result[key]
            if text.strip():
                capture['sections'].append({'section_id':len(capture['sections']),'feature_scope':'',
                    'text':text,'diagram_text':result['text'],'chunks':chunk_text(text,ocr=True),'source_url':row['url'],
                    'snapshot_sha256':row['image_sha256'],'source_type':'image_ocr','ocr_engine':row['engine']})
                check_budget.remember_capture(capture)
    capture['images']=audit
    capture['coverage'].update(image_candidates=len(candidates),image_attempted=sum(r['download_attempts'] > 0 for r in audit),
        image_duplicate_variants=len(duplicates),image_ignored_separator=separators,image_skipped_limit=sum(r['status']=='skipped_limit' for r in audit),
        image_download_failed=sum(r['status'] in ('download_failed','download_timeout') for r in audit),
        image_ocr_unconfirmed=sum(r['status'] in ('no_text','missing_response','timeout','provider_error','not_read','partial') for r in audit),
        image_ocr_retried=sum(r['ocr_attempts']>1 for r in audit),
        sections=len(capture['sections']),image_text_read=any(r['text'] for r in audit),image_unread=sum(not r['text'] for r in audit))
    return capture
