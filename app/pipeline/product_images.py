"""Bounded actual-image OCR. Alt text locates candidates, never supplies claims."""
import hashlib
import re
from urllib.parse import urljoin, urlsplit
from . import product_page, ocr
from .. import config

def capture_images(html, url, capture, fetch_bytes=None, reader=None, limit=6):
    import httpx
    from .ingest import chunk_text, merge_ocr_lines
    body=product_page.product_body(html,url)
    source_host=urlsplit(url).hostname or ''
    allowed_hosts={source_host,'images.samsung.com','static.lge.co.kr'}
    cj_hosts={'image.cjonstyle.net','itemimage.cjonstyle.net'} if source_host in ('display.cjonstyle.com','base.cjonstyle.com') else set()
    allowed_hosts.update(cj_hosts)
    candidates=[]; seen=set()
    for im in body.select('img'):
        src=urljoin(url, product_page.image_source(im))
        parsed=urlsplit(src)
        if parsed.scheme == 'http' and parsed.netloc in cj_hosts:
            src=parsed._replace(scheme='https').geturl()
        if not src or src==url or not src.startswith('https://'): continue
        host=urlsplit(src).hostname or ''
        if host not in allowed_hosts: continue
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
    def download(src):
        from ..network_safety import fetch_public
        return fetch_public(src, timeout=15, max_bytes=8*1024*1024,
                            max_redirects=2, image_only=True, https_only=True,
                            allowed_hosts=allowed_hosts).content
    fetch_bytes=fetch_bytes or download
    audit=[]; payloads=[]; loaded=[]; total_bytes=0
    for index,(_,src) in enumerate(candidates):
        row={'url':src,'text':'','engine':'none','status':'pending','image_number':index+1,
             'download_attempts':0,'ocr_attempts':0}
        audit.append(row)
        if index >= limit:
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
                if im.width * im.height > 25_000_000: raise ValueError('Image exceeds OCR pixel budget')
            total_bytes += len(data)
            if total_bytes > 32*1024*1024: raise ValueError('Images exceed combined byte budget')
            row['image_sha256']=hashlib.sha256(data).hexdigest()
            payloads.append(data);loaded.append(row)
        except Exception as e:
            row['error']=type(e).__name__+': '+str(e)[:160]
            row['status']='download_timeout' if isinstance(e,httpx.TimeoutException) else 'download_failed'
    if payloads:
        if reader is not None:
            results=[reader(data) for data in payloads]
            readings=[{'text':r.text,'engine':r.engine,'status':'read' if r.text.strip() else 'no_text','attempts':1} for r in results]
        else:
            from . import product_ocr
            readings=product_ocr.read_images(payloads,allow_llm=config.CHECK_OCR_LLM)
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
    capture['images']=audit
    capture['coverage'].update(image_candidates=len(candidates),image_attempted=min(len(candidates),limit),
        image_duplicate_variants=len(duplicates),image_skipped_limit=sum(r['status']=='skipped_limit' for r in audit),
        image_download_failed=sum(r['status'] in ('download_failed','download_timeout') for r in audit),
        image_ocr_unconfirmed=sum(r['status'] in ('no_text','missing_response','timeout','provider_error','not_read','partial') for r in audit),
        image_ocr_retried=sum(r['ocr_attempts']>1 for r in audit),
        sections=len(capture['sections']),image_text_read=any(r['text'] for r in audit),image_unread=sum(not r['text'] for r in audit))
    return capture
