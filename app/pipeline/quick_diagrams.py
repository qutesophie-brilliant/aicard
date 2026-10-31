"""Source-only links for short uploaded diagrams. No generated explanations."""
import hashlib
import re

from . import quick_text

_AI = re.compile(r'(?<![A-Za-z])AI(?![A-Za-z])|인공지능', re.I)
_TITLE = re.compile(r'(?:코스|모드|기능|제어|세탁|청소|인식|운전)\s*[:：]?\s*$')
_BOUNDARY = re.compile(r'^[*※†①-⑳]|^\d{1,2}[).]|(?:코스|모드|기능|페어링|컨트롤|디자인)\s*$')
_INPUT = re.compile(r'세탁물|의류|옷감|오염|무게|재질|물체|사물|얼굴|음성|장애물|온도|습도|실내|바닥|먼지')
_OUTPUT = re.compile(r'세탁\s*시간|헹굼|횟수|모션|수량|전력|흡입력|풍량|운전|온도|속도|경로|코스')
_ACTION = re.compile(r'조절|조합|선택|변경|분석|제어|감지|구분|추천|탐지|인식')
_EXPLANATION_END = re.compile(r'(?:조절|선택|변경|추천|인식|감지|제어)(?:합니다|해요|한다|함|됩니다)?[.!?。]?\s*$')
_PROCESS_PAIRS = (('세탁','건조'), ('건조','세탁'), ('냉장','냉동'), ('냉동','냉장'))


def _compact(text):
    return re.sub(r'\s+', '', text or '')


def _is_title(text):
    return len(text) <= 60 and bool(_AI.search(text) and _TITLE.search(text))


def capture_uploads(texts, url):
    """Retain each OCR image/PDF page as a distinct source unit."""
    from .run import split_fine_print
    sections = []
    for unit, text in enumerate(texts):
        if not text.strip():
            continue
        sales, fine = split_fine_print(text)
        for kind, value in (('판매페이지', sales), ('사양표', fine)):
            if not value.strip():
                continue
            sections.append({'section_id': len(sections), 'upload_unit': unit,
                'kind': kind, 'feature_scope': '', 'source_type': 'upload_ocr', 'source_url': url,
                'text': value, 'diagram_text': text, 'chunks': quick_text.chunks(value),
                'snapshot_sha256': hashlib.sha256(value.encode()).hexdigest()})
    return {'source_url': url, 'sections': sections, 'images': [],
            'coverage': {'scope': 'uploaded_documents', 'complete_all_sources': False,
                         'uploaded_text_units': len(texts)}}


def link_within_units(claims, docs):
    """Do not borrow an explanation from another image or PDF page."""
    from .link import link_claims
    from .retrieve import ChunkIndex
    unit_by_doc = {d['id']: d.get('upload_unit') for d in docs}
    for unit in dict.fromkeys(unit_by_doc.values()):
        subset = [c for c in claims if unit_by_doc.get(c['source']['doc_id']) == unit and not _is_title(c['text'])]
        if subset:
            link_claims(subset, ChunkIndex([d for d in docs if unit_by_doc[d['id']] == unit]), exclude_testimonials=True)
    for claim in claims:
        if _is_title(claim['text']):
            claim.update(evidence=[], status='자료 부족', basis_state='없음', conditions_noted=False, conditions='')
    return claims


def connect(claims, docs):
    """Quote adjacent source lines only when they state input, action and output."""
    documents = {d['id']: d for d in docs if d.get('source_type') == 'upload_ocr'}
    used = []
    for claim in claims:
        source = claim['source']
        doc = documents.get(source['doc_id'])
        if not doc:
            continue
        original = doc.get('diagram_text', doc['text'])
        lines = original.splitlines()
        for pos, raw in enumerate(lines):
            title = raw.strip()
            if not _is_title(title):
                continue
            if _compact(claim['text']) != _compact(title):
                continue
            following = []
            for line in lines[pos+1:pos+7]:
                text = line.strip()
                if not text or _AI.search(text) or _BOUNDARY.search(text):
                    break
                following.append(text)
                current = '\n'.join(following)
                if _EXPLANATION_END.search(text) and _INPUT.search(current) and _OUTPUT.search(current) and _ACTION.search(current):
                    break
            quote = '\n'.join(following)
            if not quote or len(quote) > 320 or not (_INPUT.search(quote) and _ACTION.search(quote) and _OUTPUT.search(quote)):
                continue
            process_text = re.sub(r'세탁물|냉장고', '', quote)
            if any(own in title and other in process_text and own not in process_text for own, other in _PROCESS_PAIRS):
                continue
            # Whitespace may differ after OCR, but words/numbers may not.
            if _compact(quote) not in _compact(original):
                continue
            evidence = {'doc_id': doc['id'], 'chunk': source['chunk'], 'quote': quote,
                        'kind': '올린 자료(도식 설명)', 'source_line_start': pos+2,
                        'source_line_end': pos+1+len(following), 'diagram': True}
            if not any(_compact(quote) in _compact(e['quote']) for e in claim.get('evidence', [])):
                claim.setdefault('evidence', []).append(evidence)
            claim.update(status='근거 확인', basis_state='있음')
            used.append((doc['id'], _compact(quote), claim['id']))
            break
    # An explanation fragment is not a second AI headline.
    return [c for c in claims if not any(c['id'] != owner and c['source']['doc_id'] == did
            and not _AI.search(c['text']) and _compact(c['text']) in quote
            for did, quote, owner in used)]
