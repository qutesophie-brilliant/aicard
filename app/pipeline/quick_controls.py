"""Link explicit setpoint humidity operation in unsaved previews only."""
import re


def connect(claims, docs):
    documents = {d['id']: d for d in docs}
    for claim in claims:
        text = claim['text']
        if not (re.search(r'AI|인공지능|지능형', text, re.I)
                and re.search(r'습도\s*(?:조절|제어)', text)):
            continue
        related = []
        for evidence in claim.get('evidence', []):
            # Retailer summaries place "+ next feature" immediately after
            # a sensor explanation. Quote only the first feature, not its
            # neighbouring power-saving heading or standalone explanation.
            quote = re.split(r'\s+\+\s+', evidence['quote'], maxsplit=1)[0].strip()
            if not re.search(r'습도|가습', quote):
                continue
            related.append(dict(evidence, quote=quote))
        if claim.get('evidence') and not related and claim.get('basis_state') != '어긋남':
            claim.update(basis_state='없음', status='자료 부족', basis=None,
                         conditions_noted=False, conditions='', condition_evidence=[])
        claim['evidence'] = related
        doc = documents[claim['source']['doc_id']]
        start = claim['source']['chunk']
        chunks = doc['chunks']
        # Stay immediately below this heading and in this source unit. Stop at
        # another AI heading; do not borrow operation from a different feature.
        for pos in range(start + 1, min(start + 4, len(chunks))):
            if re.search(r'AI|인공지능|지능형', chunks[pos], re.I):
                break
            for length in (1, 2):
                parts = chunks[pos:pos + length]
                if len(parts) != length:
                    continue
                if length == 2 and (not re.search(r'(?:목표|설정|원하는)\s*습도', parts[0])
                                    or re.search(r'[.!?。]$', parts[0])):
                    continue
                quote = ' '.join(parts)
                if (len(quote) > 240 or re.search(r'시험|실험|테스트|수동|않|못|불가|예정|AI|지능형', quote, re.I)
                        or not re.search(r'(?:목표|설정|원하는)\s*습도.{0,12}(?:맞춰|따라)', quote)
                        or not re.search(r'가습량.{0,10}자동.{0,10}(?:조절|제어)', quote)):
                    continue
                # The input setpoint and output must belong to the same
                # sentence, not separate neighbouring features.
                if re.search(r'[.!?。].*\S', quote.rstrip('.!?。')):
                    continue
                if claim.get('basis_state') == '어긋남':
                    break
                if not any(re.sub(r'\s+', '', quote) in re.sub(r'\s+', '', e['quote'])
                           for e in claim.get('evidence', [])):
                    claim.setdefault('evidence', []).append({
                        'doc_id': doc['id'], 'chunk': pos, 'quote': quote, 'kind': doc['kind']})
                claim['basis_state'] = '있음'
                claim['status'] = '조건 확인' if claim.get('conditions_noted') else '근거 확인'
                if '센서' in text:
                    claim['basis'] = 'sensor'
                break
    return claims
