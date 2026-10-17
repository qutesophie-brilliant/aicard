"""Attach conditions to the explained feature, retaining a visible source citation.

This is deterministic post-processing. It does not decide product performance,
invent missing conditions, or treat model-generated summaries as source text.
"""
import re
from bisect import bisect_right

_END = re.compile(r'(?:니다|어요|해요|돼요|세요|있음|없음|가능|불가)[.!?\s]*$|[.!?]$')
_START = re.compile(r'^\s*(?:\*|※|\[|\d{1,2}\))')
_BOILER = re.compile(r'연출|이해를 돕|실제(?:의)?\s*제품과.{0,12}(?:다를|차이)|실제와 다를|색상|구입처|재고|사전 예고')
_EXTRA = re.compile(r'인식되지 않|감지되지 않|등록되어 있어야|연결이 필요|연결이 필요하며|작동이 필요|'
                    r'인식 가능 크기\s*[:：]?\s*\d|(?:인식률|성능)에 차이가 생길|'
                    r'앱.{0,18}(?:촬영하면|직접.{0,8}설정)|리모컨.{0,18}(?:기능키|버튼).{0,30}(?:진단|실행)|'
                    r'(?:지원|적용|감지|작동|동작).{0,12}(?:않습니다|불가|불가능)|'
                    r'되어야만|(?:최소|최대)\s*\d+(?:\.\d+)?\s*(?:시간|분|초).{0,8}(?:사용|유지).{0,4}필요')
_STOP = {'기능', '제품', '사용', '사용자', '가능', '필요', '방법', '조건', '설정', '위해서', '합니다', '있습니다',
         '실제', '경우', '따라', '달라', '설명', '참고', '페이지', '하단', '정보', '모드', '자동', '일부', '시간',
         '감지', '인식', '분석', '제어', '학습', '측정', '동작', '작동', '조절'}


def _pieces(q):
    # A trailing heading belongs to the following text, never a prior limitation.
    return [p.strip() for p in re.split(r'\s+(?=[*※])|(?=\[[^\[\]]{2,160}\])|(?<=\])', q) if p.strip()]


def _segments(lines):
    """Keep section headings in source order, even when split across OCR lines."""
    keys = sorted(lines)
    runs, run = [], []
    for key in keys:
        if run and key != run[-1] + 1:
            runs.append(run)
            run = []
        run.append(key)
    if run:
        runs.append(run)
    for run in runs:
        offsets, parts, size = [], [], 0
        for key in run:
            q = lines[key]['quote'].strip()
            offsets.append(size)
            parts.append(q)
            size += len(q) + 1
        text = ' '.join(parts)
        headings = list(re.finditer(r'\[[^\[\]]{2,160}\]', text))
        cuts = set(offsets + [len(text)])
        for h in headings:
            cuts = {x for x in cuts if not h.start() < x < h.end()}
            cuts.update((h.start(), h.end()))
        cuts = sorted(cuts)
        for start, end in zip(cuts, cuts[1:]):
            segment = text[start:end]
            left = start + len(segment) - len(segment.lstrip())
            right = end - len(segment) + len(segment.rstrip())
            if left >= right:
                continue
            first = run[bisect_right(offsets, left) - 1]
            last = run[bisect_right(offsets, right - 1) - 1]
            for piece in _pieces(text[left:right]):
                yield {**lines[first], 'quote': piece, 'end_chunk': last,
                       '_heading': bool(re.fullmatch(r'\[[^\[\]]{2,160}\]', piece))}


