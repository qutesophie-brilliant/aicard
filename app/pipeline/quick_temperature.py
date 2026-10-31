"""Quote explicit thermostat operation in unsaved previews, without inferring AI.

The published-card pipeline is unchanged. A control explanation must state both
temperature thresholds, the sensor and the cooling stop/restart behavior in the
same source unit before this fallback adds any related sentence.
"""
import re

_AI = re.compile(r'(?<![A-Za-z])AI(?![A-Za-z])|인공지능', re.I)
_FEATURE = re.compile(r'자동\s*온도|항온|온도\s*(?:조절|제어|관리|케어)|냉각')
_OTHER_PROCESS = re.compile(r'냉동|건조|세탁|가열|온수')
_TEMP = re.compile(r'\d+(?:\.\d+)?\s*(?:°\s*C|℃|도)', re.I)
_RANGE = re.compile(r'\d+(?:\.\d+)?\s*(?:도|°\s*C|℃)?\s*[-~∼–]\s*\d+(?:\.\d+)?\s*(?:도|°\s*C|℃)', re.I)
_TRIAL = re.compile(r'(?:시험|실험|테스트|측정)\s*(?:조건|환경|방법|결과)|실험실|시험실')
_HEADING = re.compile(r'^(?:Point\s*\d|UPGRADE\s*\d|주의\s*사항|제품\s*사양)', re.I)
_FRIDGE_TYPE = re.compile(r'(쌀|음료|와인|김치|화장품)\s*냉장고')


def _compact(text):
    return re.sub(r'\s+', '', text)


def _unit(doc):
    if doc.get('upload_unit') is not None:
        return ('upload', doc['upload_unit'])
    if doc.get('source_type') == 'image_ocr':
        return ('image', doc.get('url') or doc['id'])
    # Plain pasted sales text and fine print are parts of one input. Captured
    # HTML retains its section boundary rather than borrowing across features.
    if not doc.get('source_type'):
        return ('text', doc.get('url', ''))
    return ('section', doc['id'])


def _windows(doc):
    # An OCR sentence may be split between sales text and fine print. Both
    # sections retain the original unit so its adjacent lines can be quoted.
    lines = doc.get('diagram_text', doc['text']).splitlines()
    for start, line in enumerate(lines):
        if not line.strip():
            continue
        if _compact(line) not in _compact(doc['text']):
            continue
        previous = next((p.strip() for p in reversed(lines[:start]) if p.strip()), '')
        if len(previous) < 50 and _TRIAL.search(previous):
            continue
        parts = []
        for end in range(start, min(start + 4, len(lines))):
            value = lines[end].strip()
            if not value or (end > start and (_HEADING.match(value) or _AI.search(value))):
                break
            parts.append(value)
            quote = '\n'.join(parts)
            if len(quote) <= 360:
                yield quote, start


def _control(quote):
    return (not _TRIAL.search(quote) and not _OTHER_PROCESS.search(quote)
            and len({float(re.search(r'\d+(?:\.\d+)?', t).group()) for t in _TEMP.findall(quote)}) == 2
            and re.search(r'센서|항온', quote)
            and re.search(r'중지|정지', quote) and re.search(r'재가동|재작동|다시.{0,8}가동', quote)
            and '냉각' in quote)


def _limit(quote):
    return (not _TRIAL.search(quote) and not _OTHER_PROCESS.search(quote)
            and len(_TEMP.findall(quote)) >= 2
            and re.search(r'(?:실내|주변|외부)\s*온도', quote)
            and re.search(r'냉장|냉각|내부\s*온도', quote)
            and re.search(r'내려가지\s*않을|유지.{0,8}(?:않을|못할)|냉각.{0,8}(?:않을|못할)', quote))


def _evidence(doc, quote, line, condition=False):
    # The line and source document are retained; no reconstructed numbers or
    # explanations are emitted. Prefer the original containing chunk if any.
    chunk = next((i for i, part in enumerate(doc['chunks'])
                  if _compact(quote.splitlines()[0]) in _compact(part)), 0)
    result = {'doc_id': doc['id'], 'chunk': chunk, 'quote': quote,
              'kind': doc['kind'], 'source_line_start': line + 1,
              'temperature_control': True}
    if condition:
        result['condition_type'] = 'operating'
    return result


def _unique_passage(candidates):
    """Collapse repeat/expanded windows of one passage; reject distinct ones."""
    unique = {}
    for candidate in candidates:
        unique.setdefault(_compact(candidate[1]), candidate)
    minimal = [candidate for text, candidate in unique.items()
               if not any(other != text and other in text for other in unique)]
    return minimal[0] if len(minimal) == 1 else None


