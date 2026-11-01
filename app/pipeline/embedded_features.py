"""Read literal product-feature data; never execute page JavaScript."""
import json
import re

from bs4 import BeautifulSoup


def lenovo_features(soup):
    matches = []
    for script in soup.select('script:not([src])'):
        text = script.string or script.get_text()
        match = re.match(r'^\s*(?:var|let|const)\s+\$pdpAllData\s*=\s*', text)
        if not match or len(text) > 2_000_000:
            continue
        try:
            data, end = json.JSONDecoder().raw_decode(text[match.end():])
        except (ValueError, TypeError):
            continue
        if text[match.end() + end:].strip().strip(';').strip():
            continue
        if isinstance(data, dict):
            features = data.get('productFeatures', {})
            if isinstance(features, dict) and isinstance(features.get('productFeatures'), list):
                matches.append(features['productFeatures'])
    # Multiple payloads can describe different products; don't combine them.
    if len(matches) != 1:
        return ''
    fields = {'headlineStr', 'subHeadlineStr', 'descriptionStr', 'headlines',
              'description', 'optionalSubHeadline', 'optionalTitle', 'optionalDescription'}
    children = {'galleryList', 'contentList', 'blade8ContentRes'}
    parts = []
    def visit(value, depth=0):
        if depth > 8:
            return
        if isinstance(value, list):
            for item in value[:100]:
                visit(item, depth + 1)
        elif isinstance(value, dict):
            for key, child in value.items():
                if key in fields and isinstance(child, str) and len(child) <= 50_000:
                    fragment = BeautifulSoup(child, 'html.parser')
                    for tag in fragment.select('script,style,iframe,img,video,form'):
                        tag.decompose()
                    if fragment.get_text(' ', strip=True):
                        parts.append('<div>' + str(fragment) + '</div>')
                elif key in children:
                    visit(child, depth + 1)
    visit(matches[0])
    return '\n'.join(dict.fromkeys(parts))[:500_000]