def _units(context):
    """Rejoin at most four adjacent source chunks, never crossing an explicit heading."""
    from . import link as policy
    by_doc = {}
    for c in context:
        by_doc.setdefault(c['doc_id'], {})[c['chunk']] = c
    result = []
    for lines in by_doc.values():
        segments = list(_segments(lines))
        scope, scope_at = '', -100
        for position, c in enumerate(segments):
            i = c['chunk']
            q = c['quote'].strip()
            if c.get('feature_scope'):
                scope, scope_at = c['feature_scope'], i
            if c['_heading']:
                scope, scope_at = q[1:-1].strip(), i
                continue
            elif (re.match(r'^(?:AI|인공지능)\s*', q) and len(q) <= 40
                  and not _END.search(q) and not re.search(r'(?:은|는|을|를|가|로)\s', q)
                  and not policy._CAUSE.search(q) and policy._feature_names(q)):
                scope, scope_at = q, i
            parts = [q]
            end = c['end_chunk']
            for following in segments[position + 1:position + 5]:
                # Model codes can contain a dot inside a still-open parenthesis.
                complete = _END.search(parts[-1]) and ' '.join(parts).count('(') <= ' '.join(parts).count(')')
                if complete or following['_heading'] or following['chunk'] > i + 3 or following['chunk'] > end + 1:
                    break
                nxt = following['quote'].strip()
                next_names = policy._feature_names(nxt) if re.match(r'^(?:AI|인공지능)', nxt) else []
                current_names = policy._feature_names(' '.join(parts) + ' ' + scope)
                different_heading = next_names and current_names and not any(
                    a in b or b in a for a in current_names for b in next_names)
                if _START.match(nxt) or different_heading:
                    break
                parts.append(nxt)
                end = following['end_chunk']
            for p in _pieces(' '.join(parts)):
                result.append({**c, 'quote': p, 'end_chunk': end,
                               '_scope': scope if i - scope_at <= 24 else ''})
    return result