def _eligible(text):
    if _OTHER_PROCESS.search(text):
        return False
    for marker in _AI.finditer(text):
        following = text[marker.end():].lstrip()
        # English product names such as "AI Care+" do not label every cooling
        # feature in the rest of a merged OCR paragraph as a separate AI claim.
        if not following or re.match(r'[A-Za-z]', following):
            continue
        headline = re.split(r'[\n.!?。;:]', following, maxsplit=1)[0][:32]
        if _FEATURE.search(headline):
            return True
    return False


def _passage_types(passage):
    """Use a passage's own product label, not unrelated comparison sections."""
    if passage is None:
        return set()
    doc, quote, line = passage
    context = []
    lines = doc.get('diagram_text', doc['text']).splitlines()
    # A short product heading immediately above a control also scopes it.
    # Do not reach through a blank line or another feature heading.
    for previous in reversed(lines[max(0, line - 3):line]):
        if not previous.strip() or _HEADING.match(previous.strip()) or _AI.search(previous):
            break
        context.append(previous)
    return set(_FRIDGE_TYPE.findall('\n'.join(context + [quote])))


def connect(claims, docs):
    documents = {doc['id']: doc for doc in docs}
    used = []
    for claim in claims:
        text = claim['text']
        if not _eligible(text):
            continue
        source = documents[claim['source']['doc_id']]
        unit = _unit(source)
        thermal_claims = {_compact(other['text']) for other in claims
                          if _eligible(other['text'])
                          and _unit(documents[other['source']['doc_id']]) == unit}
        if len(thermal_claims) != 1:
            continue
        if any(other['id'] != claim['id'] and _AI.search(other['text'])
               and _OTHER_PROCESS.search(other['text'])
               and _unit(documents[other['source']['doc_id']]) == unit for other in claims):
            continue
        unit_docs = [doc for doc in docs if _unit(doc) == unit]
        candidates = [(doc, quote, line) for doc in unit_docs
                      for quote, line in _windows(doc)]
        controls = [(doc, quote, line) for doc, quote, line in candidates if _control(quote)]
        control = _unique_passage(controls)
        if control is None:
            continue
        # Never pick the globally shortest explanation among different controls.
        doc, quote, line = control
        ranges = [(d, q, n) for d, q, n in candidates
                  if _RANGE.search(q) and '유지' in q and re.search(r'온도|냉장|냉각', q)
                  and not (_TRIAL.search(q) or _OTHER_PROCESS.search(q))]
        range_passage = _unique_passage(ranges)
        limits = [(d, q, n) for d, q, n in candidates if _limit(q)]
        limit_passage = _unique_passage(limits)
        fridge_types = set(_FRIDGE_TYPE.findall(text))
        for passage in (control, range_passage, limit_passage):
            fridge_types.update(_passage_types(passage))
        if len(fridge_types) > 1:
            continue
        additions = [_evidence(doc, quote, line)]
        used.append((unit, _compact(quote), claim['id']))
        if range_passage is not None:
            additions.append(_evidence(*range_passage))
        if limit_passage is not None:
            additions.append(_evidence(*limit_passage, condition=True))
        existing = claim.setdefault('evidence', [])
        for evidence in additions:
            matching = next((e for e in existing if _compact(e['quote']) == _compact(evidence['quote'])), None)
            if matching is not None:
                # A sentence-preserving OCR chunk may already quote this exact
                # passage. Retain the verified scope/provenance without a duplicate.
                matching.update(evidence)
            else:
                existing.append(evidence)
        condition = next((e for e in additions if e.get('condition_type') == 'operating'), None)
        if claim.get('status') != '추가 확인 필요':
            claim['status'] = '조건 확인' if condition or claim.get('conditions_noted') else '근거 확인'
        claim['basis_state'] = '있음'
        if condition:
            claim['conditions_noted'] = True
            claim['conditions'] = condition['quote']
            claim.setdefault('condition_evidence', []).append(condition)
    # A source line used inside a complete control explanation is not a second
    # AI headline merely because it says "스스로".
    return [claim for claim in claims if not any(
        claim['id'] != owner and _unit(documents[claim['source']['doc_id']]) == unit
        and not _AI.search(claim['text']) and _compact(claim['text']) in quote
        for unit, quote, owner in used)]


def include_conditions(card):
    item = card['items']['conditions']
    seen = {_compact(e['quote']) for e in item['evidence']}
    for claim in card['claims']:
        for evidence in claim['evidence']:
            if (evidence.get('temperature_control') and evidence.get('condition_type') == 'operating'
                    and _compact(evidence['quote']) not in seen):
                item['evidence'].append(evidence.copy())
                seen.add(_compact(evidence['quote']))
    if item['evidence']:
        item.update(status='근거 확인', summary='읽어 온 자료에서 관련 문장을 찾았습니다.')
