"""Dedicated, deterministic check after condition retrieval, not an accuracy claim.

Checks source location, feature ownership and a restrictive/prerequisite statement.
An unresolved condition is retained in the audit; it is never removed from scoring.
"""
import re

_GENERIC = {'사용', '제품', '기능', '방법', '안내', '조건', '설명', '정보', '경우', '환경'}


def _norm(text):
    return re.sub(r'\s+', '', text)


def has_condition_signal(text):
    from .condition_type import OPERATING
    from . import link as policy
    from .condition_grounding import _EXTRA, _BOILER
    clean = re.sub(r'주의사항\s*세부정보|주의사항\s*자세히\s*보기', '', text)
    # '면 소재 위주의 옷감' is not a warning. Preserve explicit test conditions
    # via their actual subject, not an accidental substring match on 주의.
    clean = re.sub(r'(?<=[가-힣])주의', '주 의', clean)
    trial = re.search(r'(?:시험|실험|테스트)\s*(?:조건|부하)\s*[:：]', clean)
    return bool(policy._is_cond(clean) or _EXTRA.search(clean) or trial or OPERATING.search(clean)) and not _BOILER.search(text)


def verify_condition(claim, candidate, context, candidates=()):
    from . import link as policy
    quote = candidate['quote']
    reasons = []
    if not has_condition_signal(quote):
        reasons.append('not_a_feature_condition')

    doc = candidate.get('doc_id')
    start = candidate.get('chunk')
    end = candidate.get('end_chunk', start)
    source = {r['chunk']: r['quote'] for r in context if r.get('doc_id') == doc}
    authoritative = []
    if type(start) is int and type(end) is int and end >= start:
        if all(i in source for i in range(start, end + 1)):
            authoritative.append(' '.join(source[i] for i in range(start, end + 1)))
    if not claim.get('_condition_context_complete'):
        # Legacy APIs may provide a partial context; their retrieved candidates
        # are source text, whereas a model-generated condition summary is not.
        authoritative += [r['quote'] for r in candidates
                          if r.get('doc_id') == doc and r.get('chunk') == start]
    if not any(_norm(quote) in _norm(value) for value in authoritative):
        reasons.append('quote_not_at_cited_source_position')

    scope = candidate.get('feature_scope') or candidate.get('_scope', '')
    from .condition_type import condition_type
    if condition_type(quote, scope) == 'test' or (condition_type('', scope) == 'test' and condition_type(quote, scope) != 'operating'):
        reasons.append('test_setting_not_operating_condition')
    from .feature_links import canonical_feature, source_feature_links
    claim_names, links = source_feature_links(claim, context)
    scope_names = policy._feature_names(scope)
    quote_names = policy._feature_names(quote)
    matches = lambda aa, bb: any(canonical_feature(a) == canonical_feature(b) for a in aa for b in bb
        if canonical_feature(a) and canonical_feature(b))
    if claim_names and scope_names and not matches(claim_names, scope_names):
        reasons.append('different_feature_section')
    if claim_names and quote_names and not matches(claim_names, quote_names):
        reasons.append('different_feature_subject')
    # Generic account/network wording under a different explicitly named
    # feature cannot cross a section boundary on word overlap alone.
    if scope and not scope_names and not quote_names:
        if claim_names:
            if not matches(claim_names, [scope]):
                reasons.append('section_owner_not_connected')
        else:
            terms = policy._content_nouns(scope) - _GENERIC
            anchor = claim['text'] + ' ' + ' '.join(e['quote'] for e in claim.get('evidence', []) if not e.get('cond_only'))
            if terms and not terms & (policy._content_nouns(anchor) - _GENERIC):
                reasons.append('section_owner_not_connected')
    return {'accepted': not reasons, 'reasons': reasons or ['source_and_feature_condition_verified'],
            'doc_id': doc, 'chunk': start, 'end_chunk': end,
            'feature_scope': scope, 'source_url': candidate.get('source_url', ''),
            'snapshot_sha256': candidate.get('snapshot_sha256', ''), 'feature_links': links}