def refine_conditions(claim, cands):
    from . import link as policy
    if claim['basis_state'] != '있음':
        claim['conditions_noted'] = False
        claim['conditions'] = ''
        claim['condition_evidence'] = []
        if claim['basis_state'] == '없음':
            claim['evidence'] = []
        return

    original = claim.get('evidence', [])
    previous_condition = claim.get('conditions_noted', False)
    base = [e for e in original if not e.get('cond_only') and not
            (policy._is_cond(e['quote']) and not policy._has_mechanism('', [e]))]
    # When the claimed function is explicitly named in a selected explanation,
    # do not let additional generic/other-feature hits expand its condition scope.
    named_base = [e for e in base if policy._names_feature(claim['text'], e['quote'])]
    if named_base:
        base = named_base
    src = claim.get('source') or {}
    anchor = ' '.join([claim['text'], src.get('quote', '')] + [e['quote'] for e in base])
    names = set(policy._feature_names(anchor))
    # Prefer a section naming the advertised function itself over a nested feature.
    normalize_name = lambda n: re.sub(r'(?:모드|기능|시스템)$', '', n)
    claim_names = {normalize_name(n) for n in policy._feature_names(claim['text'])}
    nouns = policy._content_nouns(anchor) - _STOP
    context = claim.get('_condition_context') or []
    units = _units(context)
    # Saved model candidates remain valid sources when document context is unavailable.
    for c in [*original, *cands, *(claim.get('_cond_pool') or []), src]:
        if 'quote' in c and c.get('doc_id') is not None and type(c.get('chunk')) is int:
            for p in _pieces(c['quote']):
                units.append({**c, 'quote': p, 'kind': c.get('kind', '판매페이지')})

    eligible = []
    seen = set()
    from .condition_verify import has_condition_signal
    from .feature_links import canonical_feature, source_feature_links
    feature_ids, _ = source_feature_links(claim, context)
    for c in units:
        q = c['quote']
        condition_text = re.sub(r'주의사항\s*세부정보|주의사항\s*자세히\s*보기', '', q)
        if not has_condition_signal(condition_text):
            continue
        # Taking a photo is a prerequisite for photo analysis, not every feature
        # under a broad AI-wind umbrella. Do not turn an optional input into a blanket condition.
        if re.search(r'앱.{0,18}촬영하면', q) and not policy._is_cond(q):
            if not re.search(r'촬영|이미지|공간\s*분석', claim['text']):
                continue
        # A broken suffix (e.g. "AKOR)에 한하여 가능합니다") has no identifiable subject.
        if re.match(r'^[A-Z0-9._-]+\)', q) or len(q) < 12:
            continue
        key = (c['doc_id'], c['chunk'], q)
        if key in seen:
            continue
        seen.add(key)
        qnames = set(policy._feature_names(q))
        scope = c.get('_scope', '')
        scope_names = set(policy._feature_names(scope))
        matches = lambda ns: any(a in b or b in a for a in names for b in ns)
        direct = bool(qnames and matches(qnames))
        inherited = bool(scope_names and matches(scope_names))
        if names and qnames and not direct:
            continue
        overlap = (policy._content_nouns(q) - _STOP) & nouns
        scope_overlap = (policy._content_nouns(scope) - _STOP) & nouns
        # Feature-specific subjects must not inherit another feature's nearby heading.
        subject = re.match(r'^[*※\s]*(.{2,24}?(?:감지|인식|분석|모드|기능|건조|세탁))(?:는|은|가|이)\s', q)
        if subject and not direct:
            terms = policy._content_nouns(subject.group(1)) - _STOP
            if terms and not terms & nouns:
                continue
        # Whole-word source overlap connects e.g. voice-control conditions to Bixby evidence.
        relevant = direct or inherited or len(overlap) >= 2 or (scope_overlap and len(overlap) >= 1)
        if not relevant:
            continue
        distance = min([abs(c['chunk'] - e['chunk']) for e in [*base, src]
                        if e.get('doc_id') == c['doc_id'] and type(e.get('chunk')) is int] or [999])
        score = 8 * direct + 6 * inherited + min(4, len(overlap)) + (2 if distance <= 3 else 0)
        if scope and claim_names:
            section_name = re.sub(r'\s+', '', scope).lower()
            if normalize_name(section_name) in claim_names:
                score += 6
        if c.get('feature_scope') and any(canonical_feature(n) == canonical_feature(scope)
                                         for n in feature_ids):
            score += 8
        eligible.append((score, -distance, c))
    eligible.sort(key=lambda x: (x[0], x[1]), reverse=True)
    from .condition_verify import verify_condition
    checked, selected = [], None
    from .condition_type import condition_type
    claim['test_evidence'] = [{k:c[k] for k in ('doc_id','chunk','end_chunk','quote','kind','source_url') if k in c}
        for _,_,c in eligible if condition_type(c['quote'], c.get('feature_scope') or c.get('_scope','')) == 'test'][:3]
    for _, _, candidate in eligible:
        check = verify_condition(claim, candidate, context, [*cands, src])
        checked.append(check)
        if check['accepted']:
            selected = candidate
            break
    claim['condition_check'] = {
        'method': 'deterministic_source_feature_condition_check',
        'state': 'verified' if selected else 'not_confirmed',
        'candidates_considered': len(eligible), 'checked': checked}
    claim['conditions_noted'] = selected is not None
    claim['status'] = policy.status_from('있음', selected is not None)
    claim['conditions'] = selected['quote'] if selected else ''
    claim['condition_evidence'] = []
    if selected:
        citation = {k: selected[k] for k in ('doc_id', 'chunk', 'quote', 'kind', 'end_chunk',
            'feature_scope', 'source_url', 'snapshot_sha256') if k in selected}
        citation['cond_only'] = True
        citation['condition_type'] = condition_type(selected['quote'], selected.get('feature_scope') or selected.get('_scope',''))
        if citation['condition_type'] == 'unspecified': citation['condition_type'] = 'operating'
        claim['condition_evidence'] = [citation]
        # The old ev[:3] dropped a fourth condition citation while still displaying "조건 확인".
        primary = [e for e in base if (e['doc_id'], e['chunk'], e['quote']) !=
                   (citation['doc_id'], citation['chunk'], citation['quote'])]
        claim['evidence'] = primary[:2] + [citation]
    else:
        claim['evidence'] = base[:3]
    if previous_condition != claim['conditions_noted']:
        # The old model note can assert the opposite condition state; replace it
        # with a bounded description of the actually displayed source result.
        claim['note_before_condition_check'] = claim.get('note', '')
        claim['note'] = ('작동 설명과 함께 이 기능의 사용 조건을 적은 원문을 연결했어요.' if selected else
                         '작동 설명을 연결했어요. 이 기능의 조건으로 연결할 원문은 확인하지 못했어요.')
    if not base:
        claim['needs_review'] = True
        claim['evidence_review_reason'] = 'no_separate_mechanism_citation_after_condition_check'
    for e in claim.get('evidence', []):
        kind = condition_type(e['quote'], e.get('feature_scope',''))
        if kind in ('test', 'test_result_limit'): e['condition_type'] = kind
    if selected and claim['condition_evidence'][0].get('condition_type') == 'test_result_limit':
        claim['note'] = '시험 결과의 해석 범위를 설명하는 원문을 연결했어요. 실제 사용 조건과는 구분해 보세요.'
