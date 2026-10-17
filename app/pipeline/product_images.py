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
    candidates=[]; seen=set()
    for im in body.select('img'):
        src=urljoin(url, im.get('data-src') or im.get('data-original') or im.get('src') or '')
        if not src or src==url or not src.startswith('https://'): continue
        host=urlsplit(src).hostname or ''; source_host=urlsplit(url).hostname or ''
        if host != source_host and host not in ('images.samsung.com','static.lge.co.kr'): continue
        if re.search(r'icon|logo|\.svg|_mo[_.]|-m(?:-|\.)',src,re.I) or src in seen: continue
        seen.add(src)
        score=2 if re.search(r'AI|인공지능|학습|스스로|알아서',im.get('alt',''),re.I) else 0
        candidates.append((score,src))
    candidates.sort(key=lambda x:-x[0])
    def download(src, redirects=0):
        with httpx.stream('GET',src,timeout=15,follow_redirects=False) as r:
            if r.is_redirect:
                target=urljoin(src,r.headers.get('location',''))
                if redirects>=2 or urlsplit(target).hostname not in (urlsplit(url).hostname,'images.samsung.com','static.lge.co.kr') or not target.startswith('https://'):
                    raise ValueError('Unsupported image redirect')
                return download(target,redirects+1)
            r.raise_for_status(); data=bytearray()
            if not r.headers.get('content-type','').startswith('image/'): raise ValueError('Not an image')
            for block in r.iter_bytes():
                data.extend(block)
                if len(data)>8*1024*1024: raise ValueError('Image exceeds 8 MB')
            return bytes(data)
    fetch_bytes=fetch_bytes or download
    audit=[]; payloads=[]; loaded=[]; total_bytes=0
    for _,src in candidates[:limit]:
        row={'url':src,'text':'','engine':'none'}
        audit.append(row)
        try:
            data=fetch_bytes(src)
            import io
            from PIL import Image
            with Image.open(io.BytesIO(data)) as im:
                if im.width * im.height > 16_000_000: raise ValueError('Image exceeds OCR pixel budget')
            total_bytes += len(data)
            if total_bytes > 32*1024*1024: raise ValueError('Images exceed combined byte budget')
            row['image_sha256']=hashlib.sha256(data).hexdigest()
            payloads.append(data);loaded.append(row)
        except Exception as e: row['error']=type(e).__name__+': '+str(e)[:160]
    if payloads:
        if reader is not None:
            results=[reader(data) for data in payloads]
            texts=[r.text for r in results]; engines=[r.engine for r in results]
        else:
            result=ocr.read_images(payloads,allow_llm=config.CHECK_OCR_LLM)
            texts=result.texts; engines=[result.engine]*len(payloads)
        for i,row in enumerate(loaded):
            text=merge_ocr_lines(texts[i] if i<len(texts) else '')
            row.update(text=text,engine=engines[i])
            if text.strip():
                capture['sections'].append({'section_id':len(capture['sections']),'feature_scope':'',
                    'text':text,'chunks':chunk_text(text,ocr=True),'source_url':row['url'],
                    'snapshot_sha256':row['image_sha256'],'source_type':'image_ocr','ocr_engine':row['engine']})
    capture['images']=audit
    capture['coverage'].update(image_candidates=len(candidates),image_attempted=len(audit),
        sections=len(capture['sections']),image_text_read=any(r['text'] for r in audit),image_unread=sum(not r['text'] for r in audit)+max(0,len(candidates)-len(audit)))
    return capture
