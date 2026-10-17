"""Feature identity links supported by a source section or an explicit step label."""
import re


def canonical_feature(value):
    value = re.sub(r'\s+', '', value).lower().replace('인공지능', 'ai')
    value = re.sub(r'^ai', '', value)
    return re.sub(r'(?:모드|코스|기능|시스템)$', '', value)


def source_feature_links(claim, context):
    from . import link as policy
    own = policy._feature_names(claim['text'])
    # Quoted feature labels need not include the letters AI (e.g. 외출절전).
    own += [m.group(1) for m in re.finditer(r"[‘'\[]([가-힣A-Za-z ]{2,20})[’'\]]", claim['text'])]
    source = claim.get('source') or {}
    quote = source.get('quote', '')
    norm = lambda s: re.sub(r'\s+', '', s)
    # A claim extracted from 'AI 절약 모드 STEP1. AI 러닝...' is one step
    # of the named parent feature. Mere co-occurrence or a shared sensor is not.
    at_source = any(row.get('doc_id') == source.get('doc_id') and
                    row.get('chunk') == source.get('chunk') and
                    norm(quote) in norm(row.get('quote', '')) for row in context)
    linked, trace = list(own), []
    step = re.search(r'(?i)\bSTEP\s*\d+[.:]?\s*', quote)
    if at_source and step and norm(claim['text']) in norm(quote[step.end():]):
        parent = policy._feature_names(quote[:step.start()])
        linked.extend(n for n in parent if n not in linked)
        trace.append({'relation': 'explicit_parent_step', 'parent_names': parent,
                      'doc_id': source['doc_id'], 'chunk': source['chunk'], 'quote': quote})
    return linked, trace
