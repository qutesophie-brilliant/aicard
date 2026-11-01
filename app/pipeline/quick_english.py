"""Retain explicit English input/action continuations in unsaved previews."""
import re

_INPUT = re.compile(r'\b(?:detect\w*|recogni[sz]\w*|measur\w*|sens(?:e|es|ing))\b', re.I)
_CONTINUATION = re.compile(r'^It\s+(?:can|will|then)\s+', re.I)
_ACTION = re.compile(r'\b(?:adjust\w*|select\w*|avoid\w*|stop\w*|send\w*|mark\w*|control\w*)\b', re.I)
_REFERENCE = re.compile(r'\b(?:these|those|that|them|this)\b', re.I)


def connect(claims, docs):
    documents = {d['id']: d for d in docs}
    for claim in claims:
        if claim.get('evidence') or claim.get('status') == '추가 확인 필요':
            continue
        text = claim['text']
        if not re.search(r'\bAI\b', text) or not _INPUT.search(text):
            continue
        doc = documents.get(claim['source']['doc_id'])
        if not doc:
            continue
        # Stay inside one original paragraph. Do not infer a pronoun's owner
        # across headings, blank lines, images, or other source documents.
        matches = []
        for line in doc['text'].splitlines():
            if text not in line:
                continue
            tail = line.split(text, 1)[1].strip()
            following = re.split(r'(?<=[.!?])\s+', tail, maxsplit=1)[0]
            if (_CONTINUATION.match(following) and _ACTION.search(following)
                    and _REFERENCE.search(following) and len(following) <= 400
                    and not re.search(r'\b(?:not|never|other|another)\b', following, re.I)):
                matches.append(following)
        if len(set(matches)) != 1:
            continue
        quote = matches[0]
        chunk = next((i for i, s in enumerate(doc['chunks']) if quote in s), None)
        if chunk is None:
            continue
        claim['evidence'] = [{'doc_id': doc['id'], 'chunk': chunk,
                              'quote': quote, 'kind': doc['kind'],
                              'english_continuation': True}]
        claim.update(status='근거 확인', basis_state='있음', basis='unknown')
    return claims
