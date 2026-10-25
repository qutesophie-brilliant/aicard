"""Capture product details and their feature boundaries without inventing source text.

Accepts fetched or browser-rendered HTML. Fetching a hidden panel is not the same
as following an unavailable remote panel: missing targets are recorded explicitly.
"""
import hashlib
import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
from . import product_page

_HEADING = re.compile(r'\[([^\[\]\n]{2,160})\]')


def _squash(text):
    return re.sub(r'\s+', '', text)


def capture_product_html(html, url):
    from .ingest import chunk_text
    raw = BeautifulSoup(html, 'html.parser')
    body = product_page.product_body(html, url)
    text = product_page.product_text(body)
    # DOM headings terminate an earlier footnote scope. A following title must
    # never become the owner of a condition appearing before that title.
    headings = {_squash(h.get_text(' ', strip=True)) for h in body.select('h1,h2,h3,h4,h5,h6')}
    headings.discard('')
    sections, owner, pending = [], '', []

    def flush():
        nonlocal pending
        value = '\n'.join(pending).strip()
        if value:
            sections.append({'feature_scope': owner, 'text': value,
                             'chunks': chunk_text(value), 'source_url': url})
        pending = []

    for line in text.splitlines():
        matches = list(_HEADING.finditer(line))
        if not matches:
            if _squash(line) in headings:
                flush()
                owner = line.strip()
            pending.append(line)
            continue
        cursor = 0
        for match in matches:
            pending.append(line[cursor:match.start()])
            flush()
            owner = match.group(1).strip()
            pending.append(match.group(0))
            cursor = match.end()
        pending.append(line[cursor:])
    flush()
    # Responsive layouts may repeat the same paragraph. Deduplicate exact
    # content only; do not drop a second, different set of feature conditions.
    unique, seen = [], set()
    digest = hashlib.sha256(html.encode('utf-8')).hexdigest()
    for section in sections:
        key = (_squash(section['feature_scope']), _squash(section['text']))
        if key not in seen:
            seen.add(key)
            section.update(section_id=len(unique), snapshot_sha256=digest)
            unique.append(section)
    unresolved = []
    # Only controls associated with a known product area are relevant here.
    # The selector removes buttons; inspect raw controls but require a target
    # within the same product root when the target is present.
    roots = raw.select('section.wcms-usp-scope.pc, #compGoodsFeatures, [itemprop="description"], #product-description, .product-description')
    for root in roots:
        for control in root.select('[aria-controls]'):
            for target_id in control.get('aria-controls', '').split():
                if body.find(id=target_id) is None:
                    unresolved.append(target_id)
    pdf_links = []
    host = urlsplit(url).hostname
    for anchor in body.select('a[href]'):
        href = urljoin(url, anchor['href'])
        if urlsplit(href).hostname == host and re.search(r'\.pdf(?:[?#]|$)', href, re.I):
            pdf_links.append(href)
    return {'source_url': url, 'snapshot_sha256': digest, 'text': text,
            'sections': unique, 'coverage': {
                'product_text_chars': len(text), 'sections': len(unique),
                'embedded_detail_panels': len(body.select('.dropContent, details')),
                'unresolved_panel_targets': sorted(set(unresolved)),
                'linked_pdf_candidates': sorted(set(pdf_links)),
                'image_text_read': False, 'scope': 'identified_product_html_only',
                'complete_all_sources': False}}


def section_documents(capture, first_id):
    """Use separate source documents to prevent scope leaking across DOM blocks."""
    return [{'id': first_id + s['section_id'], 'kind': s.get('kind', '판매페이지'),
             'title': s['feature_scope'] or '제품 설명', 'url': s['source_url'],
             'text': s['text'], 'chunks': s['chunks'],
             'feature_scope': s['feature_scope'], 'snapshot_sha256': s['snapshot_sha256'],
             'section_id': s['section_id'], 'source_type': s.get('source_type','html'),
             'upload_unit': s.get('upload_unit'), 'diagram_text': s.get('diagram_text', s['text']),
             'ocr_engine': s.get('ocr_engine','')} for s in capture['sections'] if s['chunks']]
